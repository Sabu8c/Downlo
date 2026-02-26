"""
Media Vault — FastAPI Backend
Handles background download jobs for YouTube (yt-dlp) and Spotify (spotdl).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/app/downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# ─────────────────────────────────────────────────────────────────────────────
# Job state store (in-memory; survives until container restart)
# ─────────────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class Platform(str, Enum):
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    UNKNOWN = "unknown"


class DownloadOptions(BaseModel):
    media_type: str = "video+audio"   # "video+audio" | "audio"
    quality: str = "best"             # "best" | "1080" | "720" | "480" | "360"
    audio_format: str = "mp3"         # "mp3" | "flac" | "ogg" | "m4a"
    audio_bitrate: str = "320k"       # "320k" | "256k" | "192k" | "128k"


class Job(BaseModel):
    id: str
    url: str
    platform: Platform
    status: JobStatus = JobStatus.QUEUED
    progress: str = "Waiting…"
    filename: str | None = None
    error: str | None = None
    created_at: str = ""
    options: dict = {}

    model_config = {"use_enum_values": True}


# Global registry: job_id → Job
jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def detect_platform(url: str) -> Platform:
    url_lower = url.lower()
    if any(h in url_lower for h in ("youtube.com", "youtu.be", "music.youtube.com")):
        return Platform.YOUTUBE
    if any(h in url_lower for h in ("spotify.com", "open.spotify.com")):
        return Platform.SPOTIFY
    return Platform.UNKNOWN


def _set_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        job = jobs[job_id]
        for key, val in kwargs.items():
            setattr(job, key, val)


# ─────────────────────────────────────────────────────────────────────────────
# Download workers (run in thread pool via asyncio.to_thread)
# ─────────────────────────────────────────────────────────────────────────────

def _run_youtube(job_id: str, url: str, options: DownloadOptions) -> None:
    """Download YouTube video/audio with user-selected quality options."""
    import yt_dlp  # imported here so main app can start even if deps missing

    audio_only = options.media_type == "audio"

    # ── Build format string based on user options ────────────────────────────
    if audio_only:
        fmt = "bestaudio/best"
        codec = options.audio_format if options.audio_format in ("mp3", "flac", "ogg", "m4a") else "mp3"
        merge_fmt = codec
        postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": codec}]
    else:
        q = options.quality
        if q in ("1080", "720", "480", "360"):
            fmt = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
        else:
            fmt = "bestvideo+bestaudio/best"
        merge_fmt = "mp4"
        postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]

    ydl_opts: dict[str, Any] = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "noplaylist": False,
        "format": fmt,
        "merge_output_format": merge_fmt,
        "postprocessors": postprocessors,
        "quiet": True,
        "no_warnings": True,
    }

    downloaded_file: list[str] = []

    class ProgressLogger:
        def debug(self, msg: str) -> None: pass
        def warning(self, msg: str) -> None: pass
        def error(self, msg: str) -> None: pass

    def progress_hook(d: dict[str, Any]) -> None:
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "…").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            _set_job(job_id, progress=f"Downloading {pct} at {speed} — ETA {eta}")
        elif d["status"] == "finished":
            downloaded_file.append(d["filename"])
            _set_job(job_id, progress="Post-processing…")

    ydl_opts["logger"] = ProgressLogger()
    ydl_opts["progress_hooks"] = [progress_hook]

    try:
        _set_job(job_id, status=JobStatus.RUNNING, progress="Starting download…")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        fname = Path(downloaded_file[-1]).name if downloaded_file else None
        _set_job(
            job_id,
            status=JobStatus.DONE,
            progress="Complete ✓",
            filename=fname,
        )
    except Exception as exc:
        _set_job(job_id, status=JobStatus.ERROR, error=str(exc), progress="Failed")


def _run_spotdl(job_id: str, url: str, options: DownloadOptions) -> None:
    """Download Spotify track/album/playlist via spotdl CLI."""
    _set_job(job_id, status=JobStatus.RUNNING, progress="Starting Spotify download…")

    audio_fmt = options.audio_format if options.audio_format in ("mp3", "flac", "ogg", "m4a") else "mp3"
    bitrate = options.audio_bitrate if options.audio_bitrate in ("320k", "256k", "192k", "128k") else "320k"

    cmd = [
        "spotdl",
        url,
        "--output", str(DOWNLOAD_DIR / "{title}"),
        "--format", audio_fmt,
        "--bitrate", bitrate,
    ]

    # Inject credentials if available
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        cmd += ["--client-id", SPOTIFY_CLIENT_ID, "--client-secret", SPOTIFY_CLIENT_SECRET]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(DOWNLOAD_DIR),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "spotdl returned non-zero exit code")

        # Try to extract a filename from stdout
        fname = None
        for line in result.stdout.splitlines():
            m = re.search(r'Downloaded\s+"?(.+?)"?\s*$', line)
            if m:
                fname = m.group(1).strip() + f".{audio_fmt}"
                break

        _set_job(
            job_id,
            status=JobStatus.DONE,
            progress="Complete ✓",
            filename=fname,
        )
    except Exception as exc:
        _set_job(job_id, status=JobStatus.ERROR, error=str(exc), progress="Failed")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Media Vault",
    description="Self-hosted YouTube & Spotify downloader",
    version="1.0.0",
)


# ── Request / Response Models ─────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    url: str
    options: DownloadOptions = DownloadOptions()

    @field_validator("url")
    @classmethod
    def url_must_be_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class DownloadResponse(BaseModel):
    job_id: str
    platform: str
    message: str


# ── Background task dispatcher ────────────────────────────────────────────────

async def _dispatch_job(job_id: str, url: str, platform: Platform, options: DownloadOptions) -> None:
    if platform == Platform.YOUTUBE:
        await asyncio.to_thread(_run_youtube, job_id, url, options)
    elif platform == Platform.SPOTIFY:
        await asyncio.to_thread(_run_spotdl, job_id, url, options)
    else:
        _set_job(job_id, status=JobStatus.ERROR, error="Unsupported platform", progress="Failed")


# ── API Routes ────────────────────────────────────────────────────────────────

@app.post("/api/download", response_model=DownloadResponse, status_code=202)
async def start_download(req: DownloadRequest, bg: BackgroundTasks) -> DownloadResponse:
    """Queue a new download job. Returns a job_id to poll for status."""
    platform = detect_platform(req.url)
    if platform == Platform.UNKNOWN:
        raise HTTPException(
            status_code=422,
            detail="URL not recognised as YouTube or Spotify. "
                   "Supported hosts: youtube.com, youtu.be, music.youtube.com, open.spotify.com",
        )

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        url=req.url,
        platform=platform,
        created_at=datetime.utcnow().isoformat() + "Z",
        options=req.options.model_dump(),
    )
    with _jobs_lock:
        jobs[job_id] = job

    bg.add_task(_dispatch_job, job_id, req.url, platform, req.options)

    return DownloadResponse(
        job_id=job_id,
        platform=platform.value,
        message="Job queued successfully",
    )


@app.get("/api/status/{job_id}")
async def get_status(job_id: str) -> Job:
    """Poll the status and progress of a queued or running job."""
    with _jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs")
async def list_jobs() -> list[Job]:
    """Return all jobs (newest first)."""
    with _jobs_lock:
        all_jobs = list(jobs.values())
    return sorted(all_jobs, key=lambda j: j.created_at, reverse=True)


@app.delete("/api/jobs/{job_id}", status_code=204, response_class=Response)
async def delete_job(job_id: str):
    """Remove a job record from history."""
    with _jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        del jobs[job_id]


@app.get("/api/files")
async def list_files() -> list[dict[str, Any]]:
    """Return metadata for every file in the downloads directory."""
    files = []
    for path in sorted(DOWNLOAD_DIR.iterdir()):
        if path.is_file():
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "size_human": _human_size(stat.st_size),
                    "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                    "extension": path.suffix.lstrip(".").lower(),
                }
            )
    return files


@app.get("/api/files/{filename}")
async def serve_file(filename: str) -> FileResponse:
    """Stream / download a file from the downloads directory."""
    path = DOWNLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Prevent directory traversal
    if not str(path.resolve()).startswith(str(DOWNLOAD_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(path, filename=filename)


@app.delete("/api/files/{filename}", status_code=204, response_class=Response)
async def delete_file(filename: str):
    """Permanently delete a file from the downloads directory."""
    path = DOWNLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not str(path.resolve()).startswith(str(DOWNLOAD_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    path.unlink()


# ── Utility ───────────────────────────────────────────────────────────────────

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


# ── Static files & SPA fallback ───────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
