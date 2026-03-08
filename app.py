import os
import re
import json
import uuid
import shutil
import zipfile
import threading
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_file, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

DOWNLOADS_DIR = Path('downloads')
DOWNLOADS_DIR.mkdir(exist_ok=True)

# Store SSE progress queues per session
progress_queues = {}
progress_lock = threading.Lock()


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def get_yt_dlp_path():
    for cmd in ['yt-dlp', 'yt_dlp', 'python -m yt_dlp']:
        try:
            result = subprocess.run(cmd.split() + ['--version'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return cmd.split()
        except Exception:
            continue
    return ['yt-dlp']


def get_ffmpeg_path():
    """Return the absolute path to ffmpeg, or just 'ffmpeg' as fallback."""
    found = shutil.which('ffmpeg')
    if found:
        return found
    # Common Homebrew / system locations
    for p in ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg']:
        if os.path.isfile(p):
            return p
    return 'ffmpeg'


YT_DLP = get_yt_dlp_path()
FFMPEG = get_ffmpeg_path()


def run_yt_dlp(args, timeout=60):
    cmd = YT_DLP + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result


QR_PATH = Path('qr.png')  # written by the bot process


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/downloads/<path:filename>')
def serve_download(filename):
    return send_from_directory(str(DOWNLOADS_DIR.absolute()), filename, as_attachment=True)


@app.route('/qr')
def qr_page():
    """Serve a scannable QR page. Visit this URL on Railway to link WhatsApp."""
    connected = not QR_PATH.exists()
    if connected:
        html = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"/>
<meta http-equiv="refresh" content="5"/>
<title>VidSnatch — WhatsApp Status</title>
<style>
  body{font-family:system-ui,sans-serif;background:#0a0a0f;color:#f1f5f9;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px;}
  .icon{font-size:72px;}
  h1{font-size:28px;font-weight:800;color:#22c55e;margin:0;}
  p{color:#94a3b8;margin:0;}
</style></head><body>
<div class="icon">✅</div>
<h1>Bot is Connected!</h1>
<p>WhatsApp is linked and the bot is running.</p>
<p style="margin-top:8px;font-size:13px;">This page auto-refreshes every 5 seconds.</p>
</body></html>'''
    else:
        html = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"/>
<meta http-equiv="refresh" content="20"/>
<title>VidSnatch — Scan QR</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:system-ui,sans-serif;background:#0a0a0f;color:#f1f5f9;
       display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
  .card{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);
        border-radius:20px;padding:40px;text-align:center;max-width:420px;width:100%;}
  h1{font-size:26px;font-weight:800;background:linear-gradient(135deg,#8b5cf6,#3b82f6);
     -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:8px;}
  p{color:#94a3b8;font-size:14px;margin-bottom:24px;line-height:1.6;}
  img{width:280px;height:280px;border-radius:12px;background:white;padding:12px;}
  .steps{text-align:left;margin-top:24px;font-size:13px;color:#64748b;line-height:2;}
  .refresh{margin-top:16px;font-size:12px;color:#475569;}
</style></head><body>
<div class="card">
  <h1>📱 Link WhatsApp</h1>
  <p>Scan this QR code with WhatsApp to activate VidSnatch bot.<br/>QR auto-refreshes every 20 seconds.</p>
  <img src="/qr/image" alt="WhatsApp QR Code"/>
  <div class="steps">
    1. Open WhatsApp on your phone<br/>
    2. Go to <strong>Settings → Linked Devices</strong><br/>
    3. Tap <strong>Link a Device</strong><br/>
    4. Point camera at the QR above
  </div>
  <div class="refresh">Page auto-refreshes every 20 seconds</div>
</div>
</body></html>'''
    return html, 200, {'Content-Type': 'text/html'}


@app.route('/qr/image')
def qr_image():
    """Serve the raw QR PNG for embedding in the /qr page."""
    if QR_PATH.exists():
        return send_file(str(QR_PATH.absolute()), mimetype='image/png')
    return '', 404


SUPPORTED_DOMAINS = (
    'youtube.com', 'youtu.be',
    'instagram.com', 'instagr.am',
    'facebook.com', 'fb.watch', 'fb.com',
    'twitter.com', 'x.com',
    'tiktok.com',
)

def detect_platform(url):
    url_l = url.lower()
    if 'instagram.com' in url_l or 'instagr.am' in url_l:
        return 'instagram'
    if 'facebook.com' in url_l or 'fb.watch' in url_l:
        return 'facebook'
    if 'tiktok.com' in url_l:
        return 'tiktok'
    if 'twitter.com' in url_l or 'x.com' in url_l:
        return 'twitter'
    return 'youtube'

def is_playlist_url(url, platform):
    if platform == 'youtube':
        return 'list=' in url and 'watch?v=' not in url
    if platform == 'instagram':
        return '/stories/' in url or '?highlight=' in url
    return False


@app.route('/api/fetch', methods=['POST'])
def fetch_info():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    if not any(d in url.lower() for d in SUPPORTED_DOMAINS):
        return jsonify({'error': 'Unsupported URL. Paste a YouTube, Instagram, Facebook, TikTok, or Twitter/X link.'}), 400

    platform = detect_platform(url)
    playlist_url = is_playlist_url(url, platform)

    try:
        mobile_ua = [
            '--add-header', 'User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
        ]
        if platform == 'youtube':
            fetch_extra = ['--no-check-certificates']
        elif platform in ('instagram', 'facebook', 'tiktok'):
            fetch_extra = mobile_ua + [
                '--add-header', 'Referer:https://www.instagram.com/',
                '--no-check-certificates',
            ]
        else:
            fetch_extra = []

        result = run_yt_dlp([
            '--dump-json',
            '--yes-playlist' if playlist_url else '--no-playlist',
            '--flat-playlist',
        ] + fetch_extra + [url], timeout=45)

        if result.returncode != 0:
            err = result.stderr.lower()
            if 'private' in err:
                return jsonify({'error': 'This video is private and cannot be downloaded.'}), 400
            elif 'age-restrict' in err or 'confirm your age' in err or 'inappropriate for some users' in err:
                return jsonify({'error': 'This video is age-restricted.'}), 400
            elif 'login required' in err or 'login_required' in err or 'log in to' in err or 'not logged in' in err:
                return jsonify({'error': 'This content requires login. Instagram/Facebook private content cannot be downloaded.'}), 400
            elif 'copyright' in err:
                return jsonify({'error': 'This video has been removed due to copyright.'}), 400
            elif 'unavailable' in err or 'not available' in err:
                return jsonify({'error': 'This video is unavailable or region-restricted.'}), 400
            # Log actual error for debugging
            actual_err = result.stderr.strip().split('\n')[-1] if result.stderr.strip() else 'Unknown error'
            return jsonify({'error': f'Failed to fetch video info. Please check the URL. ({actual_err[:120]})'}), 400

        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        if not lines:
            return jsonify({'error': 'No video information returned.'}), 400

        # Check if playlist
        first = json.loads(lines[0])
        is_playlist = first.get('_type') == 'playlist' or len(lines) > 1

        if is_playlist or playlist_url:
            # Try to get playlist info
            pl_result = run_yt_dlp([
                '--dump-single-json',
                '--flat-playlist',
                '--yes-playlist',
                url
            ], timeout=60)

            if pl_result.returncode == 0 and pl_result.stdout.strip():
                pl_data = json.loads(pl_result.stdout.strip())
                entries = pl_data.get('entries', [])
                videos = []
                for e in entries:
                    videos.append({
                        'title': e.get('title', 'Unknown'),
                        'id': e.get('id', ''),
                        'url': e.get('url') or e.get('webpage_url') or f"https://www.youtube.com/watch?v={e.get('id','')}",
                        'thumbnail': e.get('thumbnail') or e.get('thumbnails', [{}])[-1].get('url', '') if e.get('thumbnails') else '',
                        'duration': e.get('duration', 0),
                    })
                return jsonify({
                    'type': 'playlist',
                    'title': pl_data.get('title', 'Playlist'),
                    'channel': pl_data.get('uploader') or pl_data.get('channel', ''),
                    'thumbnail': (entries[0].get('thumbnail') or '') if entries else '',
                    'count': len(entries),
                    'videos': videos,
                    'formats': ['mp4', 'mp3'],
                    'qualities': ['1080p', '720p', '480p', '360p'],
                })

        # Single video - get full format info
        info_result = run_yt_dlp([
            '--dump-single-json',
            '--no-playlist',
        ] + fetch_extra + [url], timeout=45)

        if info_result.returncode != 0:
            return jsonify({'error': 'Failed to fetch video details.'}), 400

        info = json.loads(info_result.stdout.strip())

        # Extract available heights
        formats = info.get('formats', [])
        heights = set()
        for f in formats:
            h = f.get('height')
            if h and f.get('vcodec') != 'none':
                heights.add(h)

        quality_map = {1080: '1080p', 720: '720p', 480: '480p', 360: '360p'}
        available = [quality_map[h] for h in sorted(heights, reverse=True) if h in quality_map]
        if not available:
            available = ['720p', '480p', '360p']

        duration = info.get('duration', 0)
        view_count = info.get('view_count', 0)

        return jsonify({
            'type': 'video',
            'platform': platform,
            'title': info.get('title', 'Unknown'),
            'channel': info.get('uploader') or info.get('channel', ''),
            'thumbnail': info.get('thumbnail', ''),
            'duration': duration,
            'view_count': view_count,
            'formats': ['mp4', 'mp3'],
            'qualities': available,
        })

    except json.JSONDecodeError:
        return jsonify({'error': 'Could not parse video information.'}), 400
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Request timed out. Please try again.'}), 408
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


def parse_progress(line):
    """Parse yt-dlp progress output."""
    # [download]  45.2% of  123.45MiB at  2.34MiB/s ETA 00:12
    match = re.search(
        r'\[download\]\s+([\d.]+)%\s+of\s+([\d.]+\w+)\s+at\s+([\d.]+\w+/s)\s+ETA\s+(\S+)',
        line
    )
    if match:
        return {
            'percent': float(match.group(1)),
            'size': match.group(2),
            'speed': match.group(3),
            'eta': match.group(4),
        }
    return None


def download_worker(session_id, url, fmt, quality, is_playlist):
    # queue initialized in main thread before worker starts

    def send(event_type, data):
        with progress_lock:
            if session_id in progress_queues:
                progress_queues[session_id].append({
                    'event': event_type,
                    'data': data
                })

    try:
        session_dir = DOWNLOADS_DIR / session_id
        session_dir.mkdir(exist_ok=True)

        if fmt == 'mp3':
            # quality is e.g. "320k", "192k", "128k"
            bitrate = quality.replace('k', '') if quality.endswith('k') else '192'
            format_opts = [
                '-x',
                '--audio-format', 'mp3',
                '--audio-quality', f'{bitrate}K',
            ]
        else:
            quality_num = quality.replace('p', '')
            platform = detect_platform(url)
            if platform in ('instagram', 'tiktok', 'facebook'):
                # Instagram/TikTok/FB use DASH streams (separate video+audio tracks).
                # Reels are portrait (e.g. 720x1280): quality_num maps to WIDTH, not height.
                # Use width-based filter so "720p" captures the 720x1280 stream.
                # Re-encode audio to AAC-LC (128k) because Instagram serves HE-AAC
                # (mp4a.40.5) which many players/WhatsApp don't decode audibly.
                format_opts = [
                    '-f', (
                        f'bestvideo[width<={quality_num}]+bestaudio'
                        f'/bestvideo+bestaudio'
                        f'/best'
                    ),
                    '--merge-output-format', 'mp4',
                    '--postprocessor-args', 'Merger+ffmpeg_o:-c:a aac -b:a 128k -ar 44100 -ac 2',
                ]
            else:
                # YouTube: prefer H.264 video with separate best audio
                format_opts = [
                    '-f', (
                        f'bestvideo[height<={quality_num}][vcodec^=avc1][ext=mp4]+bestaudio'
                        f'/bestvideo[height<={quality_num}][vcodec^=avc][ext=mp4]+bestaudio'
                        f'/bestvideo[height<={quality_num}]+bestaudio'
                        f'/best[height<={quality_num}]'
                        f'/best'
                    ),
                    '--merge-output-format', 'mp4',
                ]

        playlist_opt = ['--yes-playlist'] if is_playlist else ['--no-playlist']

        platform = detect_platform(url)
        if platform == 'youtube':
            yt_extra = ['--no-check-certificates']
        elif platform in ('instagram', 'facebook', 'tiktok'):
            yt_extra = [
                '--add-header', 'User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                '--add-header', 'Accept-Language:en-US,en;q=0.9',
                '--add-header', 'Referer:https://www.instagram.com/',
                '--no-check-certificates',
            ]
        else:
            yt_extra = []

        cmd = YT_DLP + [
            '--newline',
            '--progress',
            '-o', str(session_dir / '%(title)s.%(ext)s'),
            '--ffmpeg-location', FFMPEG,
        ] + yt_extra + format_opts + playlist_opt + [url]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        current_video = ''
        video_index = 0
        total_videos = 1

        stderr_lines = []

        # Read stdout line-by-line for progress; collect stderr in background
        import threading as _t
        def _read_stderr():
            for l in process.stderr:
                stderr_lines.append(l.strip())
        _t.Thread(target=_read_stderr, daemon=True).start()

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue

            # Detect current video in playlist
            if '[download] Downloading item' in line:
                m = re.search(r'Downloading item (\d+) of (\d+)', line)
                if m:
                    video_index = int(m.group(1))
                    total_videos = int(m.group(2))
                    send('playlist_progress', {
                        'current': video_index,
                        'total': total_videos,
                    })
            elif '[download] Destination:' in line:
                current_video = line.replace('[download] Destination:', '').strip()
                current_video = Path(current_video).name
                send('video_start', {'title': current_video, 'index': video_index})

            prog = parse_progress(line)
            if prog:
                if is_playlist:
                    overall = ((video_index - 1) / max(total_videos, 1) * 100) + (prog['percent'] / max(total_videos, 1))
                    prog['overall_percent'] = round(overall, 1)
                    prog['current_video'] = video_index
                    prog['total_videos'] = total_videos
                    prog['current_title'] = current_video
                send('progress', prog)

        process.wait()

        if process.returncode != 0:
            # Surface actual yt-dlp error if available
            err_text = ' '.join(stderr_lines).lower()
            if 'private' in err_text:
                msg = 'This video is private and cannot be downloaded.'
            elif 'age-restrict' in err_text or 'confirm your age' in err_text or 'inappropriate for some users' in err_text:
                msg = 'This video is age-restricted.'
            elif 'copyright' in err_text:
                msg = 'Blocked due to copyright.'
            elif 'unavailable' in err_text or 'not available' in err_text:
                msg = 'This video is unavailable.'
            elif 'ffmpeg' in err_text:
                msg = f'FFmpeg error — ensure ffmpeg is installed. Detail: {stderr_lines[-1] if stderr_lines else ""}'
            else:
                msg = stderr_lines[-1] if stderr_lines else 'Download failed.'
            send('error', {'message': msg})
            return

        # Collect output files
        files = list(session_dir.glob('*'))
        if not files:
            send('error', {'message': 'No files were downloaded.'})
            return

        if is_playlist and len(files) > 1:
            zip_name = f'playlist_{session_id}.zip'
            zip_path = DOWNLOADS_DIR / zip_name
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, f.name)
            send('complete', {
                'type': 'zip',
                'filename': zip_name,
                'url': f'/downloads/{zip_name}',
            })
        else:
            f = files[0]
            dest = DOWNLOADS_DIR / f.name
            f.rename(dest)
            send('complete', {
                'type': 'file',
                'filename': f.name,
                'url': f'/downloads/{f.name}',
            })

        # Cleanup session dir
        try:
            for f in session_dir.glob('*'):
                f.unlink()
            session_dir.rmdir()
        except Exception:
            pass

    except Exception as e:
        send('error', {'message': str(e)})
    finally:
        send('done', {})


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    fmt = (data or {}).get('format', 'mp4')
    quality = (data or {}).get('quality', '720p')
    is_playlist = (data or {}).get('is_playlist', False)

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    session_id = str(uuid.uuid4())
    with progress_lock:
        progress_queues[session_id] = []

    thread = threading.Thread(
        target=download_worker,
        args=(session_id, url, fmt, quality, is_playlist),
        daemon=True
    )
    thread.start()

    return jsonify({'session_id': session_id})


@app.route('/api/progress/<session_id>')
def progress_stream(session_id):
    def generate():
        import time
        last_done = False
        while not last_done:
            with progress_lock:
                queue = progress_queues.get(session_id, [])
                events = queue.copy()
                if session_id in progress_queues:
                    progress_queues[session_id] = []

            for item in events:
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
                if item['event'] == 'done':
                    last_done = True
                    break

            if not last_done:
                time.sleep(0.3)

        with progress_lock:
            progress_queues.pop(session_id, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/health')
def health():
    try:
        r = run_yt_dlp(['--version'], timeout=5)
        ytdlp_ok = r.returncode == 0
        ytdlp_version = r.stdout.strip() if ytdlp_ok else 'not found'
    except Exception:
        ytdlp_ok = False
        ytdlp_version = 'not found'

    try:
        r2 = subprocess.run([FFMPEG, '-version'], capture_output=True, timeout=5)
        ffmpeg_ok = r2.returncode == 0
    except Exception:
        ffmpeg_ok = False

    return jsonify({
        'status': 'ok',
        'yt_dlp': ytdlp_version,
        'yt_dlp_ok': ytdlp_ok,
        'ffmpeg_ok': ffmpeg_ok,
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
