# ─────────────────────────────────────────────────────────────────────────────
# Downlo — Dockerfile
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

# ── Patch spotdl for Spotify API compatibility ────────────────────────────────
# spotdl crashes with KeyError when Spotify API omits 'genres' or 'label' keys.
# Patch song.py to use .get() with safe defaults.
RUN SONG_PY=$(python -c "import spotdl.types.song; print(spotdl.types.song.__file__)") && \
    sed -i 's/raw_album_meta\["genres"\]/raw_album_meta.get("genres", [])/g' "$SONG_PY" && \
    sed -i 's/raw_artist_meta\["genres"\]/raw_artist_meta.get("genres", [])/g' "$SONG_PY" && \
    sed -i 's/raw_album_meta\["label"\]/raw_album_meta.get("label", "")/g' "$SONG_PY" && \
    echo "✓ Patched spotdl song.py for API compatibility"

# ── Application code ──────────────────────────────────────────────────────────
COPY ./app ./

# ── Downloads directory ───────────────────────────────────────────────────────
# Create the mount point; actual files will live on the host via bind mount
RUN mkdir -p /app/downloads

# ── Runtime ───────────────────────────────────────────────────────────────────
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
