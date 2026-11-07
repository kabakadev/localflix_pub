import {
  useEffect,
  useLayoutEffect,
  useState,
  useRef,
  useCallback,
  useMemo,
  memo,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import "./App.css";

const API = "http://localhost:5000";

// ─── HERO BANNER ─────────────────────────────────────────────────────────────
function HeroBanner({ items, onPlay }) {
  const [current, setCurrent] = useState(0);
  const [fading, setFading] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    if (items.length > 0) {
      setCurrent((c) => (c >= items.length ? 0 : c));
    }
  }, [items.length]);

  const goTo = useCallback(
    (indexOrFn) => {
      setFading(true);
      setTimeout(() => {
        setCurrent((prev) => {
          const next =
            typeof indexOrFn === "function" ? indexOrFn(prev) : indexOrFn;
          return items.length > 0
            ? Math.max(0, Math.min(next, items.length - 1))
            : 0;
        });
        setFading(false);
      }, 400);
    },
    [items.length]
  );

  useEffect(() => {
    if (items.length < 2) return;
    timerRef.current = setInterval(() => {
      goTo((prev) => (prev + 1) % items.length);
    }, 8000);
    return () => clearInterval(timerRef.current);
  }, [items.length, goTo]);

  if (!items.length) return null;
  const item = items[current] ?? items[0];
  if (!item) return null;

  return (
    <div className="hero">
      <div className={`hero-bg ${fading ? "fading" : ""}`}>
        <img
          src={`${API}/api/thumbnail?path=${encodeURIComponent(
            item.path
          )}&hero=1`}
          alt={item.clean_name}
        />
        <div className="hero-gradient" />
      </div>
      <div className="hero-content">
        <div className="hero-meta">
          <span className={`hero-badge ${item.type}`}>
            {item.type === "episode"
              ? "SERIES"
              : item.type === "movie"
              ? "MOVIE"
              : item.type.toUpperCase()}
          </span>
          {item.duration && item.duration !== "..." && (
            <span className="hero-duration">{item.duration}</span>
          )}
        </div>
        <h2 className="hero-title">{item.clean_name}</h2>
        <p className="hero-filename">{item.name}</p>
        <div className="hero-actions">
          <button className="hero-play-btn" onClick={() => onPlay(item.path)}>
            <span className="play-icon">▶</span> Play Now
          </button>
        </div>
      </div>
      <div className="hero-dots">
        {items.map((_, i) => (
          <button
            key={i}
            className={`hero-dot ${i === current ? "active" : ""}`}
            onClick={() => {
              clearInterval(timerRef.current);
              goTo(i);
            }}
          />
        ))}
      </div>
      <div className="hero-progress">
        <div className="hero-progress-bar" key={current} />
      </div>
    </div>
  );
}

// ─── CONTEXT MENU ─────────────────────────────────────────────────────────────
function ContextMenu({
  x,
  y,
  item,
  onClose,
  onMarkWatched,
  onMarkUnwatched,
  onDelete,
}) {
  const menuRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    const onClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClickOutside);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClickOutside);
    };
  }, [onClose]);

  const [pos, setPos] = useState({ left: x, top: y });
  useLayoutEffect(() => {
    const el = menuRef.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const newLeft = x + width > window.innerWidth ? x - width : x;
    const newTop = y + height > window.innerHeight ? y - height : y;
    setPos({ left: newLeft, top: newTop });
  }, [x, y]);

  const hasProgress = (item.progress_pct || 0) > 2;
  const isCompleted = item.completed === 1;
  const handle = (fn) => (e) => {
    e.stopPropagation();
    fn();
    onClose();
  };

  return (
    <div
      className="ctx-menu"
      ref={menuRef}
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="ctx-menu-title" title={item.name}>
        {item.clean_name || item.name}
      </div>
      <div className="ctx-menu-divider" />
      {!isCompleted && (
        <button
          className="ctx-menu-item"
          onClick={handle(() => onMarkWatched(item))}
        >
          <span className="ctx-menu-icon">✓</span>Mark as Watched
        </button>
      )}
      {(hasProgress || isCompleted) && (
        <button
          className="ctx-menu-item"
          onClick={handle(() => onMarkUnwatched(item))}
        >
          <span className="ctx-menu-icon">↺</span>
          {isCompleted ? "Mark as Unwatched" : "Clear Progress"}
        </button>
      )}
      <div className="ctx-menu-divider" />
      <button
        className="ctx-menu-item ctx-menu-item--danger"
        onClick={handle(() => onDelete(item))}
      >
        <span className="ctx-menu-icon">🗑</span>Remove from Library
      </button>
    </div>
  );
}

// ─── MEDIA CARD ──────────────────────────────────────────────────────────────
const MediaCard = memo(function MediaCard({
  item,
  onPlay,
  onDelete,
  onCardMenu,
}) {
  const progress = item.progress_pct || 0;
  const completed = item.completed === 1;
  const hasDuration = item.duration && item.duration !== "...";
  const imgSrc = item.poster_url
    ? item.poster_url
    : `${API}/api/thumbnail?path=${encodeURIComponent(item.path)}`;

  return (
    <div
      className={`card ${completed ? "card--completed" : ""}`}
      onClick={() => onPlay(item.path)}
      onContextMenu={(e) => {
        e.preventDefault();
        if (onCardMenu) onCardMenu(e, item);
      }}
    >
      <div className="img-container">
        <img
          src={imgSrc}
          alt={item.name}
          loading="lazy"
          className={item.poster_url ? "card-img card-img--poster" : "card-img"}
        />
        {hasDuration && <div className="duration-pill">{item.duration}</div>}
        {!hasDuration && (
          <div className="duration-pill duration-pill--pending">⏳</div>
        )}
        <div className="play-overlay">
          <div className="play-btn">▶</div>
        </div>
        {onDelete && (
          <button
            className="card-trash-btn"
            title="Move to trash"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(item);
            }}
          >
            🗑
          </button>
        )}
        {progress > 2 && !completed && (
          <div className="card-progress-bar">
            <div
              className="card-progress-fill"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
        {completed && <div className="card-completed-badge">✓ Watched</div>}
      </div>
      <div className="card-info">
        <div className="meta">
          {item.type === "episode" && item.episode_num > 0 && (
            <span className="badge episode">EP {item.episode_num}</span>
          )}
          {item.type === "movie" && <span className="badge movie">MOVIE</span>}
          {item.type === "reel" && <span className="badge reel">REEL</span>}
          {item.type === "short_video" && (
            <span className="badge short">SHORT</span>
          )}
          <span className="size">{item.size_mb} MB</span>
        </div>
        <p className="title">{item.name}</p>
      </div>
    </div>
  );
});

