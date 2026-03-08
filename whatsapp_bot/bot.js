const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const EventSource = require('eventsource');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');
const { execFile, execSync } = require('child_process');

// Find ffmpeg binary
function findFfmpeg() {
  for (const p of ['/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/usr/bin/ffmpeg']) {
    if (fs.existsSync(p)) return p;
  }
  try { execSync('which ffmpeg'); return 'ffmpeg'; } catch { return null; }
}
const FFMPEG_BIN = findFfmpeg();

// Check video codec via ffprobe
function getVideoCodec(filePath) {
  try {
    const ffprobePath = FFMPEG_BIN.replace(/ffmpeg$/, 'ffprobe');
    const out = require('child_process').execSync(
      `"${ffprobePath}" -v quiet -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "${filePath}"`,
      { timeout: 10000 }
    ).toString().trim();
    return out;
  } catch { return 'unknown'; }
}

// Make video WhatsApp-compatible (H.264 video + AAC audio in MP4 container).
// Stream-copies video if already H.264 (near-instant), but always re-encodes audio to AAC.
// Handles any input container (mp4, webm, mkv, mov, …).
function transcodeForWhatsApp(inputPath) {
  return new Promise((resolve) => {
    if (!FFMPEG_BIN) { resolve(inputPath); return; }

    const outputPath = inputPath.replace(/\.[^.]+$/, '_wa.mp4');
    const codec = getVideoCodec(inputPath);
    const alreadyH264 = codec === 'h264';

    console.log(`   🎞  Codec: ${codec} → ${alreadyH264 ? 'copy video + AAC audio' : 're-encoding to H.264+AAC'}`);

    // Always force AAC audio so WhatsApp plays audio in both inline and doc mode.
    // -map 0:a:0? makes audio optional — won't fail on silent/video-only reels.
    const args = alreadyH264
      ? ['-i', inputPath, '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-y', outputPath]
      : ['-i', inputPath, '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-y', outputPath];

    execFile(FFMPEG_BIN, args, { timeout: 5 * 60 * 1000 }, (err) => {
      if (err) {
        console.warn('   ⚠️  Transcode failed, sending original:', err.message);
        resolve(inputPath);
      } else {
        fs.unlink(inputPath, () => {});
        resolve(outputPath);
      }
    });
  });
}

// Build a text progress bar, e.g. "█████░░░░░"
function buildProgressBar(percent, width = 10) {
  const filled = Math.min(Math.round((percent / 100) * width), width);
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

// ── Config ────────────────────────────────────────────────────────
const FLASK_URL = process.env.FLASK_URL || 'http://localhost:5001';
const MAX_INLINE_MB = 15;
const DOWNLOAD_TIMEOUT_MS = 10 * 60 * 1000;

// Use system Chromium if available (Docker), else let Puppeteer find its own
const CHROMIUM_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || undefined;

// ── Multi-platform URL regex ──────────────────────────────────────
const MEDIA_REGEX = /https?:\/\/(?:www\.|m\.|web\.)?(?:youtube\.com\/(?:watch\?[^\s]*v=|shorts\/|playlist\?list=)|youtu\.be\/|instagram\.com\/(?:p|reel|tv|stories)\/|instagr\.am\/|facebook\.com\/(?:watch|reel|video|share)[/?]|fb\.watch\/|twitter\.com\/\w+\/status\/|x\.com\/\w+\/status\/|tiktok\.com\/@[\w.]+\/video\/|vm\.tiktok\.com\/)[^\s<>"']*/i;

function detectPlatform(url) {
  if (/instagram\.com|instagr\.am/i.test(url)) return 'instagram';
  if (/facebook\.com|fb\.watch/i.test(url))    return 'facebook';
  if (/tiktok\.com|vm\.tiktok/i.test(url))     return 'tiktok';
  if (/twitter\.com|x\.com/i.test(url))        return 'twitter';
  return 'youtube';
}

const PLATFORM_ICON = {
  youtube: '▶️', instagram: '📸', facebook: '🔵', tiktok: '🎵', twitter: '🐦',
};

// ── Parse user command ────────────────────────────────────────────
function parseCommand(text) {
  const urlMatch = text.match(MEDIA_REGEX);
  if (!urlMatch) return null;

  const url = urlMatch[0];
  const rest = text.replace(urlMatch[0], '').toLowerCase().trim();
  const platform = detectPlatform(url);

  let fmt = 'mp4';
  let quality = '480p';

  if (rest.includes('mp3') || rest.includes('audio') || rest.includes('song')) {
    fmt = 'mp3';
    if (rest.includes('320')) quality = '320k';
    else if (rest.includes('128')) quality = '128k';
    else quality = '192k';
  } else {
    if (rest.includes('1080')) quality = '1080p';
    else if (rest.includes('720')) quality = '720p';
    else if (rest.includes('360')) quality = '360p';
    else quality = '480p';
  }

  const isPlaylist = platform === 'youtube' && url.includes('list=') && !url.includes('watch?v=');
  return { url, fmt, quality, isPlaylist, platform };
}

// ── HTTP helpers ──────────────────────────────────────────────────
function fetchJSON(url, options = {}) {
  return new Promise((resolve, reject) => {
    const body = options.body ? Buffer.from(options.body) : null;
    const parsed = new URL(url);
    const lib = parsed.protocol === 'https:' ? https : http;
    const req = lib.request({
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname + parsed.search,
      method: options.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...(body ? { 'Content-Length': body.length } : {}),
      },
    }, res => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data) }); }
        catch { reject(new Error('Invalid JSON response')); }
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function downloadFile(fileUrl, destPath) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(fileUrl);
    const lib = parsed.protocol === 'https:' ? https : http;
    const file = fs.createWriteStream(destPath);
    lib.get(fileUrl, res => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close();
        fs.unlinkSync(destPath);
        return downloadFile(res.headers.location, destPath).then(resolve).catch(reject);
      }
      res.pipe(file);
      file.on('finish', () => file.close(resolve));
    }).on('error', err => {
      fs.unlink(destPath, () => {});
      reject(err);
    });
  });
}

