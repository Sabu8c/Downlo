# ─────────────────────────────────────────────────────────────────────────────
# Media Vault — Dockerfile
# Base: Python 3.11 slim; adds ffmpeg + yt-dlp + spotdl
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Prevent interactive prompts during apt operations
ENV DEBIAN_FRONTEND=noninteractive

# ── System dependencies ───────────────────────────────────────────────────────
# ffmpeg: required by yt-dlp (merge video+audio) and spotdl (audio conversion)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY ./app ./

# ── Downloads directory ───────────────────────────────────────────────────────
# Create the mount point; actual files will live on the host via bind mount
RUN mkdir -p /app/downloads

# ── Runtime ───────────────────────────────────────────────────────────────────
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
