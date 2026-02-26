# Media Vault — Project State

> **Read this file at the start of every session.** It is the single source of truth for where the project stands.

---

## 1. Overall Project Goal

Build **Media Vault**: a self-hosted web application deployed via Docker that lets users download the highest-quality YouTube video/audio and Spotify audio tracks using `yt-dlp` and `spotdl`. The app exposes a clean single-page UI served by a Python FastAPI backend; downloaded files persist on the host machine via a Docker volume mount.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn (async) |
| Download Engines | yt-dlp (YouTube), spotdl (Spotify) |
| Media Processing | ffmpeg (merges video + audio streams) |
| Frontend | Vanilla HTML + JavaScript, Tailwind CSS (CDN) |
| Infrastructure | Docker, Docker Compose |

---

## 3. Target Folder Structure

```
/media-vault
├── docker-compose.yml       # Orchestration: ports, volumes, env
├── Dockerfile               # Image: Python 3.11-slim + ffmpeg + pip deps
├── requirements.txt         # Python dependencies
├── project_state.md         # ← THIS FILE (project memory)
└── /app
    ├── main.py              # FastAPI application (background tasks, API routes)
    ├── /static
    │   └── index.html       # Single-page frontend (Tailwind CSS)
    └── /downloads           # Volume mount → host ./downloads folder
```

---

## 4. API Surface (planned)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/download` | Accept a URL, infer platform, queue download job |
| `GET` | `/api/status/{job_id}` | Poll background task status & progress |
| `GET` | `/api/files` | List all downloaded files |
| `GET` | `/api/files/{filename}` | Stream / serve a downloaded file |
| `DELETE` | `/api/files/{filename}` | Delete a file |
| `GET` | `/` | Serve `index.html` |

---

## 5. Current Status / Checklist

### ✅ Step 0 — Project Bootstrap
- [x] `project_state.md` created and documented
- [x] Directory structure scaffolded

### ✅ Step 1 — Infrastructure
- [x] `Dockerfile` created (Python 3.11-slim, ffmpeg, pip deps)
- [x] `docker-compose.yml` created (port 8000, ./downloads volume mount)
- [x] `requirements.txt` created

### ✅ Step 2 — Backend (`app/main.py`)
- [x] FastAPI app with in-memory job registry + thread-safe locking
- [x] `POST /api/download` — detects platform, creates job, dispatches background task
- [x] `GET /api/status/{job_id}` — real-time progress polling
- [x] `GET /api/jobs` / `DELETE /api/jobs/{job_id}` — job history management
- [x] yt-dlp worker with live progress hook (%, speed, ETA)
- [x] spotdl worker (CLI subprocess, 320k MP3 output)
- [x] `GET /api/files` / `GET /api/files/{filename}` / `DELETE /api/files/{filename}`
- [x] Path traversal protection on all file ops

### ✅ Step 3 — Frontend (`app/static/index.html`)
- [x] Dark glassmorphism SPA (Tailwind CSS via CDN, Inter font)
- [x] URL input with live platform auto-detection + icon/badge change
- [x] Queue & History tab: animated shimmer progress bar, status pills, file download link
- [x] My Files tab: file grid with size, date, inline download & delete
- [x] Delete confirmation modal + toast notification system
- [x] Responsive layout, keyboard shortcut (Enter to download)

### ✅ Step 4 — Verification
- [x] `docker compose build` passes without errors
- [x] App reachable at `http://localhost:8000`
- [ ] Successful YouTube download test (requires real URL)
- [ ] Successful Spotify download test (requires Spotify credentials)

---

## 6. Key Decisions & Notes

- **ffmpeg** is installed at the OS level inside the Docker image (not via pip) so both `yt-dlp` and `spotdl` can find the binary on `$PATH`.
- Downloads are saved to `/app/downloads` inside the container, bind-mounted to `./downloads` on the host so files survive container restarts.
- Port **8000** is exposed exclusively for the FastAPI/Uvicorn server.
- Spotdl requires a Spotify Client ID & Secret; these will be passed via environment variables defined in `docker-compose.yml`.
