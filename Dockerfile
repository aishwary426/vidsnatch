FROM node:20-slim

# Install Python, pip, ffmpeg, and Chromium (for Puppeteer/whatsapp-web.js)
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    ffmpeg \
    chromium \
    fonts-freefont-ttf \
    ca-certificates \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Tell Puppeteer to use installed Chromium instead of downloading its own
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Install Node dependencies
COPY whatsapp_bot/package.json whatsapp_bot/package-lock.json* whatsapp_bot/
RUN cd whatsapp_bot && npm install --omit=dev

# Copy all source files
COPY . .

# Create downloads directory
RUN mkdir -p downloads

ENV PORT=8080
ENV FLASK_URL=http://localhost:8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["bash", "start.sh"]
