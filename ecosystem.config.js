module.exports = {
  apps: [
    {
      name: "navbatchilik-bot",
      script: "bot.py",
      interpreter: "/root/repo11/venv/bin/python",
      // Restart automatically if it crashes
      autorestart: true,
      watch: false,
      // Keep logs tidy
      out_file: "./logs/bot-out.log",
      error_file: "./logs/bot-err.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      // Env vars are loaded from .env by python-dotenv inside bot.py,
      // but you can also set them here if needed.
      env: {
        NODE_ENV: "production",
        // TZ is set system-wide on the VPS (Asia/Tashkent).
        // Alternatively uncomment the line below:
        // TZ: "Asia/Tashkent",
      },
    },
  ],
};
