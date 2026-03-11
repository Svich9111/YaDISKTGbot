import asyncio
import config
from yandex_disk import YandexDisk
from loguru import logger
import sys

# Configure logger to stdout
logger.remove()
logger.add(sys.stdout, level="INFO")

async def test_connection():
    logger.info("Testing Yandex Disk connection...")
    try:
        disk = YandexDisk()
        info = await disk.get_disk_info()
        
        if info:
            logger.success("Connection successful!")
            total_space = info.get('total_space', 0) / (1024**3)
            used_space = info.get('used_space', 0) / (1024**3)
            logger.info(f"Total Space: {total_space:.2f} GB")
            logger.info(f"Used Space: {used_space:.2f} GB")
            return True
        else:
            logger.error("Failed to get disk info. Check token.")
            return False
    except Exception as e:
        logger.exception(f"Connection error: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_connection())