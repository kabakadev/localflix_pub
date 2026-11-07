import requests

BASE_URL = "http://localhost:5000/api/debug/parse"

TEST_CASES = [
    # --- EPISODES ---
    {
        "name": "SxxExx standard",
        "filename": "The.Boys.S03E06.mkv",
        "duration": 3600,
        "size": 580,
        "expected_type": "episode",
        "expected_episode": 6,
    },
    {
        "name": "Season Ep format",
        "filename": "The Boys - Season 3 Ep 6.mkv",
        "duration": 3500,
        "size": 500,
        "expected_type": "episode",
        "expected_episode": 6,
    },

    # --- ANIME ---
    {
        "name": "Anime valid",
        "filename": "[SubsPlease] One Piece - 1047 (1080p).mkv",
        "duration": 1400,
        "size": 300,
        "expected_type": "episode",
        "expected_episode": 1047,
    },
    {
        "name": "Anime invalid (no signals)",
        "filename": "One Piece - 1047.mkv",
        "duration": 1400,
        "size": 300,
        "expected_type": "episode",  # fallback
        "expected_episode": 0,       # should NOT detect episode
    },

    # --- MOVIES ---
    {
        "name": "Movie with year",
        "filename": "Inception.2010.1080p.mkv",
        "duration": 5400,
        "size": 1400,
        "expected_type": "movie",
        "expected_episode": 0,
        
    },
    {
        "name": "Movie dash year",
        "filename": "Casino.Royale - 2006.mkv",
        "duration": 5400,
        "size": 1200,
        "expected_type": "movie",
        "expected_episode": 0,
    },

    # --- EDGE CASES ---
    {
        "name": "Weird formatting",
        "filename": "...The..Boys...S03E06...",
        "duration": 3600,
        "size": 600,
        "expected_type": "episode",
        "expected_episode": 6,
    },
    # --- NUMBERED MOVIES (should NOT be mistaken for episodes) ---
    {
        "name": "Numbered movie - Iron Man 1",
        "filename": "Iron Man 1.mkv",
        "duration": 7200,
        "size": 1300,
        "expected_type": "movie",
        "expected_episode": 0,
    },
    {
        "name": "Numbered movie - John Wick 3",
        "filename": "John Wick 3 (Final Chapter).mp4",
        "duration": 6840,
        "size": 1100,
        "expected_type": "movie",
        "expected_episode": 0,
    },
    {
        "name": "Numbered movie - Avengers Infinity War",
        "filename": "Avengers Infinity War.mp4",
        "duration": 8940,
        "size": 2000,
        "expected_type": "movie",
        "expected_episode": 0,
    },
    {
        "name": "Numbered movie - Inception (no year)",
        "filename": "Inception.mkv",
        "duration": 8880,
        "size": 1400,
        "expected_type": "movie",
        "expected_episode": 0,
    },
    {
        "name": "Numbered movie - Fast 9",
        "filename": "Fast 9.mkv",
        "duration": 7620,
        "size": 1600,
        "expected_type": "movie",
        "expected_episode": 0,
    },

    # --- ANIME EDGE CASES (should detect episode correctly) ---
    {
        "name": "Anime episode 1 (valid)",
        "filename": "[SubsPlease] One Piece - 1 (1080p).mkv",
        "duration": 1440,
        "size": 200,
        "expected_type": "episode",
        "expected_episode": 1,
    },
    {
        "name": "Anime episode 1047 (valid)",
        "filename": "[SubsPlease] One Piece - 1047 (1080p).mkv",
        "duration": 1440,
        "size": 200,
        "expected_type": "episode",
        "expected_episode": 1047,
    },
    
]


def run_tests():
    print("🚀 Running parser tests...\n")
    passed = 0

    for test in TEST_CASES:
        params = {
            "filename": test["filename"],
            "duration": test["duration"],
            "size": test["size"]
        }

        res = requests.get(BASE_URL, params=params)
        data = res.json()

        actual_type = data["classify"]["type"]
        actual_ep = data["parse"]["episode_num"]

        ok = (
            actual_type == test["expected_type"] and
            actual_ep == test["expected_episode"]
        )

        status = "✅ PASS" if ok else "❌ FAIL"

        print(f"{status} — {test['name']}")
        print(f"   File: {test['filename']}")
        print(f"   Expected: type={test['expected_type']} ep={test['expected_episode']}")
        print(f"   Got:      type={actual_type} ep={actual_ep}")
        print(f"   Signals:  {data['classify']['signals']}\n")

        if ok:
            passed += 1

    print(f"🎯 Result: {passed}/{len(TEST_CASES)} tests passed")


if __name__ == "__main__":
    run_tests()