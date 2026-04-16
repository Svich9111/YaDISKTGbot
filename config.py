import os
from dotenv import dotenv_values

# Load .env file if it exists (for local development)
# On Render.com, environment variables are set directly in the dashboard
_env_file = dotenv_values(".env") if os.path.exists(".env") else {}


def _get_env(key: str, default: str = None) -> str | None:
    """Get environment variable from os.environ or .env file."""
    return os.environ.get(key) or _env_file.get(key) or default


# Telegram Bot
BOT_TOKEN = _get_env("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set. Please configure it in .env or environment variables.")

# Yandex Disk
YANDEX_DISK_TOKEN = _get_env("YANDEX_DISK_TOKEN")
if not YANDEX_DISK_TOKEN:
    raise ValueError("YANDEX_DISK_TOKEN is not set. Please configure it in .env or environment variables.")

# Admin configuration
ADMIN_ID = int(_get_env("ADMIN_ID", "0"))

# Database - use absolute path for Render.com (ephemeral filesystem)
DB_NAME = _get_env("DB_NAME", "bot_database.db")

# Yandex Disk paths
ROOT_FOLDER = _get_env("ROOT_FOLDER", "Фото из телеграмма")
EXPORT_FOLDER = f"{ROOT_FOLDER}/Полный архив"

# Retry settings
RETRY_ATTEMPTS = 3
RETRY_DELAYS = [5, 30, 120]  # seconds

# Queue settings
MAX_CONCURRENT_UPLOADS = int(_get_env("MAX_CONCURRENT_UPLOADS", "5"))

# File size limit (2GB)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

# Web server for health checks (Render.com requires HTTP server)
WEB_HOST = _get_env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(_get_env("PORT", "10000"))

# Webhook settings
WEBHOOK_HOST = _get_env("WEBHOOK_HOST")  # e.g., https://your-app.onrender.com
WEBHOOK_PATH = _get_env("WEBHOOK_PATH", f"/webhook/{BOT_TOKEN}")
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else None

# Sentry
SENTRY_DSN = _get_env("SENTRY_DSN")

# Redis
REDIS_URL = _get_env("REDIS_URL")

# Log level
LOG_LEVEL = _get_env("LOG_LEVEL", "INFO")
