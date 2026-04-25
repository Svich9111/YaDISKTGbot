import asyncio
import sys
import aiohttp
import sentry_sdk
from aiogram import Bot
from aiogram.types import BotCommand
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from loguru import logger
import config
from handlers import router
from database import (
    init_db, get_pending_files_with_ids, get_expired_notifications,
    delete_notification,
)
from queue_manager import UploadQueue
from loader import dp, queue
from web_server import run_web_server, stop_web_server, set_bot_running, set_db_healthy

# Sentry initialization
if config.SENTRY_DSN:
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    logger.info("Sentry initialized")

# Настройка loguru для cloud-совместимого логирования (stdout only)
logger.remove()  # Remove default handler
logger.add(
    sys.stdout,
    level=config.LOG_LEVEL,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
        "<level>{message}</level>"
    ),
    enqueue=True,
)


async def self_ping():
    """Фоновая задача для предотвращения засыпания на Render.com"""
    if not config.WEBHOOK_HOST:
        return

    url = f"{config.WEBHOOK_HOST}/health"
    logger.info(f"Self-ping task started for: {url}")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        logger.info("Self-ping successful (200 OK)")
                    else:
                        logger.warning(f"Self-ping returned status: {resp.status}")
            except Exception as e:
                logger.error(f"Self-ping failed: {e}")
            
            # Пингуем каждые 10 минут (600 секунд)
            await asyncio.sleep(600)


async def cleanup_notifications(bot: Bot):
    """Фоновая задача для очистки старых уведомлений"""
    while True:
        try:
            # Получаем уведомления старше 6 часов
            expired = await get_expired_notifications(hours=6)
            if expired:
                logger.info(f"Found {len(expired)} expired notifications to delete")
                for notif_id, chat_id, message_id in expired:
                    try:
                        await bot.delete_message(chat_id, message_id)
                        logger.info(f"Deleted notification message {message_id} in chat {chat_id}")
                    except Exception:
                        logger.warning(
                            f"Failed to delete message {message_id}",
                        )
                    finally:
                        # Удаляем из БД даже если не удалось удалить в телеграме
                        # (например, сообщение уже удалено)
                        await delete_notification(notif_id)

            # Проверяем раз в час
            await asyncio.sleep(3600)
        except Exception:
            logger.exception("Error in cleanup task")
            await asyncio.sleep(3600)


async def on_startup(bot: Bot, queue: UploadQueue):
    """Действия при запуске бота"""
    await init_db()
    set_db_healthy(True)

    # Настройка Webhook если URL задан
    if config.WEBHOOK_URL:
        await bot.set_webhook(
            url=config.WEBHOOK_URL,
            drop_pending_updates=False,
            allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
        )
        logger.info(f"Webhook set to: {config.WEBHOOK_URL}")
    else:
        # Принудительно удаляем вебхук и сбрасываем старые обновления для устранения конфликтов
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted (with drop_pending_updates), using polling")

    # Установка команд бота
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="status", description="Статус загрузки и диска"),
        BotCommand(command="configure", description="Настройка токена и папки"),
        BotCommand(command="clear_queue", description="Очистить очередь (Админ)"),
        BotCommand(command="export_all", description="Информация об экспорте (Админ)")
    ]
    await bot.set_my_commands(commands)

    # Запуск задачи очистки уведомлений
    asyncio.create_task(cleanup_notifications(bot))
    
    # Запуск задачи само-пинга
    asyncio.create_task(self_ping())

    # Восстановление очереди
    try:
        pending_files = await get_pending_files_with_ids()
        if pending_files:
            logger.info(f"Restoring {len(pending_files)} pending files from database...")
            restored_count = 0
            
            # Импортируем функцию обновления статуса
            from database import update_status
            import datetime
            
            from datetime import datetime, timedelta
            
            for file_data in pending_files:
                # telegram_file_id, telegram_file_unique_id, disk_path, chat_id, message_id, file_size, created_at
                telegram_file_id = file_data[0]
                unique_id = file_data[1]
                disk_path = file_data[2]
                chat_id = file_data[3]
                message_id = file_data[4]
                file_size = file_data[5]
                created_at_str = file_data[6]

                # Проверка возраста файла
                try:
                    created_at = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                    if datetime.now() - created_at > timedelta(hours=12):
                        logger.warning(f"Skipping old file {unique_id} (created at {created_at_str})")
                        await update_status(unique_id, "error", reason="Restore timeout (older than 12h)")
                        continue
                except Exception as e:
                    logger.error(f"Error parsing date for {unique_id}: {e}")

                try:
                    file = await bot.get_file(telegram_file_id)
                    await queue.add_task(
                        file.file_path,
                        disk_path,
                        unique_id,
                        bot,
                        chat_id,
                        message_id,
                        None,  # yandex_token (будет выбран дефолтный)
                        file_size  # размер файла для стриминга
                    )
                    restored_count += 1
                except Exception as e:
                    logger.error(f"Failed to restore file {unique_id}: {e}")
            logger.info(f"Successfully restored {restored_count} files to queue")
                # telegram_file_id, telegram_file_unique_id, disk_path, chat_id, message_id, file_size
                telegram_file_id = file_data[0]
                unique_id = file_data[1]
                disk_path = file_data[2]
                chat_id = file_data[3]
                message_id = file_data[4]
                file_size = file_data[5]

                try:
                    file = await bot.get_file(telegram_file_id)
                    await queue.add_task(
                        file.file_path,
                        disk_path,
                        unique_id,
                        bot,
                        chat_id,
                        message_id,
                        None,  # yandex_token (будет выбран дефолтный)
                        file_size  # размер файла для стриминга
                    )
                    restored_count += 1
                except Exception as e:
                    logger.error(f"Failed to restore file {unique_id}: {e}")
            logger.info(f"Successfully restored {restored_count} files to queue")
    except Exception as e:
        logger.error(f"Error restoring queue from DB: {e}")

    logger.info("Bot started successfully")