// ─── VIRTUALISED HORIZONTAL CAROUSEL ─────────────────────────────────────────
const CARD_W = 276;
const CARD_H = 216;

// Module-level scroll position cache — survives React unmount/remount cycles.
const _carouselScrollCache = new Map();

function Carousel({ items, onPlay, onDelete, onCardMenu, groupName }) {
  const scrollRef = useRef(null);
  const [scrollState, setScrollState] = useState({ left: false, right: true });

  const virtualizer = useVirtualizer({
    horizontal: true,
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => CARD_W,
    overscan: 3,
  });

  const checkScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const left = el.scrollLeft > 10;
    const right = el.scrollLeft < el.scrollWidth - el.clientWidth - 10;
    setScrollState((prev) =>
      prev.left === left && prev.right === right ? prev : { left, right }
    );
    if (groupName) _carouselScrollCache.set(groupName, el.scrollLeft);
  }, [groupName]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", checkScroll, { passive: true });
    checkScroll();
    const onWheel = (e) => {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.stopPropagation();
      window.scrollBy({ top: e.deltaY, behavior: "auto" });
      e.preventDefault();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("scroll", checkScroll);
      el.removeEventListener("wheel", onWheel);
    };
  }, [items.length, checkScroll]);

  useEffect(() => {
    if (!groupName) return;
    const el = scrollRef.current;
    if (!el) return;
    const saved = _carouselScrollCache.get(groupName);
    if (saved && saved > 0) el.scrollLeft = saved;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const scroll = (dir) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({
      left: dir === "right" ? el.clientWidth * 0.75 : -el.clientWidth * 0.75,
      behavior: "smooth",
    });
  };

  const totalWidth = virtualizer.getTotalSize();

  return (
    <div className="carousel-wrapper">
      {scrollState.left && (
        <button className="scroll-arrow left" onClick={() => scroll("left")}>
          ‹
        </button>
      )}
      <div className="carousel" ref={scrollRef}>
        <div
          style={{ width: totalWidth, position: "relative", height: CARD_H }}
        >
          {virtualizer.getVirtualItems().map((vItem) => (
            <div
              key={vItem.key}
              style={{
                position: "absolute",
                top: 0,
                left: vItem.start,
                width: 260,
                height: CARD_H,
              }}
            >
              <MediaCard
                item={items[vItem.index]}
                onPlay={onPlay}
                onDelete={onDelete}
                onCardMenu={onCardMenu}
              />
            </div>
          ))}
        </div>
      </div>
      {scrollState.right && items.length > 3 && (
        <button className="scroll-arrow right" onClick={() => scroll("right")}>
          ›
        </button>
      )}
    </div>
  );
}