function waitForDownload(sessionId, onProgress) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`${FLASK_URL}/api/progress/${sessionId}`);
    const timer = setTimeout(() => {
      es.close();
      reject(new Error('Download timed out after 10 minutes.'));
    }, DOWNLOAD_TIMEOUT_MS);

    es.addEventListener('progress', e => {
      if (onProgress) {
        try { onProgress(JSON.parse(e.data)); } catch {}
      }
    });

    es.addEventListener('complete', e => {
      clearTimeout(timer);
      es.close();
      try { resolve(JSON.parse(e.data)); }
      catch { reject(new Error('Bad complete event')); }
    });

    es.addEventListener('error', e => {
      clearTimeout(timer);
      es.close();
      try { reject(new Error(JSON.parse(e.data).message)); }
      catch { reject(new Error('Download failed on server.')); }
    });

    es.addEventListener('done', () => { clearTimeout(timer); es.close(); });
    es.onerror = () => {};
  });
}

// ── Build WhatsApp client ─────────────────────────────────────────
function createClient() {
  const puppeteerArgs = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--single-process',
  ];

  const clientOptions = {
    authStrategy: new LocalAuth({ dataPath: './.wwebjs_auth' }),
    puppeteer: {
      headless: true,
      args: puppeteerArgs,
      ...(CHROMIUM_PATH ? { executablePath: CHROMIUM_PATH } : {}),
    },
  };

  return new Client(clientOptions);
}