async def main():
    # Подключение роутера
    dp.include_router(router)

    # Запуск воркеров очереди
    queue.start_workers()

    retry_delay = 5
    max_retries = 100

    # Запуск веб-сервера для health checks и webhooks (один раз при старте)
    # Мы создаем временный объект бота для инициализации сервера, 
    # но в режиме вебхуков он будет обновляться.
    temp_bot = Bot(token=config.BOT_TOKEN)
    web_runner = await run_web_server(temp_bot, dp)
    await temp_bot.session.close()

    for attempt in range(max_retries):
        bot = None
        try:
            logger.info(f"Starting bot session (attempt {attempt + 1})")
            
            # Инициализация бота с поддержкой локального сервера API
            from aiogram.client.default import DefaultBotProperties
            from aiogram.client.telegram import TelegramAPIServer
            
            if config.TELEGRAM_API_URL:
                # Если задан локальный сервер, используем его
                server = TelegramAPIServer.from_base(config.TELEGRAM_API_URL)
                bot = Bot(
                    token=config.BOT_TOKEN,
                    server=server,
                    default=DefaultBotProperties(parse_mode="Markdown")
                )
                logger.info(f"Using Local Bot API Server: {config.TELEGRAM_API_URL}")
            else:
                # Стандартный сервер Telegram
                bot = Bot(
                    token=config.BOT_TOKEN,
                    default=DefaultBotProperties(parse_mode="Markdown")
                )
            
            queue.set_bot(bot)

            await on_startup(bot, queue)
            set_bot_running(True)

            if config.WEBHOOK_URL:
                # В режиме вебхуков просто ждем, пока веб-сервер работает
                logger.info("Bot is running in Webhook mode")
                while True:
                    await asyncio.sleep(3600)
            else:
                # Запуск поллинга с таймаутом для предотвращения зависаний
                logger.info("Bot is running in Polling mode")
                await dp.start_polling(bot, handle_signals=False, polling_timeout=30)

        except TelegramConflictError:
            logger.warning("Telegram conflict error (another instance running). Retrying...")
            if bot:
                await bot.session.close()
            set_bot_running(False)
            await asyncio.sleep(retry_delay)
            continue

        except TelegramAPIError as e:
            logger.error(f"Telegram API error: {e}")
            if bot:
                await bot.session.close()
            set_bot_running(False)
            await asyncio.sleep(retry_delay)
            continue

        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            if bot:
                await bot.session.close()
            set_bot_running(False)
            await asyncio.sleep(retry_delay)
            continue

        finally:
            if bot:
                await bot.session.close()
            set_bot_running(False)
            logger.info("Bot session closed")

    # Cleanup web server on exit
    if 'web_runner' in locals():
        await stop_web_server(web_runner)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
