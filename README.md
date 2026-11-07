# Localflix

Localflix is a local media browser and player. The application uses a React
frontend, a Python Flask backend, SQLite for the media cache, and local
`ffmpeg`, `ffprobe`, and `mpv` tools.

This repository is the simplified public edition. It does not contain the
private licensing and activation implementation.

## Requirements

- Python 3.10+
- Node.js and npm
- `ffmpeg` and `ffprobe`
- `mpv` for playback

## Backend

Install the Python dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
```

Run the backend from the `backend` directory:

```bash
cd backend
python server.py
```

The backend listens on `http://localhost:5000`.

Runtime configuration is local to each installation. Copy or create
`backend/config.json` as needed; it is ignored by Git because it may contain
local paths and third-party API credentials.

## Frontend

```bash
cd frontend-v2
npm ci
npm run dev
```

The frontend expects the backend at `http://localhost:5000` during development.
Use `npm run build` to create a production build.

## Privacy and credentials

Do not commit API keys, access tokens, private keys, local configuration files,
databases, generated thumbnails, or build artifacts. Use local ignored
configuration for optional metadata providers.
