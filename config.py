import os
from dotenv import dotenv_values

config = dotenv_values(".env")

BOT_TOKEN = config.get("BOT_TOKEN")
YANDEX_DISK_TOKEN = config.get("YANDEX_DISK_TOKEN")
ADMIN_ID = int(config.get("ADMIN_ID", 0))
DB_NAME = "bot_database.db"
LOG_FILE = "bot.log"

# Yandex Disk paths
ROOT_FOLDER = "Фото из телеграмма"
EXPORT_FOLDER = f"{ROOT_FOLDER}/Полный архив"

# Retry settings
RETRY_ATTEMPTS = 3
RETRY_DELAYS = [5, 30, 120]  # seconds

# Queue settings
MAX_CONCURRENT_UPLOADS = 5

# File size limit (2GB)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024