// ─── CAROUSEL ROW ────────────────────────────────────────────────────────────
const CarouselRow = memo(function CarouselRow({
  groupName,
  items,
  onPlay,
  onEnrich,
  onDelete,
  onCardMenu,
}) {
  const [viewMode, setViewMode] = useState("show");

  useEffect(() => {
    if (onEnrich && groupName && items.length > 0)
      onEnrich(groupName, items[0].type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupName]);

  const seasons = [...new Set(items.map((i) => i.season_num || 1))].sort(
    (a, b) => a - b
  );
  const isMultiSeason = seasons.length > 1;
  const bySeason = seasons.reduce((acc, s) => {
    acc[s] = items.filter((i) => (i.season_num || 1) === s);
    return acc;
  }, {});

  return (
    <div className="media-row-container">
      <div className="row-header">
        <h2 className="row-title">{groupName}</h2>
        {isMultiSeason && (
          <div className="view-toggle">
            <button
              className={viewMode === "show" ? "active" : ""}
              onClick={() => setViewMode("show")}
            >
              By Show
            </button>
            <button
              className={viewMode === "season" ? "active" : ""}
              onClick={() => setViewMode("season")}
            >
              By Season
            </button>
          </div>
        )}
      </div>
      {viewMode === "show" || !isMultiSeason ? (
        <Carousel
          items={items}
          onPlay={onPlay}
          onDelete={onDelete}
          onCardMenu={onCardMenu}
          groupName={groupName}
        />
      ) : (
        <div className="season-stack">
          {seasons.map((s) => (
            <div key={s} className="season-block">
              <p className="season-label">Season {s}</p>
              <Carousel
                items={bySeason[s]}
                onPlay={onPlay}
                onDelete={onDelete}
                onCardMenu={onCardMenu}
                groupName={`${groupName}-s${s}`}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

// ─── SCAN STATUS BANNER ──────────────────────────────────────────────────────
function ScanStatusBanner({ scanState }) {
  if (!scanState) return null;
  const { phase, total, done, duration_queue_size, thumb_queue_size } =
    scanState;
  const isScanning = phase === "scanning";
  const isEnriching = phase === "complete" && duration_queue_size > 0;
  const isThumbing =
    phase === "complete" && duration_queue_size === 0 && thumb_queue_size > 0;
  if (!isScanning && !isEnriching && !isThumbing) return null;
  const pct = isScanning && total > 0 ? Math.round((done / total) * 100) : null;
  const label = isScanning
    ? `Indexing library… ${done} / ${total} files`
    : isEnriching
    ? `Getting durations… ${duration_queue_size} remaining`
    : `Generating previews… ${thumb_queue_size} remaining`;
  return (
    <div className="scan-banner">
      <div className="scan-banner-inner">
        <span className="scan-banner-label">{label}</span>
        <div className="scan-banner-track">
          {pct !== null ? (
            <div className="scan-banner-fill" style={{ width: `${pct}%` }} />
          ) : (
            <div className="scan-banner-fill scan-banner-fill--indeterminate" />
          )}
        </div>
      </div>
    </div>
  );
}

// ─── SETTINGS PAGE ───────────────────────────────────────────────────────────
function SettingsPage({ onClose }) {
  const [config, setConfig] = useState(null);
  const [players, setPlayers] = useState(null);
  const [posterStatus, setPosterStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [newDir, setNewDir] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [rescanning, setRescanning] = useState(false);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch(`${API}/api/config`)
      .then((r) => r.json())
      .then(setConfig);
    fetch(`${API}/api/players`)
      .then((r) => r.json())
      .then(setPlayers);
    fetch(`${API}/api/poster/status`)
      .then((r) => r.json())
      .then(setPosterStatus);
    fetch(`${API}/api/stats`)
      .then((r) => r.json())
      .then(setStats);
  }, []);

  const save = async () => {
    setSaving(true);
    await fetch(`${API}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const addDir = () => {
    if (!newDir.trim()) return;
    setConfig((c) => ({
      ...c,
      media_dirs: [...(c.media_dirs || []), newDir.trim()],
    }));
    setNewDir("");
  };

  const removeDir = (i) =>
    setConfig((c) => ({
      ...c,
      media_dirs: c.media_dirs.filter((_, idx) => idx !== i),
    }));

  const resetPosters = async () => {
    await fetch(`${API}/api/poster/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const s = await fetch(`${API}/api/poster/status`).then((r) => r.json());
    setPosterStatus(s);
  };

  const rescan = async () => {
    setRescanning(true);
    await fetch(`${API}/api/scan/trigger`, { method: "POST" });
    setTimeout(() => setRescanning(false), 1500);
  };

  if (!config)
    return (
      <div className="settings-loading">
        <div className="spinner" />
        <p>Loading settings...</p>
      </div>
    );

  const posterPct = posterStatus
    ? Math.round((posterStatus.enriched / (posterStatus.total || 1)) * 100)
    : 0;

  return (
    <div className="settings-overlay">
      <div className="settings-panel">
        <div className="settings-header">
          <h2>⚙️ Settings</h2>
          <button className="close-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="settings-body">
          {stats && (
            <div className="settings-section">
              <h3>Library</h3>
              <div className="stats-grid">
                <div className="stats-card">
                  <span className="stats-value">
                    {stats.total.toLocaleString()}
                  </span>
                  <span className="stats-label">Total Files</span>
                </div>
                <div className="stats-card">
                  <span className="stats-value">
                    {stats.movies.toLocaleString()}
                  </span>
                  <span className="stats-label">Movies</span>
                </div>
                <div className="stats-card">
                  <span className="stats-value">
                    {stats.episodes.toLocaleString()}
                  </span>
                  <span className="stats-label">
                    Episodes
                    {stats.shows > 0 && (
                      <em className="stats-sublabel">{stats.shows} shows</em>
                    )}
                  </span>
                </div>
                <div className="stats-card">
                  <span className="stats-value">
                    {stats.total_size_gb.toLocaleString()} GB
                  </span>
                  <span className="stats-label">Total Size</span>
                </div>
              </div>
              <div className="stats-secondary">
                {stats.shorts > 0 && (
                  <span className="stats-pill">
                    {stats.shorts} shorts/reels
                  </span>
                )}
                <span className="stats-pill">
                  {stats.posters} / {stats.total} posters
                </span>
                {stats.pending_duration > 0 && (
                  <span className="stats-pill stats-pill--pending">
                    ⏳ {stats.pending_duration} durations pending
                  </span>
                )}
                {stats.pending_thumbs > 0 && (
                  <span className="stats-pill stats-pill--pending">
                    🖼 {stats.pending_thumbs} thumbnails pending
                  </span>
                )}
              </div>
            </div>
          )}

          {players && (
            <div className="settings-section">
              <h3>System</h3>
              <div className="info-row">
                <span>Operating System</span>
                <strong>{players.os}</strong>
              </div>
              <div className="info-row">
                <span>Active Player</span>
                <strong>{players.current}</strong>
              </div>
              <div className="info-row">
                <span>Available Players</span>
                <strong>
                  {players.available.join(", ") || "None detected"}
                </strong>
              </div>
            </div>
          )}

          <div className="settings-section">
            <h3>TMDB Poster Integration</h3>
            <p className="settings-hint">
              Requires a <strong>Read Access Token (v4 auth)</strong> — not the
              v3 API Key. Get yours at{" "}
              <a
                href="https://www.themoviedb.org/settings/api"
                target="_blank"
                rel="noreferrer"
              >
                themoviedb.org → Settings → API
              </a>
              , copy the long token under <em>"Read Access Token (v4 auth)"</em>
              . Posters fetch automatically as you browse.
            </p>
            <div className="dir-add" style={{ marginTop: "0.6rem" }}>
              <input
                type={showKey ? "text" : "password"}
                placeholder="eyJhbGciOiJIUzI1NiJ9… (Read Access Token)"
                value={config.tmdb_api_key || ""}
                onChange={(e) =>
                  setConfig((c) => ({ ...c, tmdb_api_key: e.target.value }))
                }
                className="settings-input"
              />
              <button
                className="add-btn"
                style={{
                  background: "var(--surface2)",
                  color: "var(--text)",
                  border: "1px solid var(--border)",
                }}
                onClick={() => setShowKey((v) => !v)}
              >
                {showKey ? "Hide" : "Show"}
              </button>
            </div>
            {config.tmdb_api_key && config.tmdb_api_key.length < 100 && (
              <div className="settings-warning">
                ⚠️ This looks like a v3 API Key (too short). TMDB will return
                401 errors. Please paste the <strong>Read Access Token</strong>{" "}
                instead — it starts with "eyJ" and is much longer.
              </div>
            )}
            {posterStatus && (
              <div className="poster-status">
                <div className="poster-status-row">
                  <span>
                    {posterStatus.enriched} / {posterStatus.total} posters
                    fetched
                  </span>
                  <button className="poster-reset-btn" onClick={resetPosters}>
                    Re-fetch all
                  </button>
                </div>
                <div className="poster-progress-track">
                  <div
                    className="poster-progress-fill"
                    style={{ width: `${posterPct}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          <div className="settings-section">
            <h3>Preferred Player</h3>
            <select
              className="settings-select"
              value={config.preferred_player || "auto"}
              onChange={(e) =>
                setConfig((c) => ({ ...c, preferred_player: e.target.value }))
              }
            >
              <option value="auto">Auto (best available)</option>
              {players?.available.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>

          <div className="settings-section">
            <h3>Media Directories</h3>
            <div className="dir-list">
              {(config.media_dirs || []).map((dir, i) => (
                <div key={i} className="dir-item">
                  <span className="dir-path">{dir}</span>
                  <button className="dir-remove" onClick={() => removeDir(i)}>
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <div className="dir-add">
              <input
                type="text"
                placeholder="Add directory path..."
                value={newDir}
                onChange={(e) => setNewDir(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addDir()}
                className="settings-input"
              />
              <button className="add-btn" onClick={addDir}>
                Add
              </button>
            </div>
          </div>

          <div className="settings-section">
            <h3>Hero Banner</h3>
            <div className="info-row">
              <span>Featured items (auto-rotates every 8s)</span>
              <input
                type="number"
                min={1}
                max={20}
                value={config.featured_count || 5}
                onChange={(e) =>
                  setConfig((c) => ({
                    ...c,
                    featured_count: parseInt(e.target.value),
                  }))
                }
                className="settings-number"
              />
            </div>
            <p className="settings-hint" style={{ marginTop: "0.5rem" }}>
              Hero items are randomly selected from your most recent movies and
              shows — never the same order twice.
            </p>
          </div>

          <div className="settings-section">
            <h3>Content</h3>
            <div className="info-row">
              <div>
                <span>Index shorts &amp; reels</span>
                <p className="settings-hint" style={{ marginTop: "0.2rem" }}>
                  {config.index_shorts_reels === false
                    ? "Shorts and reels are hidden. Re-enable and save to rescan them."
                    : "Disable to hide all short clips and reels from the library."}
                </p>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={config.index_shorts_reels !== false}
                  onChange={(e) =>
                    setConfig((c) => ({
                      ...c,
                      index_shorts_reels: e.target.checked,
                    }))
                  }
                />
                <span className="toggle-slider" />
              </label>
            </div>
            {config.index_shorts_reels === false && (
              <div className="settings-warning">
                ⚠️ Saving will permanently remove all shorts and reels from the
                library. They will be re-indexed if you re-enable this setting.
              </div>
            )}
          </div>
        </div>

        <div className="settings-footer">
          <button className="save-btn" onClick={save} disabled={saving}>
            {saving ? "Saving..." : saved ? "✓ Saved!" : "Save Changes"}
          </button>
          <button className="rescan-btn" onClick={rescan} disabled={rescanning}>
            {rescanning ? "⏳ Scan started…" : "🔄 Rescan Library"}
          </button>
          <p className="settings-note">
            Rescan picks up new files instantly. Directory changes also trigger
            a scan automatically on save.
          </p>
        </div>
      </div>
    </div>
  );
}

// ─── CONTINUE WATCHING ROW ───────────────────────────────────────────────────
function ContinueWatchingRow({ items, onPlay, onDelete, onCardMenu }) {
  if (!items.length) return null;
  return (
    <div className="media-row-container continue-watching-row">
      <div className="row-header">
        <h2 className="row-title row-title--continue">▶ Continue Watching</h2>
      </div>
      <Carousel
        items={items}
        onPlay={onPlay}
        onDelete={onDelete}
        onCardMenu={onCardMenu}
        groupName="__continue_watching__"
      />
    </div>
  );
}

// ─── ROW MEASURER ────────────────────────────────────────────────────────────
const RowMeasurer = memo(function RowMeasurer({
  rowIndex,
  onHeight,
  style,
  children,
}) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let rafId = null;
    const ro = new ResizeObserver(([entry]) => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const h = entry.contentRect.height;
        if (h > 0) onHeight(rowIndex, h);
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [rowIndex, onHeight]);
  return (
    <div ref={ref} style={style}>
      {children}
    </div>
  );
});

// ─── VIRTUALISED ROW LIST ────────────────────────────────────────────────────
const ROW_H = 380;

function RowVirtualizer({ rows, onPlay, onEnrich, onDelete, onCardMenu }) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const parentRef = useRef(null);
  const offsetTopRef = useRef(0);
  const measuredHeightsRef = useRef({});
  const offsetsRef = useRef({ offsets: [], total: safeRows.length * ROW_H });
  const rowCountRef = useRef(safeRows.length);
  rowCountRef.current = safeRows.length;
  const rangeTimerRef = useRef(null);
  const [totalHeight, setTotalHeight] = useState(() => safeRows.length * ROW_H);
  const OVERSCAN = 4;

  const recomputeOffsets = useCallback(() => {
    const count = rowCountRef.current;
    const offsets = [];
    let acc = 0;
    for (let i = 0; i < count; i++) {
      offsets.push(acc);
      acc += measuredHeightsRef.current[i] ?? ROW_H;
    }
    return { offsets, total: acc };
  }, []);

  const computeRange = useCallback(() => {
    const scrollY = window.scrollY;
    const viewportH = window.innerHeight;
    const relScroll = scrollY - offsetTopRef.current;
    const { offsets } = offsetsRef.current;
    const len = rowCountRef.current;
    if (!len) return { start: 0, end: -1 };
    let start = 0;
    for (let i = 0; i < len; i++) {
      const bottom = offsets[i] + (measuredHeightsRef.current[i] ?? ROW_H);
      if (bottom > relScroll) {
        start = i;
        break;
      }
      start = i;
    }
    let end = start;
    for (let i = start; i < len; i++) {
      end = i;
      if (offsets[i] >= relScroll + viewportH) break;
    }
    return {
      start: Math.max(0, start - OVERSCAN),
      end: Math.min(len - 1, end + OVERSCAN),
    };
  }, []);

  const onRowHeight = useCallback(
    (index, height) => {
      if (measuredHeightsRef.current[index] === height) return;
      measuredHeightsRef.current[index] = height;
      const result = recomputeOffsets();
      offsetsRef.current = result;
      setTotalHeight(result.total);
      if (rangeTimerRef.current) clearTimeout(rangeTimerRef.current);
      rangeTimerRef.current = setTimeout(() => {
        setRange(computeRange());
      }, 50);
    },
    [recomputeOffsets, computeRange]
  );

  useLayoutEffect(() => {
    measuredHeightsRef.current = {};
    const result = recomputeOffsets();
    offsetsRef.current = result;
    setTotalHeight(result.total);
  }, [safeRows.length, recomputeOffsets]);

  useLayoutEffect(() => {
    if (parentRef.current) {
      offsetTopRef.current =
        parentRef.current.getBoundingClientRect().top + window.scrollY;
    }
  }, [safeRows.length]);

  const [range, setRange] = useState(() => computeRange());
  const rangeRef = useRef(range);
  rangeRef.current = range;

  useEffect(() => {
    let rafId = null;
    const onScroll = () => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const next = computeRange();
        if (
          next.start !== rangeRef.current.start ||
          next.end !== rangeRef.current.end
        ) {
          setRange(next);
        }
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (rafId) cancelAnimationFrame(rafId);
      if (rangeTimerRef.current) clearTimeout(rangeTimerRef.current);
    };
  }, [computeRange]);

  useLayoutEffect(() => {
    setRange(computeRange());
  }, [safeRows.length, computeRange]);

  if (safeRows.length === 0) return null;

  const clampedStart = Math.max(0, Math.min(range.start, safeRows.length - 1));
  const clampedEnd = Math.max(-1, Math.min(range.end, safeRows.length - 1));
  const { offsets } = offsetsRef.current;
  const visibleRows = [];
  for (let i = clampedStart; i <= clampedEnd; i++) visibleRows.push(i);
  const viewportStart = Math.max(0, clampedStart + OVERSCAN);
  const viewportEnd = Math.min(safeRows.length - 1, clampedEnd - OVERSCAN);

  return (
    <div ref={parentRef} style={{ position: "relative", height: totalHeight }}>
      {visibleRows.map((i) => {
        const [groupName, items] = safeRows[i];
        const isOverscan = i < viewportStart || i > viewportEnd;
        return (
          <RowMeasurer
            key={groupName}
            rowIndex={i}
            onHeight={onRowHeight}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${offsets[i] ?? i * ROW_H}px)`,
            }}
          >
            <CarouselRow
              groupName={groupName}
              items={items}
              onPlay={onPlay}
              onEnrich={onEnrich}
              onDelete={onDelete}
              onCardMenu={onCardMenu}
            />
          </RowMeasurer>
        );
      })}
    </div>
  );
}

// ─── BACK TO TOP ─────────────────────────────────────────────────────────────
function BackToTop() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 600);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  if (!visible) return null;
  return (
    <button
      className="back-to-top"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      title="Back to top"
      aria-label="Scroll back to top"
    >
      ↑
    </button>
  );
}

// ─── FIRST RUN SCREEN ────────────────────────────────────────────────────────
function FirstRunScreen({ onComplete }) {
  const [step, setStep] = useState(1);
  const [dirs, setDirs] = useState([]);
  const [input, setInput] = useState("");
  const [pathInfo, setPathInfo] = useState(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [warnings, setWarnings] = useState([]);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (!input.trim()) {
      setPathInfo(null);
      return;
    }
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setChecking(true);
      fetch(`${API}/api/setup/browse`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: input.trim() }),
      })
        .then((r) => r.json())
        .then(setPathInfo)
        .catch(() => setPathInfo(null))
        .finally(() => setChecking(false));
    }, 400);
  }, [input]);

  const addDir = () => {
    const trimmed = input.trim();
    if (!trimmed || dirs.includes(trimmed)) return;
    setDirs((d) => [...d, trimmed]);
    setInput("");
    setPathInfo(null);
  };

  const removeDir = (i) => setDirs((d) => d.filter((_, idx) => idx !== i));

  const finish = async () => {
    if (!dirs.length) return;
    setSubmitting(true);
    const res = await fetch(`${API}/api/setup/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ media_dirs: dirs }),
    }).then((r) => r.json());
    setWarnings(res.warnings || []);
    setSubmitting(false);
    setStep(3);
  };

  if (step === 1)
    return (
      <div className="firstrun-overlay">
        <div className="firstrun-card">
          <div className="firstrun-logo">
            <span className="brand-icon" style={{ fontSize: "3rem" }}>
              🍿
            </span>
            <span
              className="brand-name"
              style={{
                fontFamily: "'Bebas Neue', sans-serif",
                fontSize: "3rem",
                letterSpacing: "4px",
                color: "var(--accent)",
              }}
            >
              Localflix
            </span>
          </div>
          <h1 className="firstrun-title">Welcome</h1>
          <p className="firstrun-subtitle">
            Your personal media browser for movies and shows. Let's point
            Localflix at your media collection to get started.
          </p>
          <button className="firstrun-btn-primary" onClick={() => setStep(2)}>
            Get Started →
          </button>
        </div>
      </div>
    );

  if (step === 2)
    return (
      <div className="firstrun-overlay">
        <div className="firstrun-card firstrun-card--wide">
          <div className="firstrun-step-header">
            <span className="firstrun-step-label">Step 1 of 1</span>
            <h1 className="firstrun-title">Where are your movies?</h1>
            <p className="firstrun-subtitle">
              Add the folders where your media files are stored. You can add
              more later in Settings.
            </p>
          </div>
          <div className="firstrun-input-row">
            <div className="firstrun-input-wrap">
              <input
                className={`settings-input firstrun-path-input ${
                  pathInfo
                    ? pathInfo.exists
                      ? "input--valid"
                      : "input--invalid"
                    : ""
                }`}
                type="text"
                placeholder={
                  navigator.platform.includes("Win")
                    ? "e.g. D:\\Movies or E:\\Media"
                    : "/media/movies or /Volumes/HDD/Films"
                }
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addDir()}
                autoFocus
              />
              {input && (
                <span className="firstrun-path-status">
                  {checking ? "⏳" : pathInfo?.exists ? "✓" : "✗"}
                </span>
              )}
            </div>
            <button
              className="firstrun-btn-add"
              onClick={addDir}
              disabled={!input.trim()}
            >
              Add
            </button>
          </div>
          {pathInfo && !pathInfo.exists && input && (
            <p className="firstrun-path-warning">
              This path doesn't exist yet. You can still add it — useful for
              external drives that aren't always connected.
            </p>
          )}
          {pathInfo?.subdirs?.length > 0 && (
            <div className="firstrun-suggestions">
              <p className="firstrun-suggestions-label">Subdirectories:</p>
              <div className="firstrun-suggestions-list">
                {pathInfo.subdirs.slice(0, 8).map((d) => (
                  <button
                    key={d}
                    className="firstrun-suggestion-chip"
                    onClick={() => setInput(d)}
                  >
                    {d.split(/[\\/]/).pop()}/
                  </button>
                ))}
              </div>
            </div>
          )}
          {dirs.length > 0 && (
            <div className="firstrun-dirs-list">
              {dirs.map((d, i) => (
                <div key={i} className="firstrun-dir-item">
                  <span className="firstrun-dir-icon">📁</span>
                  <span className="firstrun-dir-path">{d}</span>
                  <button className="dir-remove" onClick={() => removeDir(i)}>
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="firstrun-actions">
            <button className="firstrun-btn-ghost" onClick={() => setStep(1)}>
              ← Back
            </button>
            <button
              className="firstrun-btn-primary"
              disabled={!dirs.length || submitting}
              onClick={finish}
            >
              {submitting
                ? "Setting up…"
                : `Start Scanning ${
                    dirs.length > 0
                      ? `(${dirs.length} folder${dirs.length > 1 ? "s" : ""})`
                      : ""
                  }`}
            </button>
          </div>
        </div>
      </div>
    );

  return (
    <div className="firstrun-overlay">
      <div className="firstrun-card">
        <div className="firstrun-done-icon">✓</div>
        <h1 className="firstrun-title">All set!</h1>
        <p className="firstrun-subtitle">
          Localflix is scanning your library in the background. Your movies and
          shows will appear as they're indexed.
        </p>
        {warnings.length > 0 && (
          <div
            className="settings-warning"
            style={{ marginBottom: "1.5rem", textAlign: "left" }}
          >
            ⚠️ These paths couldn't be found — check they're correct or that the
            drive is connected:
            <ul style={{ marginTop: "0.4rem", paddingLeft: "1.2rem" }}>
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        <button className="firstrun-btn-primary" onClick={onComplete}>
          Open Localflix →
        </button>
      </div>
    </div>
  );
}

// ─── DELETE CONFIRM MODAL ────────────────────────────────────────────────────
function DeleteConfirmModal({ item, onConfirm, onCancel }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="delete-overlay" onClick={onCancel}>
      <div className="delete-modal" onClick={(e) => e.stopPropagation()}>
        <div className="delete-modal-icon">🗑️</div>
        <h3 className="delete-modal-title">Remove from library?</h3>
        <p className="delete-modal-name">{item.clean_name || item.name}</p>
        <p className="delete-modal-filename">{item.name}</p>
        <p className="delete-modal-hint">
          File moves to <code>~/.localflix/trash/</code> and is removed from the
          library. You can recover it from there manually.
        </p>
        <div className="delete-modal-actions">
          <button className="delete-modal-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button className="delete-modal-confirm" onClick={onConfirm}>
            Remove File
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── MAIN APP ────────────────────────────────────────────────────────────────
function App() {
  const [media, setMedia] = useState([]);
  const [featured, setFeatured] = useState([]);
  const [continueWatching, setContinue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [firstRun, setFirstRun] = useState(false);
  const [filter, setFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [isDark, setIsDark] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [toast, setToast] = useState(null);
  const [scanState, setScanState] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [appConfig, setAppConfig] = useState({
    index_shorts_reels: false,
    featured_count: 5,
  });

  const enrichedGroupsRef = useRef(new Set());
  const requestEnrich = useCallback((groupName, type) => {
    if (!groupName || enrichedGroupsRef.current.has(groupName)) return;
    enrichedGroupsRef.current.add(groupName);
    fetch(`${API}/api/enrich/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ groups: [{ clean_name: groupName, type }] }),
    }).catch(() => {});
  }, []);

  // ── loadMedia ───────────────────────────────────────────────────────────────
  // Approach C fix: load ALL items in one request. At 306 items (~50KB JSON)
  // this is trivially fast. Search and filter operate on client-side state
  // only — no server round-trips, no pagination, no reset-wipes-items bug.
  const loadMedia = useCallback(async () => {
    try {
      const data = await fetch(`${API}/api/media`).then((r) => r.json());
      // /api/media without ?page= returns a flat array
      setMedia(Array.isArray(data) ? data : data.items ?? []);
    } catch (err) {
      console.error("loadMedia failed:", err);
    }
  }, []);

  const loadFeatured = useCallback(
    () =>
      fetch(`${API}/api/featured`)
        .then((r) => r.json())
        .then(setFeatured),
    []
  );
  const loadContinue = useCallback(
    () =>
      fetch(`${API}/api/continue_watching`)
        .then((r) => r.json())
        .then(setContinue),
    []
  );
  const loadConfig = useCallback(
    () =>
      fetch(`${API}/api/config`)
        .then((r) => r.json())
        .then(setAppConfig),
    []
  );

  useEffect(() => {
    fetch(`${API}/api/setup/status`)
      .then((r) => r.json())
      .then((s) => {
        if (s.first_run) {
          setFirstRun(true);
          setLoading(false);
        } else {
          Promise.all([
            loadMedia(),
            loadFeatured(),
            loadContinue(),
            loadConfig(),
          ])
            .catch((err) => console.error("Load failed:", err))
            .finally(() => setLoading(false));
        }
      })
      .catch(() => {
        Promise.all([
          loadMedia(),
          loadFeatured(),
          loadContinue(),
          loadConfig(),
        ]).finally(() => setLoading(false));
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // NOTE: No effect that re-fetches on [loadMedia] / [searchQuery, filter].
  // Search and filter are purely client-side now (Approach C).
  // loadMedia() is called once on mount (via setup/status effect) and on
  // SSE events (library_updated, config_updated) only.

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const deleteMedia = useCallback(async () => {
    if (!deleteTarget) return;
    const { path } = deleteTarget;
    setDeleteTarget(null);
    setMedia((prev) => prev.filter((i) => i.path !== path));
    setContinue((prev) => prev.filter((i) => i.path !== path));
    setFeatured((prev) => prev.filter((i) => i.path !== path));
    try {
      const r = await fetch(`${API}/api/media`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (!r.ok) {
        const err = await r.json();
        throw new Error(err.error || "Delete failed");
      }
      showToast("🗑 File removed");
    } catch (err) {
      showToast(`❌ ${err.message}`);
      loadMedia();
      loadFeatured();
      loadContinue();
    }
  }, [deleteTarget, loadMedia, loadFeatured, loadContinue]); // eslint-disable-line react-hooks/exhaustive-deps

  const openCtxMenu = useCallback((e, item) => {
    setCtxMenu({ x: e.clientX, y: e.clientY, item });
  }, []);

  const markWatched = useCallback(async (item) => {
    const dur = item.duration_sec > 0 ? item.duration_sec : 1000000;
    const patch = { progress_pct: 100, completed: 1 };
    setMedia((prev) =>
      prev.map((m) => (m.path === item.path ? { ...m, ...patch } : m))
    );
    setContinue((prev) => prev.filter((m) => m.path !== item.path));
    setFeatured((prev) =>
      prev.map((m) => (m.path === item.path ? { ...m, ...patch } : m))
    );
    await fetch(`${API}/api/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: item.path,
        position_sec: dur,
        duration_sec: dur,
      }),
    });
    showToast("✓ Marked as watched");
  }, []);

  const markUnwatched = useCallback(async (item) => {
    const patch = { progress_pct: 0, completed: 0, position_sec: 0 };
    setMedia((prev) =>
      prev.map((m) => (m.path === item.path ? { ...m, ...patch } : m))
    );
    setContinue((prev) => prev.filter((m) => m.path !== item.path));
    setFeatured((prev) =>
      prev.map((m) => (m.path === item.path ? { ...m, ...patch } : m))
    );
    await fetch(`${API}/api/progress/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: item.path }),
    });
    showToast("↺ Progress cleared");
  }, []);

  useEffect(() => {
    let interval = null;
    const poll = () => {
      fetch(`${API}/api/scan/status`)
        .then((r) => r.json())
        .then((state) => {
          setScanState(state);
          const busy =
            state.phase === "scanning" || state.duration_queue_size > 0;
          if (!busy && interval) {
            clearInterval(interval);
            interval = null;
          }
        })
        .catch(() => {});
    };
    poll();
    interval = setInterval(poll, 2000);
    return () => {
      if (interval) clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    let es = null;
    let reconnectTimer = null;
    let alive = true;
    const connect = () => {
      es = new EventSource(`${API}/api/events`);

      es.addEventListener("library_updated", () => {
        loadMedia();
        loadFeatured();
        loadContinue();
      });

      es.addEventListener("config_updated", () => {
        loadFeatured();
        loadConfig();
        loadMedia();
      });

      es.addEventListener("media_enriched", (e) => {
        const { path, duration_sec, duration, type } = JSON.parse(e.data);
        const patch = type
          ? { duration_sec, duration, type }
          : { duration_sec, duration };
        setMedia((prev) =>
          prev.map((item) =>
            item.path === path ? { ...item, ...patch } : item
          )
        );
        setFeatured((prev) =>
          prev.map((item) =>
            item.path === path ? { ...item, ...patch } : item
          )
        );
      });

      es.addEventListener("media_deleted", (e) => {
        const { path } = JSON.parse(e.data);
        setMedia((prev) => prev.filter((i) => i.path !== path));
        setContinue((prev) => prev.filter((i) => i.path !== path));
        setFeatured((prev) => prev.filter((i) => i.path !== path));
      });

      es.addEventListener("poster_updated", (e) => {
        const { path, poster_url, source: src } = JSON.parse(e.data);
        setMedia((prev) =>
          prev.map((item) =>
            item.path === path
              ? { ...item, poster_url, poster_source: src }
              : item
          )
        );
        setFeatured((prev) =>
          prev.map((item) =>
            item.path === path
              ? { ...item, poster_url, poster_source: src }
              : item
          )
        );
      });

      es.addEventListener("progress_updated", (e) => {
        const { path, progress_pct, completed } = JSON.parse(e.data);
        setMedia((prev) =>
          prev.map((item) =>
            item.path === path ? { ...item, progress_pct, completed } : item
          )
        );
        setContinue((prev) => {
          const exists = prev.find((i) => i.path === path);
          if (!exists && progress_pct >= 5 && !completed) {
            loadContinue();
            return prev;
          }
          return prev
            .map((item) =>
              item.path === path ? { ...item, progress_pct, completed } : item
            )
            .filter((item) => item.progress_pct < 90 && !item.completed);
        });
      });

      es.onerror = () => {
        es.close();
        if (!alive) return;
        reconnectTimer = setTimeout(() => {
          if (alive) connect();
        }, 3000);
      };
    };
    connect();
    return () => {
      alive = false;
      clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [loadMedia, loadFeatured, loadContinue, loadConfig]);

  useEffect(() => {
    if (
      !appConfig.index_shorts_reels &&
      (filter === "short_video" || filter === "reel")
    ) {
      setFilter("all");
    }
  }, [appConfig.index_shorts_reels, filter]);

  useEffect(() => {
    document.body.className = isDark ? "dark-theme" : "light-theme";
  }, [isDark]);

  const playVideo = useCallback((filepath) => {
    setMedia((prev) => {
      const item = prev.find((i) => i.path === filepath);
      if (item && !item.completed) {
        setContinue((cw) => {
          const already = cw.find((i) => i.path === filepath);
          if (!already)
            return [{ ...item, progress_pct: item.progress_pct || 1 }, ...cw];
          return cw;
        });
      }
      return prev;
    });
    fetch(`${API}/api/play`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: filepath }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.resumed_from) {
          const mins = Math.floor(data.resumed_from / 60);
          const secs = Math.floor(data.resumed_from % 60)
            .toString()
            .padStart(2, "0");
          showToast(`▶ Resuming from ${mins}:${secs}`);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredMedia = useMemo(() => {
    return media.filter((m) => {
      if (!appConfig.index_shorts_reels) {
        if (m.type === "short_video" || m.type === "reel") return false;
        if (m.duration_sec > 0 && m.duration_sec < 600) return false;
      }
      if (filter !== "all" && m.type !== filter) return false;
      // Client-side search (Approach C) — no server round-trip needed
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const name = (m.name || "").toLowerCase();
        const cleanName = (m.clean_name || "").toLowerCase();
        if (!name.includes(q) && !cleanName.includes(q)) return false;
      }
      return true;
    });
  }, [media, filter, searchQuery, appConfig.index_shorts_reels]);

  const groupedMedia = useMemo(() => {
    const groups = filteredMedia.reduce((acc, item) => {
      let groupName;
      if (item.duration_sec > 0 && item.duration_sec < 180) {
        groupName = "Reels";
      } else if (item.duration_sec >= 180 && item.duration_sec < 600) {
        groupName = "Shorts";
      } else if (item.type === "episode") {
        groupName = item.clean_name || "Uncategorized Shows";
      } else if (item.type === "short_video") {
        groupName = "Shorts";
      } else if (item.type === "reel") {
        groupName = "Reels";
      } else {
        groupName = "Movies";
      }
      if (!acc[groupName]) acc[groupName] = [];
      acc[groupName].push(item);
      return acc;
    }, {});

    for (const [name, items] of Object.entries(groups)) {
      if (name !== "Reels" && name !== "Shorts" && name !== "Movies") {
        items.sort((a, b) => {
          const s = (a.season_num || 1) - (b.season_num || 1);
          return s !== 0 ? s : (a.episode_num || 0) - (b.episode_num || 0);
        });
      }
    }
    return groups;
  }, [filteredMedia]);

  if (loading)
    return (
      <div className="loading-state">
        <div className="spinner" />
        <p>Starting up...</p>
      </div>
    );

  if (firstRun)
    return (
      <FirstRunScreen
        onComplete={() => {
          setFirstRun(false);
          setLoading(true);
          Promise.all([
            loadMedia(),
            loadFeatured(),
            loadContinue(),
            loadConfig(),
          ]).finally(() => setLoading(false));
        }}
      />
    );

  return (
    <div className="app-root">
      <ScanStatusBanner scanState={scanState} />

      <header className="app-header">
        <div className="header-inner">
          <div className="header-brand">
            <span className="brand-icon">🍿</span>
            <span className="brand-name">Localflix</span>
          </div>
          <div className="header-controls">
            <div className="search-wrap">
              <span className="search-icon">🔍</span>
              <input
                type="text"
                placeholder="Search titles..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="search-bar"
              />
            </div>
            <div className="filters">
              {[
                { key: "all", label: "All" },
                { key: "movie", label: "Movies" },
                { key: "episode", label: "Shows" },
                ...(appConfig.index_shorts_reels
                  ? [
                      { key: "short_video", label: "Shorts" },
                      { key: "reel", label: "Reels" },
                    ]
                  : []),
              ].map((f) => (
                <button
                  key={f.key}
                  className={filter === f.key ? "active" : ""}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="header-actions">
              <button
                className="icon-btn"
                onClick={() => setIsDark(!isDark)}
                title="Toggle theme"
              >
                {isDark ? "☀️" : "🌙"}
              </button>
              <button
                className="icon-btn"
                onClick={() => setShowSettings(true)}
                title="Settings"
              >
                ⚙️
              </button>
            </div>
          </div>
        </div>
      </header>

      {!searchQuery && filter === "all" && featured.length > 0 && (
        <HeroBanner items={featured} onPlay={playVideo} />
      )}

      <main className="main-content">
        {Object.keys(groupedMedia).length > 0 ? (
          <div className="netflix-layout">
            {!searchQuery && filter === "all" && (
              <ContinueWatchingRow
                items={continueWatching}
                onPlay={playVideo}
                onDelete={setDeleteTarget}
                onCardMenu={openCtxMenu}
              />
            )}
            <RowVirtualizer
              rows={Object.entries(groupedMedia) ?? []}
              onPlay={playVideo}
              onEnrich={requestEnrich}
              onDelete={setDeleteTarget}
              onCardMenu={openCtxMenu}
            />
          </div>
        ) : (
          <div className="no-results">
            <p>🎬</p>
            <p>
              No media found{searchQuery ? ` matching "${searchQuery}"` : ""}
            </p>
            {searchQuery && (
              <button
                onClick={() => {
                  setSearchInput("");
                  setSearchQuery("");
                }}
              >
                Clear Search
              </button>
            )}
          </div>
        )}
      </main>

      {toast && <div className="toast">{toast}</div>}
      {showSettings && <SettingsPage onClose={() => setShowSettings(false)} />}
      {deleteTarget && (
        <DeleteConfirmModal
          item={deleteTarget}
          onConfirm={deleteMedia}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          item={ctxMenu.item}
          onClose={() => setCtxMenu(null)}
          onMarkWatched={markWatched}
          onMarkUnwatched={markUnwatched}
          onDelete={(item) => setDeleteTarget(item)}
        />
      )}
      <BackToTop />
    </div>
  );
}

export default App;
