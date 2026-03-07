# VidSnatch — YouTube Downloader

A beautiful dark-themed YouTube downloader powered by yt-dlp + Flask.

## Prerequisites

- Python 3.9+
- ffmpeg (required for MP3 conversion and MP4 merging)
- pip

---

## Install FFmpeg

### macOS
```bash
brew install ffmpeg
```

### Ubuntu / Debian
```bash
sudo apt update && sudo apt install ffmpeg -y
```

### Windows
1. Download from https://ffmpeg.org/download.html (get a "release build")
2. Extract and add the `bin/` folder to your system PATH
3. Verify: `ffmpeg -version`

---

## Setup

### 1. Clone / navigate to the project directory
```bash
cd youtube_download
```

### 2. Create a virtual environment (recommended)

**macOS / Linux**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows**
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the app
```bash
python app.py
```

Open your browser at **http://localhost:5000**

---

## Deploy to Railway

1. Push this folder to a GitHub repo
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Select the repo — Railway auto-detects Python
4. Set the start command: `python app.py`
5. Set environment variable: `PORT=8080` (Railway sets this automatically)
6. Railway will install `requirements.txt` automatically

> Note: ffmpeg must be available on the Railway instance. Add the `nixpacks.toml` below if needed.

**nixpacks.toml** (create this file if ffmpeg is missing on Railway):
```toml
[phases.setup]
nixPkgs = ["ffmpeg"]
```

---

## Deploy to Render

1. Push to GitHub
2. New Web Service → connect repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `python app.py`
5. Set env var `PORT` if needed (Render sets it automatically)

---

## Project Structure

```
youtube_download/
├── app.py          # Flask backend (REST API + SSE)
├── index.html      # Single-file frontend
├── requirements.txt
├── README.md
└── downloads/      # Created automatically at runtime
```

---

## Features

- Single video + full playlist support
- MP4 (1080p/720p/480p/360p) and MP3 (320/192/128 kbps)
- Real-time download progress via Server-Sent Events
- Playlist ZIP bundling
- Download history (localStorage)
- Dark glassmorphism UI