// ── Main message handler ──────────────────────────────────────────
async function handleMessage(msg, client) {
  if (!msg.body) return;
  const cmd = parseCommand(msg.body);
  if (!cmd) return;

  const { url, fmt, quality, platform } = cmd;
  const icon = PLATFORM_ICON[platform] || '🎬';
  const label = fmt === 'mp3' ? `MP3 ${quality}` : `MP4 ${quality}`;
  console.log(`[${new Date().toLocaleTimeString()}] ${msg.from} → ${url} (${label})`);

  await msg.react('⏳');
  let metaTitle = 'your video';

  try {
    // 1. Fetch metadata
    const fetchRes = await fetchJSON(`${FLASK_URL}/api/fetch`, {
      method: 'POST',
      body: JSON.stringify({ url }),
    });

    if (fetchRes.status !== 200) {
      await msg.react('❌');
      await msg.reply(`❌ *Error:* ${fetchRes.data.error || 'Could not fetch video info.'}`);
      return;
    }

    const meta = fetchRes.data;
    metaTitle = meta.title || 'your video';
    const isPlaylistMeta = meta.type === 'playlist';

    const buildStatusMsg = (prog) => {
      const lines = [
        `${icon} *${metaTitle}*`,
        isPlaylistMeta ? `📋 Playlist — ${meta.count} videos` : '',
        meta.channel ? `👤 ${meta.channel}` : '',
        `🎯 Format: ${label}`,
      ].filter(Boolean);

      if (prog) {
        const bar = buildProgressBar(prog.percent);
        lines.push(`\n📥 ${bar} ${prog.percent.toFixed(1)}%`);
        if (prog.speed && prog.eta) lines.push(`⚡ ${prog.speed}  •  ⏱ ETA ${prog.eta}`);
        if (prog.size) lines.push(`📦 ${prog.size}`);
      } else {
        lines.push(`⏳ Starting download...`);
      }
      return lines.join('\n');
    };

    const statusMsg = await msg.reply(buildStatusMsg(null));

    // 2. Start download
    const dlRes = await fetchJSON(`${FLASK_URL}/api/download`, {
      method: 'POST',
      body: JSON.stringify({ url, format: fmt, quality, is_playlist: isPlaylistMeta }),
    });

    if (dlRes.status !== 200) {
      await msg.react('❌');
      await statusMsg.edit(`❌ *Download error:* ${dlRes.data.error || 'Server error.'}`).catch(() => {});
      return;
    }

    // 3. Wait for completion via SSE, editing the status message with live progress
    let lastEditAt = 0;
    const completed = await waitForDownload(dlRes.data.session_id, async (prog) => {
      const now = Date.now();
      if (now - lastEditAt < 5000) return;   // throttle: edit at most once per 5 s
      lastEditAt = now;
      await statusMsg.edit(buildStatusMsg(prog)).catch(() => {});
    });

    // 4. Fetch file to local /tmp
    const fileUrl = `${FLASK_URL}${completed.url}`;
    const tmpPath = path.join('/tmp', completed.filename);
    await downloadFile(fileUrl, tmpPath);

    console.log(`   ✅ ${completed.filename}`);

    // 5. Transcode to H.264+AAC MP4 so WhatsApp plays all video inline
    let sendPath = tmpPath;
    if (fmt !== 'mp3') {
      console.log('   🔄 Transcoding to H.264+AAC for WhatsApp compatibility...');
      sendPath = await transcodeForWhatsApp(tmpPath);
    }

    const fileSizeMB = fs.statSync(sendPath).size / (1024 * 1024);
    console.log(`   📦 ${fileSizeMB.toFixed(1)} MB — sending...`);

    // 6. Send via WhatsApp
    const media = MessageMedia.fromFilePath(sendPath);
    await client.sendMessage(msg.from, media, {
      sendMediaAsDocument: fileSizeMB > MAX_INLINE_MB,
      caption: `✅ *${metaTitle}*\n📦 ${fileSizeMB.toFixed(1)} MB • ${label}`,
    });

    await msg.react('✅');
    console.log(`   📤 Sent to ${msg.from}`);
    fs.unlink(sendPath, () => {});

  } catch (err) {
    console.error('   ❌ Error:', err.message);
    await msg.react('❌');
    await msg.reply(
      `❌ *Failed to download "${metaTitle}"*\n\nReason: ${err.message}\n\n_Make sure the video is public and not age-restricted._`
    );
  }
}

// ── Start bot with auto-reconnect ────────────────────────────────
function startBot() {
  const client = createClient();

  // Shared QR file — Flask serves it at /qr/image
  const QR_FILE = path.join(__dirname, '..', 'qr.png');

  client.on('qr', qr => {
    console.log('\n========================================');
    console.log('📱 SCAN QR: visit  /qr  on your server');
    console.log('   Local: http://localhost:5001/qr');
    console.log('========================================\n');
    qrcode.generate(qr, { small: true });

    // Write QR image to project root so Flask can serve it
    QRCode.toFile(QR_FILE, qr, { width: 400, margin: 2 }, err => {
      if (err) return;
      // Auto-open on macOS during local dev
      const { exec } = require('child_process');
      exec(`open "${QR_FILE}" 2>/dev/null`, () => {});
    });
  });

  client.on('authenticated', () => {
    // Delete QR file so /qr page switches to "Connected" state
    try { fs.unlinkSync(QR_FILE); } catch {}
    console.log('✅ WhatsApp authenticated');
  });
  client.on('auth_failure', msg => {
    console.error('❌ Auth failed:', msg);
    console.log('Deleting session and restarting...');
    setTimeout(startBot, 5000);
  });

  client.on('ready', () => {
    console.log('\n🤖 VidSnatch Bot is LIVE and ready!');
    console.log(`   Connected to: ${FLASK_URL}`);
    console.log('\nSend a YouTube link to download:');
    console.log('  <url>          → 720p MP4');
    console.log('  <url> mp3      → 192kbps MP3');
    console.log('  <url> 1080     → 1080p MP4\n');
  });

  // 'message' = received from others; 'message_create' = your own sent messages
  // We need both so Saved Messages (send-to-self) works
  client.on('message', msg => handleMessage(msg, client));
  client.on('message_create', msg => {
    if (msg.fromMe) handleMessage(msg, client);
  });

  client.on('disconnected', reason => {
    console.log('⚠️  Disconnected:', reason, '— restarting in 10s...');
    client.destroy().catch(() => {});
    setTimeout(startBot, 10000);
  });

  client.initialize().catch(err => {
    console.error('Init error:', err.message, '— retrying in 15s...');
    setTimeout(startBot, 15000);
  });
}

// ── Entry point ───────────────────────────────────────────────────
console.log('🚀 Starting VidSnatch WhatsApp Bot...');
console.log(`   Flask backend: ${FLASK_URL}`);
console.log(`   Chromium: ${CHROMIUM_PATH || 'auto-detect'}\n`);
startBot();
