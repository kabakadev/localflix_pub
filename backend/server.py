from flask import Flask, jsonify, request, send_file, Response
from flask_cors import CORS
import os, re, subprocess, tempfile, sqlite3, json, shutil, platform
import threading, time, queue
from contextlib import contextmanager

app  = Flask(__name__)
CORS(app)

MEDIA_EXTS  = (".mp4", ".mkv", ".avi", ".webm", ".m4v", ".mov")
DB_PATH     = "media_cache.db"
CONFIG_PATH = "config.json"

# Persistent thumbnail cache — survives OS reboots unlike /tmp.
# ~/.localflix/thumbs/ works on Windows (%USERPROFILE%), Mac, and Linux.
THUMB_DIR = os.path.join(os.path.expanduser("~"), ".localflix", "thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)

TRASH_DIR = os.path.join(os.path.expanduser("~"), ".localflix", "trash")
os.makedirs(TRASH_DIR, exist_ok=True)

# ── SQLite connection helper ──────────────────────────────────────────────────
_db_write_lock = threading.Lock()

@contextmanager
def _db(write=False):
    lock = _db_write_lock if write else None
    if lock:
        lock.acquire()
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()
        if lock:
            lock.release()

# ── Cached config ─────────────────────────────────────────────────────────────
_config_cache: dict = {}
_config_lock  = threading.Lock()

# ── Schema version ───────────────────────────────────────────────────────────
SCHEMA_VERSION = 7

# ── Scan state ───────────────────────────────────────────────────────────────
_scan_lock     = threading.Lock()
_scan_complete = threading.Event()
_scan_complete.clear()

_scan_state = {
    "running":   False,
    "total":     0,
    "done":      0,
    "new_files": 0,
    "removed":   0,
    "phase":     "idle",
}
_scan_state_lock = threading.Lock()

# ── SSE subscribers ──────────────────────────────────────────────────────────
_sse_subscribers: list = []
_sse_lock = threading.Lock()

def notify_subscribers(event: str, data: dict):
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)


# ─────────────────────────────────────────────────────────────────────────────
# DURATION ENRICHMENT QUEUE
# ─────────────────────────────────────────────────────────────────────────────
_duration_queue: queue.Queue = queue.Queue()
_duration_worker_started = False
_duration_worker_lock    = threading.Lock()
_DURATION_WORKERS = 8

def _ensure_duration_worker():
    global _duration_worker_started
    with _duration_worker_lock:
        if not _duration_worker_started:
            for i in range(_DURATION_WORKERS):
                t = threading.Thread(
                    target=_duration_worker,
                    name=f"duration-worker-{i}",
                    daemon=True,
                )
                t.start()
            _duration_worker_started = True

def _duration_worker():
    while True:
        try:
            filepath = _duration_queue.get(timeout=30)
        except queue.Empty:
            continue

        with _db() as conn:
            row = conn.execute(
                "SELECT duration_sec, duration_queued FROM media WHERE path = ?",
                (filepath,)
            ).fetchone()
        if not row or (row[0] and row[0] > 0):
            _duration_queue.task_done()
            continue

        duration_sec = get_video_duration(filepath)
        if duration_sec > 0:
            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            duration_str = f"{mins}:{secs:02d}"
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET duration_sec=?, duration=?, duration_queued=0 WHERE path=?",
                    (duration_sec, duration_str, filepath)
                )
            with _db() as conn:
                row = conn.execute(
                    "SELECT name, size_mb FROM media WHERE path=?", (filepath,)
                ).fetchone()
            if row:
                filename, size_mb = row
                media_type, confidence, signals = smart_classify(
                    filename, duration_sec, size_mb or 0
                )
                with _db(write=True) as conn:
                    conn.execute(
                        "UPDATE media SET type=?, confidence=?, signals_found=? WHERE path=?",
                        (media_type, confidence, json.dumps(signals), filepath)
                    )
            notify_subscribers("media_enriched", {
                "path":         filepath,
                "duration_sec": duration_sec,
                "duration":     duration_str,
                "type":         media_type,
            })

        _duration_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL WORKER POOL
# ─────────────────────────────────────────────────────────────────────────────
_thumb_queue: queue.Queue = queue.Queue()
_thumb_worker_started     = False
_thumb_worker_lock        = threading.Lock()
_THUMB_WORKERS = 4


def _thumb_path_for(filepath: str, hero: bool = False) -> str:
    import hashlib
    stable = hashlib.md5(filepath.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = "_hero" if hero else ""
    return os.path.join(THUMB_DIR, f"lflix_{stable}{suffix}.jpg")


def _generate_thumb(filepath: str, out_path: str, width: int) -> bool:
    for ts in ["00:05:00", "00:01:00", "00:00:10", "00:00:03"]:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", ts, "-i", filepath,
                 "-vframes", "1", "-q:v", "3",
                 "-vf", f"scale={width}:-1",
                 "-update", "1",
                 out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15
            )
        except subprocess.TimeoutExpired:
            continue
        if os.path.exists(out_path):
            return True
    return False


def _ensure_thumb_worker():
    global _thumb_worker_started
    with _thumb_worker_lock:
        if not _thumb_worker_started:
            for i in range(_THUMB_WORKERS):
                t = threading.Thread(
                    target=_thumb_worker,
                    name=f"thumb-worker-{i}",
                    daemon=True,
                )
                t.start()
            _thumb_worker_started = True


def _thumb_worker():
    while True:
        try:
            filepath = _thumb_queue.get(timeout=30)
        except queue.Empty:
            continue

        card_path = _thumb_path_for(filepath, hero=False)
        hero_path = _thumb_path_for(filepath, hero=True)

        card_exists = os.path.exists(card_path)
        hero_exists = os.path.exists(hero_path)

        if card_exists and hero_exists:
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET thumb_path=?, thumb_queued=0 WHERE path=?",
                    (card_path, filepath)
                )
            _thumb_queue.task_done()
            continue

        if not card_exists:
            card_exists = _generate_thumb(filepath, card_path, width=640)

        if not hero_exists:
            hero_exists = _generate_thumb(filepath, hero_path, width=1280)

        if card_exists:
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET thumb_path=?, thumb_queued=0 WHERE path=?",
                    (card_path, filepath)
                )
        else:
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET thumb_queued=0 WHERE path=?", (filepath,)
                )

        _thumb_queue.task_done()


def _migrate_tmp_thumbnails():
    tmp_dir = tempfile.gettempdir()
    migrated = 0
    try:
        for fname in os.listdir(tmp_dir):
            if not (fname.startswith("lflix_") and fname.endswith(".jpg")):
                continue
            src  = os.path.join(tmp_dir, fname)
            dest = os.path.join(THUMB_DIR, fname)
            if os.path.exists(dest):
                continue
            try:
                shutil.copy2(src, dest)
                migrated += 1
            except Exception:
                pass
    except Exception:
        pass

    if migrated:
        print(f"🖼  Migrated {migrated} thumbnails from /tmp → {THUMB_DIR}")

    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT path FROM media WHERE thumb_path = '' OR thumb_path IS NULL"
            ).fetchall()
        updated = 0
        for (filepath,) in rows:
            candidate = _thumb_path_for(filepath)
            if os.path.exists(candidate):
                with _db(write=True) as conn:
                    conn.execute(
                        "UPDATE media SET thumb_path=?, thumb_queued=0 WHERE path=?",
                        (candidate, filepath)
                    )
                updated += 1
        if updated:
            print(f"🖼  Linked {updated} existing thumbnails to DB")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# MPV IPC POSITION TRACKER
# ─────────────────────────────────────────────────────────────────────────────
def _mpv_socket_path():
    if platform.system() == "Windows":
        return r"\\.\pipe\lflix_mpv"
    return os.path.join(tempfile.gettempdir(), "lflix_mpv.sock")

