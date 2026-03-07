#!/bin/bash
set -e

echo "=== VidSnatch Starting ==="

# Start Flask backend in background
echo "[Flask] Starting on port ${PORT:-8080}..."
python3 app.py &
FLASK_PID=$!

# Wait for Flask to be ready
echo "[Flask] Waiting for backend to be ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${PORT:-8080}/api/health" > /dev/null 2>&1; then
        echo "[Flask] Backend is ready."
        break
    fi
    sleep 1
done

# Start WhatsApp bot
echo "[Bot] Starting WhatsApp bot..."
cd whatsapp_bot
FLASK_URL="http://localhost:${PORT:-8080}" node bot.js &
BOT_PID=$!

echo "=== Both services running ==="
echo "    Flask PID: $FLASK_PID"
echo "    Bot PID:   $BOT_PID"
echo ""
echo ">>> If first run: check logs for QR code, then scan with WhatsApp <<<"

# Keep container alive; restart bot if it dies
wait $FLASK_PID
