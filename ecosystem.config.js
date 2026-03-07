const path = require('path');
const root = __dirname;

module.exports = {
  apps: [
    {
      name: 'vidsnatch-flask',
      script: path.join(root, 'venv/bin/python3'),
      args: 'app.py',
      cwd: root,
      env: {
        PORT: '5001',
        FLASK_ENV: 'production',
        PYTHONUNBUFFERED: '1',
      },
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      watch: false,
      error_file: path.join(root, 'logs/flask-error.log'),
      out_file: path.join(root, 'logs/flask-out.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
    {
      name: 'vidsnatch-bot',
      script: 'bot.js',
      cwd: path.join(root, 'whatsapp_bot'),
      env: {
        FLASK_URL: 'http://localhost:5001',
      },
      autorestart: true,
      restart_delay: 5000,   // wait 5s before restart so Flask is ready
      max_restarts: 20,
      watch: false,
      error_file: path.join(root, 'logs/bot-error.log'),
      out_file: path.join(root, 'logs/bot-out.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    },
  ],
};