_now_playing: dict = {}
_now_playing_lock = threading.Lock()
# Monotonically-increasing session counter. Each call to play_media() increments
# this and stamps the new _now_playing dict. Every tracker thread captures its
# own session_id at launch and breaks out of its loop the moment the global
# counter advances — guaranteeing only ONE tracker is ever active at a time.
_session_counter = 0

def _mpv_send(sock, command: list):
    msg = json.dumps({"command": command}) + "\n"
    try:
        sock.sendall(msg.encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n")[0]
        return json.loads(line).get("data")
    except Exception:
        return None

def _open_mpv_socket():
    import socket
    sock_path = _mpv_socket_path()
    try:
        if platform.system() == "Windows":
            return None
        else:
            import socket as _socket
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(sock_path)
            return s
    except Exception:
        return None

def _mpv_tracker(my_session_id: int):
    """
    Tracks playback position via mpv IPC socket.

    my_session_id: captured from _session_counter at launch time.
    The tracker breaks immediately when _session_counter advances past
    my_session_id — this happens the moment the user plays a new video.
    Only one tracker is ever active at a time.

    Writes to DB and fires SSE only when position advances by >= 2 seconds,
    eliminating the constant 5-second noise that caused progress bars on
    unrelated items to flicker.
    """
    consecutive_failures = 0
    sock                 = None
    confirmed_playing    = False
    last_real_position   = 0.0
    last_written_position = -1.0   # position at last DB write; -1 = never written

    while True:
        time.sleep(5)

        # ── Session ownership check ───────────────────────────────────────────
        # If _session_counter has moved past my_session_id, a new video has
        # started. Exit immediately — do NOT write anything; the new tracker
        # owns _now_playing and will handle the new video's session.
        with _now_playing_lock:
            if _session_counter != my_session_id:
                break
            if not _now_playing:
                break
            filepath     = _now_playing["path"]
            duration_sec = _now_playing["duration_sec"]

        if sock is None:
            sock = _open_mpv_socket()
            if sock is None:
                consecutive_failures += 1
                if consecutive_failures > 6:
                    _finish_session(filepath, duration_sec, last_real_position)
                    with _now_playing_lock:
                        if _session_counter == my_session_id:
                            _now_playing.clear()
                    break
                continue
            consecutive_failures = 0

        try:
            position_sec = _mpv_send(sock, ["get_property", "time-pos"])
            eof_reached  = _mpv_send(sock, ["get_property", "eof-reached"])

            if position_sec is None:
                consecutive_failures += 1
                if consecutive_failures > 3:
                    sock.close()
                    sock = None
                continue

            consecutive_failures = 0
            position_sec = float(position_sec)

            if position_sec > 0:
                confirmed_playing  = True
                last_real_position = position_sec

            if not confirmed_playing:
                continue

            # Refresh duration if it wasn't known at play time
            if duration_sec == 0:
                with _db() as conn:
                    fresh = conn.execute(
                        "SELECT duration_sec FROM media WHERE path = ?", (filepath,)
                    ).fetchone()
                if fresh and fresh[0] and fresh[0] > 0:
                    duration_sec = fresh[0]
                    with _now_playing_lock:
                        if _now_playing.get("path") == filepath:
                            _now_playing["duration_sec"] = duration_sec

            progress_pct = round(position_sec / duration_sec * 100, 1) if duration_sec > 0 else 0

            real_eof = (
                eof_reached is True
                and confirmed_playing
                and progress_pct >= 80
            )
            completed = 1 if (real_eof or progress_pct >= 90) else 0

            # ── Write only when position has meaningfully advanced ────────────
            # Writing + firing SSE on every 5-second poll causes:
            #   - Constant unnecessary DB writes
            #   - SSE noise to all connected browsers → flickering progress bars
            #     on items the user isn't watching
            # Only write when position has moved >= 2 seconds from last write,
            # OR when the video just completed.
            position_moved = abs(position_sec - last_written_position) >= 2.0
            if position_moved or completed:
                with _db(write=True) as conn:
                    conn.execute("""
                        INSERT INTO watch_history
                            (path, position_sec, duration_sec, progress_pct, completed,
                             last_watched, watch_count)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(path) DO UPDATE SET
                            position_sec = excluded.position_sec,
                            duration_sec = excluded.duration_sec,
                            progress_pct = excluded.progress_pct,
                            completed    = excluded.completed,
                            last_watched = excluded.last_watched,
                            watch_count  = watch_count + 1
                    """, (filepath, position_sec, duration_sec,
                          progress_pct, completed, time.time()))

                notify_subscribers("progress_updated", {
                    "path":         filepath,
                    "position_sec": position_sec,
                    "duration_sec": duration_sec,
                    "progress_pct": progress_pct,
                    "completed":    completed,
                })
                last_written_position = position_sec

            if completed:
                with _now_playing_lock:
                    if _session_counter == my_session_id:
                        _now_playing.clear()
                if sock:
                    sock.close()
                break

        except Exception:
            consecutive_failures += 1
            try:
                sock.close()
            except Exception:
                pass
            sock = None

def _finish_session(filepath, duration_sec, last_position=0.0):
    # Write the final position before reading back — last_position was previously
    # passed in but never used; the function only read the DB, which could be
    # stale by up to 5s (poll interval) or reflect a previous session's position
    # if the existing_pct guard had blocked writes.
    with _db(write=True) as conn:
        if last_position > 0:
            # Read real duration from media table in case it was enriched
            # during playback (duration_sec arg may be 0 from play time)
            dur_row = conn.execute(
                "SELECT duration_sec FROM media WHERE path = ?", (filepath,)
            ).fetchone()
            real_dur = (dur_row[0] if dur_row and dur_row[0] and dur_row[0] > 0
                        else duration_sec)
            if real_dur > 0:
                pct = round(last_position / real_dur * 100, 1)
                completed = 1 if pct >= 90 else 0
                conn.execute("""
                    INSERT INTO watch_history
                        (path, position_sec, duration_sec, progress_pct,
                         completed, last_watched, watch_count)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(path) DO UPDATE SET
                        position_sec = excluded.position_sec,
                        duration_sec = excluded.duration_sec,
                        progress_pct = excluded.progress_pct,
                        completed    = excluded.completed,
                        last_watched = excluded.last_watched,
                        watch_count  = watch_count + 1
                """, (filepath, last_position, real_dur, pct, completed, time.time()))
                notify_subscribers("progress_updated", {
                    "path":         filepath,
                    "progress_pct": pct,
                    "completed":    completed,
                })
                return

        # No last_position known — read whatever the DB has
        row = conn.execute(
            "SELECT progress_pct FROM watch_history WHERE path = ?", (filepath,)
        ).fetchone()
        if not row:
            return
        pct = row[0]
        if pct >= 90:
            conn.execute(
                "UPDATE watch_history SET completed = 1 WHERE path = ?", (filepath,)
            )
        notify_subscribers("progress_updated", {
            "path":         filepath,
            "progress_pct": pct,
            "completed":    1 if pct >= 90 else 0,
        })

def _is_mpv(player_cmd):
    return player_cmd and player_cmd[0] in ("mpv",)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
def get_default_dirs():
    home   = os.path.expanduser("~")
    system = platform.system()
    if system == "Windows":
        return [os.path.join(home, "Videos"), os.path.join(home, "Downloads")]
    elif system == "Darwin":
        return [os.path.join(home, "Movies"), os.path.join(home, "Downloads")]
    else:
        return [os.path.join(home, "Videos"), os.path.join(home, "Downloads")]

def load_config() -> dict:
    global _config_cache
    with _config_lock:
        if _config_cache:
            return dict(_config_cache)
    if not os.path.exists(CONFIG_PATH):
        cfg = {"media_dirs": get_default_dirs(), "preferred_player": "auto",
               "featured_count": 5, "tmdb_api_key": "",
               "index_shorts_reels": False}
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    else:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        changed = False
        if "index_shorts_reels" not in cfg:
            cfg["index_shorts_reels"] = False
            changed = True
        if changed:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
    with _config_lock:
        _config_cache = dict(cfg)
    return cfg

def save_config(cfg: dict):
    global _config_cache
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    with _config_lock:
        _config_cache = dict(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# PLAYER DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def find_player():
    system   = platform.system()
    config   = load_config()
    preferred = config.get("preferred_player", "auto")

    if system == "Windows":
        candidates = [
            ("mpv",      ["mpv", "--fullscreen"]),
            ("vlc",      ["vlc", "--fullscreen", "--quiet"]),
            ("wmplayer", ["wmplayer"]),
        ]
        def fallback(path): os.startfile(path); return True
    elif system == "Darwin":
        candidates = [
            ("mpv", ["mpv", "--fs"]),
            ("vlc", ["/Applications/VLC.app/Contents/MacOS/VLC", "--fullscreen"]),
        ]
        def fallback(path): subprocess.Popen(["open", path]); return True
    else:
        candidates = [
            ("mpv", ["mpv", "--fullscreen", "--really-quiet"]),
            ("vlc", ["vlc", "--fullscreen", "--quiet"]),
            ("ffplay", ["ffplay", "-fs"]),
        ]
        def fallback(path): subprocess.Popen(["xdg-open", path]); return True

    if preferred != "auto":
        for name, cmd in candidates:
            if name == preferred and shutil.which(name):
                return cmd, fallback
    for name, cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd, fallback
    return None, fallback


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def _schema_version(conn):
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0

def init_db():
    with _db(write=True) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
        ver = _schema_version(conn)

        if ver < SCHEMA_VERSION:
            print(f"⚙️  DB schema v{ver} → v{SCHEMA_VERSION}: rebuilding...")
            conn.execute("DROP TABLE IF EXISTS media")
            conn.execute("DROP TABLE IF EXISTS watch_history")
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                path             TEXT PRIMARY KEY,
                name             TEXT,
                clean_name       TEXT,
                season_num       INTEGER DEFAULT 0,
                episode_num      INTEGER DEFAULT 0,
                year             INTEGER DEFAULT 0,
                type             TEXT,
                confidence       TEXT DEFAULT 'guessed',
                signals_found    TEXT DEFAULT '[]',
                size_mb          REAL,
                duration         TEXT,
                duration_sec     REAL    DEFAULT 0,
                duration_queued  INTEGER DEFAULT 0,
                thumb_path       TEXT    DEFAULT '',
                thumb_queued     INTEGER DEFAULT 0,
                mtime            REAL,
                added_at         REAL,
                tmdb_id          INTEGER DEFAULT 0,
                anilist_id       INTEGER DEFAULT 0,
                poster_url       TEXT    DEFAULT '',
                poster_source    TEXT    DEFAULT '',
                last_api_attempt REAL    DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_history (
                path            TEXT PRIMARY KEY,
                position_sec    REAL    DEFAULT 0,
                duration_sec    REAL    DEFAULT 0,
                progress_pct    REAL    DEFAULT 0,
                completed       INTEGER DEFAULT 0,
                last_watched    REAL    DEFAULT 0,
                watch_count     INTEGER DEFAULT 0
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_type      ON media(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_added     ON media(added_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_name      ON media(clean_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_thumb     ON media(thumb_queued)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_last_watched ON watch_history(last_watched DESC)")

init_db()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_video_duration(filepath):
    try:
        cmd    = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", filepath]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=8)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ANIME SIGNAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────
# FIX: The old first pattern r'\[.+?\]' matched ANY bracketed text including
# episode codes like [1x01] and [S01E01], causing House MD and similar shows
# to be falsely flagged as anime. The new pattern excludes those episode formats.
# Real anime group tags contain letters: [HorribleSubs], [Erai-raws], etc.
ANIME_SIGNAL_PATTERNS = [
    # Exclude pure episode codes like [1x01] or [S01E01] — not anime signals.
    # Match only brackets containing 2+ chars that aren't a bare NxNN or SxxExx.
    re.compile(r'\[(?!\d+x\d+\]|[Ss]\d+[Ee]\d+\])[^\]]{2,}\]'),
    re.compile(r'\((1080p|720p|480p)\)', re.I),
    re.compile(r'[\u3040-\u30ff]'),
    re.compile(r'[\u4e00-\u9fff]'),
    re.compile(r'\b(BD|BDRip|BluRay)\b', re.I),
]

def has_anime_signals(raw):
    return any(p.search(raw) for p in ANIME_SIGNAL_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# SMART PARSE
# ─────────────────────────────────────────────────────────────────────────────
JUNK_WORDS = re.compile(
    r'(?i)\b('
    r'web[-.]?dl|webrip|hdrip|bdrip|blu[-.]?ray|hdtv|dvdrip|dvdscr|'
    r'hevc|x265|x264|h\.?264|h\.?265|avc|xvid|divx|'
    r'aac|ac3|dts|mp3|flac|'
    r'10bit|10-bit|hdr|sdr|dovi|'
    r'repack|proper|extended|theatrical|directors\.cut|unrated|'
    r'multi|dual|dubbed|subbed|'
    r'yts|yify|season'
    r')\b'
)

def smart_parse(filename):
    raw   = filename
    clean = re.sub(r'\.(mkv|mp4|avi|webm|m4v|mov)$', '', raw, flags=re.I)

    # ── Bracket SxE detection: [1x01]  or  [S01E02] ──────────────────────
    # FIX: These patterns MUST run before bracket stripping — the episode
    # info lives inside the brackets and would be lost by re.sub('\[.*?\]').
    #
    # We also truncate clean at the bracket start position. For filenames like:
    #   "House - [1x01] - Pilot"
    # everything from "[" onwards is episode metadata, not the show title.
    # Truncating here removes " - Pilot" and any other trailing episode title
    # without needing an extra regex.
    #
    # Formats handled:
    #   [1x01]   → season=1, episode=1   (scene/old-format TV)
    #   [S01E01] → season=1, episode=1   (bracket-wrapped standard)
    _bracket_nxnn = re.search(r'\[(\d{1,2})x(\d{1,4})\]',       clean, re.I)
    _bracket_sxxe = re.search(r'\[[Ss](\d{1,2})[Ee](\d{1,4})\]', clean)
    _bracket_match = _bracket_nxnn or _bracket_sxxe

    if _bracket_match:
        # Truncate: drop bracket + everything after (episode title etc.)
        clean = clean[:_bracket_match.start()]

    clean = re.sub(r'\[.*?\]', ' ', clean)
    clean = re.sub(r'\(.*?\)', ' ', clean)

    # Season
    season_num = 0
    if _bracket_nxnn:
        season_num = int(_bracket_nxnn.group(1))
    elif _bracket_sxxe:
        season_num = int(_bracket_sxxe.group(1))
    else:
        season_match = re.search(r'(?i)(?:s(\d{1,2})e\d|season\s*(\d{1,2})\s*(?:episode)?)', clean)
        if season_match:
            season_num = int(next(g for g in season_match.groups() if g is not None))

    # Episode
    episode_num = 0
    if _bracket_nxnn:
        episode_num = int(_bracket_nxnn.group(2))
    elif _bracket_sxxe:
        episode_num = int(_bracket_sxxe.group(2))
    else:
        ep_patterns = [
            (r'(?i)s\d{1,2}e(\d{1,4})',          False),
            (r'(?i)\be(\d{1,4})\b',               False),
            (r'(?i)episode\s*(\d{1,4})',           False),
            (r'(?i)ep\.?\s*(\d{1,4})',             False),
            (r'(?i)\b\d{1,2}x(\d{1,4})\b',        False),
            (r'-\s*(\d{1,4})\b(?!\s*\d)',          True),
        ]
        for pattern, anime_only in ep_patterns:
            m = re.search(pattern, clean)
            if m:
                if anime_only and not has_anime_signals(raw):
                    continue
                episode_num = int(m.group(1))
                break

    year = 0
    ym   = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', clean)
    if ym:
        year = int(ym.group(1))

    clean = re.sub(r'(?i)\s*s\d{1,2}e\d{1,4}.*$',             '', clean)
    clean = re.sub(r'(?i)\s*season\s*\d+\s*episode\s*\d+.*$', '', clean)
    clean = re.sub(r'(?i)\s*season\s*\d+',                     '', clean)
    clean = re.sub(r'(?i)\s*(?:episode|ep\.?)\s*\d+.*$',      '', clean)
    clean = re.sub(r'(?i)\s+e\d{1,4}\b.*$',                   '', clean)
    clean = re.sub(r'(?i)\s*\b\d{1,2}x\d{1,4}\b.*$',         '', clean)
    if has_anime_signals(raw):
        clean = re.sub(r'\s*-\s*\d{1,4}\b.*$',                '', clean)
    clean = re.sub(r'\s*\b(19[5-9]\d|20[0-2]\d)\b',           '', clean)
    clean = JUNK_WORDS.sub(' ', clean)
    clean = re.sub(r'(?i)\b\d{3,4}p\b',                       '', clean)

    clean = clean.replace('.', ' ').replace('_', ' ')
    clean = re.sub(r'[\s\-_]+$', '', clean)
    clean = re.sub(r'^[\s\-_]+', '', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    clean = clean.title()

    if not clean or len(clean) < 2:
        clean = re.sub(r'\.(mkv|mp4|avi|webm|m4v|mov)$', '', raw, flags=re.I).title()

    return clean, season_num, episode_num, year


# ─────────────────────────────────────────────────────────────────────────────
# SMART CLASSIFY
# ─────────────────────────────────────────────────────────────────────────────
def smart_classify(filename, duration_sec, size_mb):
    # If duration not yet known, fall back to filename signals only
    if duration_sec and duration_sec > 0:
        if duration_sec < 180:
            return "reel", "certain", ["duration<3min"]
        if 180 <= duration_sec <= 600:
            return "short_video", "certain", ["duration<10min"]

    raw            = filename
    signals        = []
    episode_weight = 0
    movie_weight   = 0

    if re.search(r'(?i)s\d{1,2}e\d{1,4}', raw):
        episode_weight += 100; signals.append("SxxExx")
    # FIX: Bracket-wrapped episode codes — high confidence, equal to SxxExx.
    # "House - [1x01] - Pilot" and "Firefly.[S01E01].Serenity" both land here.
    if re.search(r'\[\d{1,2}x\d{1,4}\]', raw, re.I):
        episode_weight += 100; signals.append("bracket-NxNN")
    if re.search(r'\[[Ss]\d{1,2}[Ee]\d{1,4}\]', raw):
        episode_weight += 100; signals.append("bracket-SxxExx")
    if re.search(r'(?i)\be\d{1,4}\b', raw):
        episode_weight += 90;  signals.append("E-number")
    if re.search(r'(?i)\b(?:episode|ep\.?)\s*\d+', raw):
        episode_weight += 90;  signals.append("episode-word")
    if re.search(r'(?i)season\s*\d+\s*episode\s*\d+', raw):
        episode_weight += 90;  signals.append("season-episode-word")
    if re.search(r'(?i)\b\d{1,2}x\d{1,4}\b', raw):
        episode_weight += 85;  signals.append("x-notation")
    if re.search(r'-\s*\d{1,4}\b', raw) and has_anime_signals(raw):
        episode_weight += 75;  signals.append("anime-bare-number")

    if re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', raw):
        movie_weight += 80;    signals.append("year-in-filename")
    if size_mb and size_mb > 1200 and duration_sec and duration_sec > 4500:
        movie_weight += 50;    signals.append("large-file-long-duration")
    if duration_sec and duration_sec > 6000 and episode_weight == 0:
        movie_weight += 60;    signals.append("very-long-duration")

    if episode_weight >= 75:
        confidence = "certain" if episode_weight >= 85 else "inferred"
        return "episode", confidence, signals

    if movie_weight >= 80:
        if duration_sec and duration_sec < 3600:
            return "short_video", "inferred", signals + ["short-film"]
        confidence = "certain" if "year-in-filename" in signals else "inferred"
        return "movie", confidence, signals

    if size_mb and size_mb > 700 and duration_sec and duration_sec > 4500:
        return "movie", "guessed", signals + ["size-duration-heuristic"]

    return "episode", "guessed", signals + ["default-fallback"]


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFY AND CACHE  (fast — no FFmpeg here)
# ─────────────────────────────────────────────────────────────────────────────
def classify_and_cache(filename, filepath, mtime, enqueue_duration=True):
    size_mb = os.path.getsize(filepath) / (1024 * 1024)

    with _db() as conn:
        existing = conn.execute(
            "SELECT duration_sec, thumb_path FROM media WHERE path = ?", (filepath,)
        ).fetchone()
    existing_duration  = existing[0] if existing and existing[0] else 0
    existing_thumb     = existing[1] if existing and existing[1] else ""

    duration_sec = existing_duration
    duration_str = f"{int(duration_sec//60)}:{int(duration_sec%60):02d}" if duration_sec > 0 else "..."
    needs_duration = duration_sec == 0

    thumb_path     = existing_thumb or _thumb_path_for(filepath)
    thumb_on_disk  = os.path.exists(thumb_path)
    needs_thumb    = not thumb_on_disk

    clean_name, season_num, episode_num, year = smart_parse(filename)
    media_type, confidence, signals = smart_classify(filename, duration_sec, size_mb)

    with _db(write=True) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO media
            (path, name, clean_name, season_num, episode_num, year,
             type, confidence, signals_found,
             size_mb, duration, duration_sec, duration_queued,
             thumb_path, thumb_queued,
             mtime, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filepath, filename, clean_name, season_num, episode_num, year,
              media_type, confidence, json.dumps(signals),
              round(size_mb, 2), duration_str, duration_sec,
              1 if needs_duration else 0,
              thumb_path if thumb_on_disk else "",
              1 if needs_thumb else 0,
              mtime, mtime))

    if needs_duration and enqueue_duration:
        _duration_queue.put(filepath)
        _ensure_duration_worker()

    if needs_thumb:
        _thumb_queue.put(filepath)
        _ensure_thumb_worker()


# ─────────────────────────────────────────────────────────────────────────────
# POSTER ENRICHMENT — TMDB + AniList  (demand-driven, not bulk)
# ─────────────────────────────────────────────────────────────────────────────
import urllib.request
import urllib.parse
import urllib.error

_enrich_demand_queue: queue.Queue = queue.Queue()
_enrich_worker_started            = False
_enrich_worker_lock               = threading.Lock()

_RETRY_API_ERROR = 24 * 3600
_RETRY_NO_RESULT = 30 * 24 * 3600


def _fuzzy_match(a: str, b: str) -> float:
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    def bigrams(s):
        return [s[i:i+2] for i in range(len(s) - 1)]

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        shared = sum((min(a.count(c), b.count(c)) for c in set(a)))
        return 2 * shared / (len(a) + len(b))

    overlap = 0
    bb_copy = list(bb)
    for gram in ba:
        if gram in bb_copy:
            overlap += 1
            bb_copy.remove(gram)

    return 2.0 * overlap / (len(ba) + len(bb))


def _http_get_json(url: str, headers: dict = None, timeout: int = 8):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _query_tmdb(clean_name: str, year: int, media_type: str, api_key: str):
    base = "https://api.themoviedb.org/3"
    headers = {"Authorization": f"Bearer {api_key}"}

    endpoint = "movie" if media_type == "movie" else "tv"
    params = {"query": clean_name, "language": "en-US", "page": "1"}
    if year and media_type == "movie":
        params["year"] = str(year)

    url = f"{base}/search/{endpoint}?" + urllib.parse.urlencode(params)

    data = None
    for attempt in range(3):
        try:
            data = _http_get_json(url, headers=headers)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  TMDB 429 rate limit for '{clean_name}' — waiting {wait}s")
                time.sleep(wait)
                continue
            elif e.code == 401:
                print(
                    f"  TMDB 401 — wrong key type. "
                    f"Go to themoviedb.org → Settings → API → "
                    f"'Read Access Token (v4 auth)', NOT the 'API Key (v3)'."
                )
                return None, 0, None
            else:
                print(f"  TMDB HTTP {e.code} for '{clean_name}'")
                return None, 0, None
        except Exception as e:
            print(f"  TMDB request failed for '{clean_name}': {e}")
            return None, 0, None

    if data is None:
        return None, 0, None

    results = data.get("results", [])
    if not results:
        return None, 0, None

    for result in results[:3]:
        vote_count = result.get("vote_count", 0)
        if vote_count < 10:
            continue

        result_title = result.get("title") or result.get("name") or ""
        similarity   = _fuzzy_match(clean_name, result_title)

        if similarity < 0.70:
            continue

        poster_path = result.get("poster_path")
        if not poster_path:
            continue

        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
        tmdb_id    = result.get("id", 0)
        print(f"  ✅ TMDB: '{clean_name}' → '{result_title}' ({similarity:.0%}, {vote_count} votes)")
        return poster_url, tmdb_id, result_title

    print(f"  ⚠️  TMDB: no confident match for '{clean_name}'")
    return None, 0, None


def _query_anilist(clean_name: str):
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        id
        title { english romaji }
        coverImage { extraLarge large }
        popularity
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"search": clean_name}}).encode()

    data = None
    for attempt in range(3):
        req = urllib.request.Request(
            "https://graphql.anilist.co",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  AniList 429 rate limit for '{clean_name}' — waiting {wait}s")
                time.sleep(wait)
                continue
            else:
                print(f"  AniList HTTP {e.code} for '{clean_name}'")
                return None, 0, None
        except Exception as e:
            print(f"  AniList request failed for '{clean_name}': {e}")
            return None, 0, None

    if data is None:
        return None, 0, None

    media = data.get("data", {}).get("Media")
    if not media:
        return None, 0, None

    popularity = media.get("popularity", 0)
    if popularity < 50:
        return None, 0, None

    titles = media.get("title", {})
    best_sim = 0.0
    for candidate in [titles.get("english"), titles.get("romaji")]:
        if candidate:
            best_sim = max(best_sim, _fuzzy_match(clean_name, candidate))

    if best_sim < 0.65:
        print(f"  ⚠️  AniList: low similarity for '{clean_name}' (best {best_sim:.0%})")
        return None, 0, None

    cover = media.get("coverImage", {})
    poster_url = cover.get("extraLarge") or cover.get("large")
    if not poster_url:
        return None, 0, None

    anilist_id    = media.get("id", 0)
    matched_title = titles.get("english") or titles.get("romaji") or clean_name
    print(f"  ✅ AniList: '{clean_name}' → '{matched_title}' ({best_sim:.0%})")
    return poster_url, anilist_id, matched_title


def _enrich_one(row: dict, api_key: str):
    path       = row["path"]
    clean_name = row["clean_name"] or ""
    year       = row["year"] or 0
    media_type = row["type"] or "movie"
    is_anime   = has_anime_signals(row["name"] or "")

    now = time.time()

    poster_url    = None
    poster_source = None
    tmdb_id       = 0
    anilist_id    = 0

    if is_anime:
        poster_url, anilist_id, _ = _query_anilist(clean_name)
        if poster_url:
            poster_source = "anilist"
        elif api_key:
            poster_url, tmdb_id, _ = _query_tmdb(clean_name, year, media_type, api_key)
            if poster_url:
                poster_source = "tmdb"
    elif api_key:
        poster_url, tmdb_id, _ = _query_tmdb(clean_name, year, media_type, api_key)
        if poster_url:
            poster_source = "tmdb"

    if poster_url and poster_source:
        with _db(write=True) as conn:
            conn.execute("""
                UPDATE media
                SET poster_url = ?, poster_source = ?,
                    tmdb_id = ?, anilist_id = ?,
                    last_api_attempt = ?
                WHERE path = ?
            """, (poster_url, poster_source, tmdb_id, anilist_id, now, path))
        notify_subscribers("poster_updated", {
            "path":       path,
            "poster_url": poster_url,
            "source":     poster_source,
        })
    else:
        with _db(write=True) as conn:
            conn.execute(
                "UPDATE media SET last_api_attempt = ?, poster_source = 'not_found' WHERE path = ?",
                (now, path)
            )


def _ensure_enrich_worker():
    global _enrich_worker_started
    with _enrich_worker_lock:
        if not _enrich_worker_started:
            t = threading.Thread(target=_enrich_demand_worker, daemon=True)
            t.start()
            _enrich_worker_started = True


def _enrich_demand_worker():
    while True:
        try:
            row = _enrich_demand_queue.get(timeout=30)
        except queue.Empty:
            continue

        with _db() as conn:
            current = conn.execute(
                "SELECT poster_url FROM media WHERE path = ?", (row["path"],)
            ).fetchone()
        if current and current[0]:
            _enrich_demand_queue.task_done()
            continue

        config  = load_config()

        # Enrich whenever a TMDB key is configured.
        # The old enable_poster_enrichment flag was never written to config.json
        # so enrichment was permanently disabled for everyone. Now we check for
        # a non-empty key only. The 401/429 handlers protect against bad keys.
        api_key = config.get("tmdb_api_key", "").strip()
        if not api_key and not has_anime_signals(row.get("name", "")):
            # No key and not anime — AniList needs no key, TMDB does
            _enrich_demand_queue.task_done()
            continue

        try:
            _enrich_one(row, api_key)
        except Exception as e:
            print(f"  ❌ Enrichment error for {row.get('path')}: {e}")
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET last_api_attempt=?, poster_source='api_error' WHERE path=?",
                    (time.time(), row["path"])
                )

        _enrich_demand_queue.task_done()
        time.sleep(0.5)


def _startup_enrich():
    _scan_complete.wait()
    time.sleep(3)

    with _db() as conn:
        rows = conn.execute("""
            SELECT path, name, clean_name, year, type, last_api_attempt, poster_source
            FROM media
            WHERE poster_url = ''
              AND type IN ('movie', 'episode')
            ORDER BY added_at DESC
            LIMIT 50
        """).fetchall()

    if rows:
        print(f"🎨 Startup enrichment: queuing top {len(rows)} items")
        for row in rows:
            _enrich_demand_queue.put(dict(row))
        _ensure_enrich_worker()


# ─────────────────────────────────────────────────────────────────────────────
# CORE SCAN — fast filesystem walk, no FFmpeg
# ─────────────────────────────────────────────────────────────────────────────
def run_scan(notify=True):
    if not _scan_lock.acquire(blocking=False):
        return

    try:
        with _scan_state_lock:
            _scan_state.update({"running": True, "phase": "scanning",
                                "total": 0, "done": 0,
                                "new_files": 0, "removed": 0})

        config      = load_config()
        media_dirs  = config.get("media_dirs", get_default_dirs())
        current_files = set()
        new_files     = []

        all_files = []
        for directory in media_dirs:
            expanded = os.path.expanduser(directory)
            if not os.path.exists(expanded):
                continue
            for root, dirs, files in os.walk(expanded):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for file in files:
                    if file.lower().endswith(MEDIA_EXTS):
                        all_files.append((file, os.path.join(root, file)))

        with _scan_state_lock:
            _scan_state["total"] = len(all_files)

        for i, (file, filepath) in enumerate(all_files):
            current_files.add(filepath)
            mtime = os.path.getmtime(filepath)
            with _db() as conn:
                cached = conn.execute(
                    "SELECT mtime FROM media WHERE path = ?", (filepath,)
                ).fetchone()
            if not cached or cached[0] != mtime:
                classify_and_cache(file, filepath, mtime)
                new_files.append(file)

            with _scan_state_lock:
                _scan_state["done"]      = i + 1
                _scan_state["new_files"] = len(new_files)

        with _db() as conn:
            db_files = [r[0] for r in conn.execute("SELECT path FROM media").fetchall()]
        removed = [f for f in db_files if f not in current_files]
        if removed:
            with _db(write=True) as conn:
                conn.executemany("DELETE FROM media WHERE path = ?", [(f,) for f in removed])

        with _db() as conn:
            stale = conn.execute("""
                SELECT path, name, size_mb, duration_sec FROM media
                WHERE type = 'episode'
                  AND duration_sec > 0
                  AND duration_sec < 600
            """).fetchall()
        fixed = 0
        for row in stale:
            filepath, filename, size_mb, duration_sec = row
            media_type, confidence, signals = smart_classify(
                filename, duration_sec, size_mb or 0
            )
            if media_type != "episode":
                with _db(write=True) as conn:
                    conn.execute(
                        "UPDATE media SET type=?, confidence=?, signals_found=? WHERE path=?",
                        (media_type, confidence, json.dumps(signals), filepath)
                    )
                fixed += 1
        if fixed:
            print(f"🔧 Reclassified {fixed} misclassified short episodes")

        with _scan_state_lock:
            _scan_state.update({"running": False, "phase": "complete",
                                "removed": len(removed)})

        if notify and (new_files or removed or fixed):
            notify_subscribers("library_updated", {
                "new":     len(new_files),
                "removed": len(removed),
                "reclassified": fixed,
            })
            print(f"📡 SSE: {len(new_files)} new, {len(removed)} removed, {fixed} reclassified")

        print(f"✅ Scan complete: {len(all_files)} files, {len(new_files)} new, {len(removed)} removed")

    except Exception as e:
        with _scan_state_lock:
            _scan_state.update({"running": False, "phase": "error"})
        print(f"❌ Scan error: {e}")
        import traceback; traceback.print_exc()

    finally:
        _scan_complete.set()
        _scan_lock.release()


# ─────────────────────────────────────────────────────────────────────────────
# FILESYSTEM WATCHER
# ─────────────────────────────────────────────────────────────────────────────
def filesystem_watcher():
    _scan_complete.wait()
    while True:
        time.sleep(30)
        run_scan(notify=True)

watcher_thread = threading.Thread(target=filesystem_watcher, daemon=True)
watcher_thread.start()

threading.Thread(target=_migrate_tmp_thumbnails, daemon=True).start()
threading.Thread(target=_startup_enrich, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# DB QUERY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_all_media(page: int = None, limit: int = 100, search: str = "", type_filter_param: str = ""):
    cfg          = load_config()
    index_shorts = cfg.get("index_shorts_reels", False)

    shorts_clause = "" if index_shorts else "AND type NOT IN ('short_video', 'reel')"

    extra_clauses = []
    params        = []

    if type_filter_param and type_filter_param != "all":
        extra_clauses.append("AND m.type = ?")
        params.append(type_filter_param)

    if search:
        extra_clauses.append("AND (m.clean_name LIKE ? OR m.name LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern])

    extra = " ".join(extra_clauses)

    with _db() as conn:
        if page is None:
            rows = conn.execute(f"""
                SELECT m.*,
                       COALESCE(w.progress_pct, 0)   AS progress_pct,
                       COALESCE(w.completed,    0)    AS completed,
                       COALESCE(w.position_sec, 0)    AS position_sec,
                       COALESCE(w.last_watched, 0)    AS last_watched
                FROM media m
                LEFT JOIN watch_history w ON m.path = w.path
                WHERE 1=1 {shorts_clause} {extra}
                ORDER BY m.clean_name ASC, m.season_num ASC, m.episode_num ASC
            """, params).fetchall()
            return [dict(r) for r in rows]

        total = conn.execute(
            f"SELECT COUNT(*) FROM media m WHERE 1=1 {shorts_clause} {extra}",
            params
        ).fetchone()[0]

        offset = (page - 1) * limit
        rows = conn.execute(f"""
            SELECT m.*,
                   COALESCE(w.progress_pct, 0)   AS progress_pct,
                   COALESCE(w.completed,    0)    AS completed,
                   COALESCE(w.position_sec, 0)    AS position_sec,
                   COALESCE(w.last_watched, 0)    AS last_watched
            FROM media m
            LEFT JOIN watch_history w ON m.path = w.path
            WHERE 1=1 {shorts_clause} {extra}
            ORDER BY m.clean_name ASC, m.season_num ASC, m.episode_num ASC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        import math
        return {
            "items":  [dict(r) for r in rows],
            "total":  total,
            "page":   page,
            "limit":  limit,
            "pages":  math.ceil(total / limit) if limit > 0 else 1,
        }

def fetch_featured(count):
    pool = count * 4
    with _db() as conn:
        rows = conn.execute("""
            SELECT * FROM (
                SELECT * FROM media
                WHERE type IN ('movie', 'episode')
                ORDER BY added_at DESC
                LIMIT ?
            ) ORDER BY RANDOM() LIMIT ?
        """, (pool, count)).fetchall()

        if not rows:
            rows = conn.execute(
                "SELECT * FROM media ORDER BY RANDOM() LIMIT ?", (count,)
            ).fetchall()

    return [dict(r) for r in rows]

def fetch_continue_watching():
    with _db() as conn:
        rows = conn.execute("""
            SELECT m.*, w.progress_pct, w.position_sec, w.last_watched, w.completed
            FROM watch_history w
            JOIN media m ON m.path = w.path
            WHERE w.progress_pct >= 2 AND w.progress_pct < 90 AND w.completed = 0
            ORDER BY w.last_watched DESC
            LIMIT 20
        """).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/events')
def sse_stream():
    q = queue.Queue(maxsize=20)
    with _sse_lock:
        _sse_subscribers.append(q)

    def stream():
        yield "event: connected\ndata: {}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            with _sse_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route('/api/scan/status')
def scan_status():
    with _scan_state_lock:
        state = dict(_scan_state)
    state["duration_queue_size"] = _duration_queue.qsize()
    state["thumb_queue_size"]    = _thumb_queue.qsize()
    state["scan_complete"]       = _scan_complete.is_set()
    return jsonify(state)


@app.route('/api/scan/trigger', methods=['POST'])
def trigger_scan():
    threading.Thread(target=run_scan, kwargs={"notify": True}, daemon=True).start()
    return jsonify({"status": "scan started"})


@app.route('/api/media', methods=['GET'])
def get_media():
    page_str   = request.args.get("page")
    limit_str  = request.args.get("limit", "100")
    search     = request.args.get("search", "").strip()
    type_param = request.args.get("type", "").strip()

    if page_str is not None:
        try:
            page  = max(1, int(page_str))
            limit = max(1, min(500, int(limit_str)))
        except ValueError:
            return jsonify({"error": "page and limit must be integers"}), 400
        return jsonify(fetch_all_media(
            page=page, limit=limit,
            search=search, type_filter_param=type_param
        ))

    return jsonify(fetch_all_media(search=search, type_filter_param=type_param))


@app.route('/api/media', methods=['DELETE'])
def delete_media():
    data     = request.json or {}
    filepath = data.get("path", "").strip()
    if not filepath:
        return jsonify({"error": "path is required"}), 400

    if not os.path.isfile(filepath):
        with _db(write=True) as conn:
            conn.execute("DELETE FROM media WHERE path = ?", (filepath,))
        notify_subscribers("media_deleted", {"path": filepath})
        return jsonify({"status": "ok", "note": "file not on disk, removed from DB"})

    basename = os.path.basename(filepath)
    dest     = os.path.join(TRASH_DIR, basename)
    if os.path.exists(dest):
        name, ext = os.path.splitext(basename)
        dest = os.path.join(TRASH_DIR, f"{name}_{abs(hash(filepath))}{ext}")
    try:
        shutil.move(filepath, dest)
    except Exception as e:
        return jsonify({"error": f"Could not move file: {e}"}), 500

    with _db(write=True) as conn:
        conn.execute("DELETE FROM media         WHERE path = ?", (filepath,))
        conn.execute("DELETE FROM watch_history WHERE path = ?", (filepath,))
    notify_subscribers("media_deleted", {"path": filepath})
    return jsonify({"status": "ok", "moved_to": dest})


@app.route('/api/featured')
def get_featured():
    _scan_complete.wait(timeout=60)
    config = load_config()
    return jsonify(fetch_featured(config.get("featured_count", 5)))


@app.route('/api/continue_watching')
def get_continue_watching():
    return jsonify(fetch_continue_watching())

@app.route('/api/stats')
def get_stats():
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        movies = conn.execute("SELECT COUNT(*) FROM media WHERE type = 'movie'").fetchone()[0]
        episodes = conn.execute("SELECT COUNT(*) FROM media WHERE type = 'episode'").fetchone()[0]
        shows = conn.execute("SELECT COUNT(DISTINCT clean_name) FROM media WHERE type = 'episode'").fetchone()[0]
        shorts = conn.execute("SELECT COUNT(*) FROM media WHERE type IN ('short_video', 'reel')").fetchone()[0]
        total_size_mb = conn.execute("SELECT COALESCE(SUM(size_mb), 0) FROM media").fetchone()[0]
        posters = conn.execute("SELECT COUNT(*) FROM media WHERE poster_url != '' AND poster_url IS NOT NULL").fetchone()[0]
        pending_duration = conn.execute("SELECT COUNT(*) FROM media WHERE duration_sec = 0 OR duration_sec IS NULL").fetchone()[0]
        pending_thumbs = conn.execute("SELECT COUNT(*) FROM media WHERE (thumb_path = '' OR thumb_path IS NULL)").fetchone()[0]

    return jsonify({
        "total":            total,
        "movies":           movies,
        "episodes":         episodes,
        "shows":            shows,
        "shorts":           shorts,
        "total_size_gb":    round(total_size_mb / 1024, 2),
        "posters":          posters,
        "pending_duration": pending_duration,
        "pending_thumbs":   pending_thumbs,
    })


@app.route('/api/play', methods=['POST'])
def play_media():
    filepath = request.json.get('path')
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    player_cmd, fallback = find_player()
    try:
        with _db() as conn:
            media_row = conn.execute(
                "SELECT duration_sec FROM media WHERE path = ?", (filepath,)
            ).fetchone()
            hist_row = conn.execute(
                "SELECT position_sec, progress_pct, completed FROM watch_history WHERE path = ?",
                (filepath,)
            ).fetchone()

        duration_sec = media_row[0] if media_row and media_row[0] else 0.0

        resume_pos = 0.0
        if hist_row:
            saved_pos, saved_pct, completed = hist_row
            if saved_pct and 5 <= saved_pct < 90 and not completed:
                resume_pos = float(saved_pos) if saved_pos else 0.0

        if player_cmd and _is_mpv(player_cmd):
            sock_path  = _mpv_socket_path()
            screenshot_dir = tempfile.gettempdir()
            launch_cmd = player_cmd + [
                f"--input-ipc-server={sock_path}",
                "--really-quiet",
                f"--screenshot-directory={screenshot_dir}",
            ]
            if resume_pos > 0:
                launch_cmd += [f"--start={resume_pos}"]
            launch_cmd += [filepath]
            if platform.system() != "Windows":
                try:
                    os.remove(sock_path)
                except FileNotFoundError:
                    pass

            subprocess.Popen(launch_cmd,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

            with _now_playing_lock:
                global _session_counter
                # Save position of any previously-playing video before handing
                # off. The old tracker will break on its next wakeup because
                # _session_counter no longer matches its my_session_id.
                if _now_playing:
                    _finish_session(
                        _now_playing["path"],
                        _now_playing["duration_sec"],
                        0.0  # tracker will have written the last real position already
                    )
                _session_counter += 1
                my_session_id = _session_counter
                _now_playing.clear()
                _now_playing["path"]         = filepath
                _now_playing["duration_sec"] = duration_sec

            tracker = threading.Thread(
                target=_mpv_tracker,
                args=(my_session_id,),
                daemon=True
            )
            tracker.start()

        elif player_cmd:
            subprocess.Popen(player_cmd + [filepath],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            fallback(filepath)

        now = time.time()
        with _db(write=True) as conn:
            conn.execute("""
                INSERT INTO watch_history
                    (path, last_watched, watch_count, duration_sec, position_sec,
                     progress_pct, completed)
                VALUES (?, ?, 1, ?, 0, 0, 0)
                ON CONFLICT(path) DO UPDATE SET
                    last_watched = excluded.last_watched,
                    watch_count  = watch_count + 1,
                    duration_sec = CASE WHEN excluded.duration_sec > 0
                                   THEN excluded.duration_sec
                                   ELSE watch_history.duration_sec END
            """, (filepath, now, duration_sec))

        return jsonify({
            "status":       "playing",
            "tracking":     _is_mpv(player_cmd),
            "player":       player_cmd[0] if player_cmd else "system default",
            "resumed_from": resume_pos if resume_pos > 0 else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/progress', methods=['POST'])
def update_progress():
    data         = request.json or {}
    filepath     = data.get('path')
    position_sec = float(data.get('position_sec', 0))
    duration_sec = float(data.get('duration_sec', 0))

    if not filepath:
        return jsonify({"error": "path required"}), 400

    progress_pct = round((position_sec / duration_sec * 100), 1) if duration_sec > 0 else 0
    completed    = 1 if progress_pct >= 90 else 0

    with _db(write=True) as conn:
        conn.execute("""
            INSERT INTO watch_history
                (path, position_sec, duration_sec, progress_pct, completed, last_watched, watch_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(path) DO UPDATE SET
                position_sec = excluded.position_sec,
                duration_sec = excluded.duration_sec,
                progress_pct = excluded.progress_pct,
                completed    = excluded.completed,
                last_watched = excluded.last_watched,
                watch_count  = watch_count + 1
        """, (filepath, position_sec, duration_sec, progress_pct, completed, time.time()))

    return jsonify({"status": "ok", "progress_pct": progress_pct, "completed": completed})


@app.route('/api/progress/reset', methods=['POST'])
def reset_progress():
    filepath = (request.json or {}).get('path')
    if not filepath:
        return jsonify({"error": "path required"}), 400
    with _db(write=True) as conn:
        conn.execute("DELETE FROM watch_history WHERE path = ?", (filepath,))
    return jsonify({"status": "reset"})


@app.route('/api/thumbnail')
def get_thumbnail():
    filepath  = request.args.get('path')
    is_hero   = request.args.get('hero', '0') == '1'

    if not filepath or not os.path.exists(filepath):
        return _thumb_placeholder(is_hero)

    with _db() as conn:
        row = conn.execute(
            "SELECT poster_url, thumb_path, thumb_queued FROM media WHERE path = ?",
            (filepath,)
        ).fetchone()

    if row:
        poster_url = row[0]
        thumb_path = row[1]
        queued     = row[2]

        if poster_url:
            from flask import redirect
            if is_hero and "/w500/" in poster_url:
                return redirect(poster_url.replace("/w500/", "/w1280/"))
            return redirect(poster_url)

        wanted_path = _thumb_path_for(filepath, hero=is_hero)
        if os.path.exists(wanted_path):
            return send_file(wanted_path, mimetype='image/jpeg')

        if is_hero and thumb_path and os.path.exists(thumb_path):
            return send_file(thumb_path, mimetype='image/jpeg')

        if not queued:
            _thumb_queue.put(filepath)
            _ensure_thumb_worker()
            with _db(write=True) as conn:
                conn.execute(
                    "UPDATE media SET thumb_queued=1 WHERE path=?", (filepath,)
                )
    else:
        card_path = _thumb_path_for(filepath, hero=False)
        hero_path = _thumb_path_for(filepath, hero=True)
        if is_hero and os.path.exists(hero_path):
            return send_file(hero_path, mimetype='image/jpeg')
        if os.path.exists(card_path):
            return send_file(card_path, mimetype='image/jpeg')
        _thumb_queue.put(filepath)
        _ensure_thumb_worker()

    return _thumb_placeholder(is_hero)


def _thumb_placeholder(hero: bool = False):
    w, h = (1280, 720) if hero else (640, 360)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="#1c1c28"/>'
        f'<text x="{w//2}" y="{h//2}" text-anchor="middle" fill="#6b7280" '
        f'font-family="sans-serif" font-size="14">Generating preview...</text>'
        f'</svg>'
    ).encode()
    return Response(svg, mimetype='image/svg+xml')


@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/config', methods=['POST'])
def update_config():
    cfg = request.json
    if not cfg:
        return jsonify({"error": "No config"}), 400

    old_cfg = load_config()
    save_config(cfg)

    threading.Thread(target=run_scan, kwargs={"notify": True}, daemon=True).start()
    notify_subscribers("config_updated", {"featured_count": cfg.get("featured_count", 5)})
    return jsonify({"status": "saved", "config": cfg})

@app.route('/api/players')
def get_available_players():
    all_players = ["mpv", "vlc", "ffplay", "wmplayer"]
    available   = [p for p in all_players if shutil.which(p)]
    player_cmd, _ = find_player()
    current     = player_cmd[0] if player_cmd else "system default"
    return jsonify({"available": available, "current": current, "os": platform.system()})


@app.route('/api/enrich/batch', methods=['POST'])
def enrich_batch():
    data   = request.json or {}
    groups = data.get("groups", [])
    if not groups:
        return jsonify({"status": "ok", "queued": 0})

    now    = time.time()
    queued = 0

    for group in groups[:10]:
        clean_name = (group.get("clean_name") or "").strip()
        if not clean_name:
            continue

        with _db() as conn:
            row = conn.execute("""
                SELECT path, name, clean_name, year, type,
                       last_api_attempt, poster_source
                FROM media
                WHERE clean_name = ?
                  AND poster_url  = ''
                  AND type IN ('movie', 'episode')
                LIMIT 1
            """, (clean_name,)).fetchone()

        if not row:
            continue

        row     = dict(row)
        attempt = row.get("last_api_attempt") or 0
        source  = row.get("poster_source") or ""
        elapsed = now - attempt

        if source == "not_found"  and elapsed < _RETRY_NO_RESULT:
            continue
        if source == "api_error"  and elapsed < _RETRY_API_ERROR:
            continue
        if source in ("tmdb", "anilist"):
            continue

        _enrich_demand_queue.put(row)
        _ensure_enrich_worker()
        queued += 1

    return jsonify({"status": "queued", "queued": queued})


@app.route('/api/poster/status')
def poster_status():
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM media WHERE type IN ('movie','episode')"
        ).fetchone()[0]
        enriched = conn.execute(
            "SELECT COUNT(*) FROM media WHERE poster_url != '' AND type IN ('movie','episode')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM media WHERE poster_url = '' AND type IN ('movie','episode')"
        ).fetchone()[0]
    return jsonify({"total": total, "enriched": enriched, "pending": pending})


@app.route('/api/poster/reset', methods=['POST'])
def poster_reset():
    data     = request.json or {}
    filepath = data.get("path")
    with _db(write=True) as conn:
        if filepath:
            conn.execute(
                "UPDATE media SET poster_url='', poster_source='', last_api_attempt=0 WHERE path=?",
                (filepath,)
            )
        else:
            conn.execute(
                "UPDATE media SET poster_url='', poster_source='', last_api_attempt=0"
            )
    return jsonify({"status": "reset", "scope": filepath or "all"})


@app.route('/api/setup/status')
def setup_status():
    cfg       = load_config()
    dirs      = cfg.get("media_dirs", [])
    defaults  = get_default_dirs()

    all_default = all(d in defaults for d in dirs) and len(dirs) > 0

    dirs_info = {}
    any_has_media = False
    for d in dirs:
        expanded = os.path.expanduser(d)
        exists   = os.path.isdir(expanded)
        has_media = False
        if exists:
            for root, _, files in os.walk(expanded):
                if any(f.lower().endswith(MEDIA_EXTS) for f in files):
                    has_media = True
                    any_has_media = True
                    break
        dirs_info[d] = {"exists": exists, "has_media": has_media}

    with _db() as conn:
        db_count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    first_run = all_default and not any_has_media and db_count == 0

    return jsonify({
        "first_run":    first_run,
        "media_dirs":   dirs,
        "dirs_info":    dirs_info,
        "db_count":     db_count,
        "default_dirs": defaults,
    })


@app.route('/api/setup/complete', methods=['POST'])
def setup_complete():
    data = request.json or {}
    dirs = data.get("media_dirs", [])

    if not dirs:
        return jsonify({"error": "At least one media directory is required"}), 400

    warnings = [d for d in dirs if not os.path.isdir(os.path.expanduser(d))]

    cfg = load_config()
    cfg["media_dirs"] = dirs
    save_config(cfg)

    threading.Thread(target=run_scan, kwargs={"notify": True}, daemon=True).start()

    return jsonify({
        "status":   "ok",
        "dirs":     dirs,
        "warnings": warnings,
    })


@app.route('/api/setup/browse', methods=['POST'])
def setup_browse():
    data = request.json or {}
    raw  = (data.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "path required"}), 400

    expanded = os.path.expanduser(raw)
    exists   = os.path.isdir(expanded)

    subdirs = []
    if exists:
        try:
            subdirs = sorted([
                os.path.join(expanded, d)
                for d in os.listdir(expanded)
                if os.path.isdir(os.path.join(expanded, d))
                and not d.startswith('.')
            ])[:20]
        except PermissionError:
            pass

    return jsonify({
        "path":    expanded,
        "exists":  exists,
        "subdirs": subdirs,
    })


@app.route('/api/debug/parse')
def debug_parse():
    filename     = request.args.get('filename', '')
    duration_sec = float(request.args.get('duration', 3600))
    size_mb      = float(request.args.get('size', 500))
    if not filename:
        return jsonify({"error": "Provide ?filename="}), 400
    clean_name, season_num, episode_num, year = smart_parse(filename)
    media_type, confidence, signals           = smart_classify(filename, duration_sec, size_mb)
    return jsonify({
        "input":    filename,
        "parse":    {"clean_name": clean_name, "season_num": season_num,
                     "episode_num": episode_num, "year": year},
        "classify": {"type": media_type, "confidence": confidence, "signals": signals},
    })

@app.route('/api/debug/library')
def debug_library():
    with _db() as conn:
        rows = conn.execute("""
            SELECT name, clean_name, season_num, episode_num, year,
                   type, confidence, signals_found, size_mb, duration, duration_queued,
                   poster_source, poster_url, last_api_attempt
            FROM media ORDER BY confidence ASC, clean_name ASC
        """).fetchall()
        return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("🚀 Localflix starting...")
    config = load_config()
    print(f"📁 Scanning: {config['media_dirs']}")
    player_cmd, _ = find_player()
    print(f"🎬 Player: {player_cmd[0] if player_cmd else 'system default'}")

    threading.Thread(target=run_scan, kwargs={"notify": True}, daemon=True).start()

    app.run(port=5000, debug=True, threaded=True, use_reloader=False)