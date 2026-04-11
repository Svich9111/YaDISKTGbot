import asyncio
import aiohttp
import sys
import config
from database import init_db
from loguru import logger


async def check_yandex_disk():
    """Проверка доступности Яндекс.Диска"""
    url = "https://cloud-api.yandex.net/v1/disk"
    token = config.YANDEX_DISK_TOKEN
    if not token.startswith("OAuth"):
        token = f"OAuth {token}"
    headers = {"Authorization": token}
    try:
        # Disable SSL verification for healthcheck
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    logger.info("✅ Yandex Disk API is accessible")
                    return True
                else:
                    logger.error(f"❌ Yandex Disk API error: {resp.status}")
                    return False
    except Exception:
        logger.exception("❌ Yandex Disk connection failed")
        return False


async def check_telegram():
    """Проверка токена Telegram"""
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe"
    try:
        # Disable SSL verification for healthcheck
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    logger.info("✅ Telegram Bot API is accessible")
                    return True
                else:
                    logger.error(f"❌ Telegram Bot API error: {resp.status}")
                    return False
    except Exception:
        logger.exception("❌ Telegram connection failed")
        return False


async def check_db():
    """Проверка базы данных"""
    try:
        await init_db()
        logger.info("✅ Database is accessible")
        return True
    except Exception:
        logger.exception("❌ Database error")
        return False


async def main():
    logger.info("Starting health check...")

    results = await asyncio.gather(
        check_yandex_disk(),
        check_telegram(),
        check_db(),
    )

    if all(results):
        logger.info("🚀 All systems operational!")
        sys.exit(0)
    else:
        logger.error("⚠️ Some systems are down!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
