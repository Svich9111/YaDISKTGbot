import asyncio
import logging
import sys
from aiogram import Bot
from aiogram.types import BotCommand
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
import config
from handlers import router
from database import init_db, get_pending_files_with_ids, get_expired_notifications, delete_notification
from queue_manager import UploadQueue
from loader import dp, queue

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

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
                    except Exception as e:
                        logger.warning(f"Failed to delete message {message_id}: {e}")
                    finally:
                        # Удаляем из БД даже если не удалось удалить в телеграме (например, сообщение уже удалено)
                        await delete_notification(notif_id)
            
            # Проверяем раз в час
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")
            await asyncio.sleep(3600)

async def on_startup(bot: Bot, queue: UploadQueue):
    """Действия при запуске бота"""
    await init_db()
    
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
    
    # Восстановление очереди
    try:
        pending_files = await get_pending_files_with_ids()
        if pending_files:
            logger.info(f"Restoring {len(pending_files)} pending files from database...")
            restored_count = 0
            for file_data in pending_files:
                telegram_file_id, unique_id, disk_path, chat_id, message_id = file_data
                try:
                    file = await bot.get_file(telegram_file_id)
                    await queue.add_task(
                        file.file_path, 
                        disk_path, 
                        unique_id, 
                        bot, 
                        chat_id, 
                        message_id,
                        None,        # yandex_token (будет выбран дефолтный)
                        getattr(file, "file_size", None)  # размер файла для стриминга
                    )
                    restored_count += 1
                except Exception as e:
                    logger.error(f"Failed to restore file {unique_id}: {e}")
            logger.info(f"Successfully restored {restored_count} files to queue")
    except Exception as e:
        logger.error(f"Error restoring queue from DB: {e}")

    # Не удаляем pending updates, чтобы бот мог обработать сообщения, пришедшие во время простоя
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Bot started successfully")

async def main():
    # Подключение роутера
    dp.include_router(router)
    
    # Запуск воркеров очереди
    queue.start_workers()

    retry_delay = 5
    max_retries = 100

    for attempt in range(max_retries):
        bot = None
        try:
            logger.info(f"Starting bot session (attempt {attempt + 1})")
            bot = Bot(token=config.BOT_TOKEN)
            queue.set_bot(bot)
            
            await on_startup(bot, queue)
            
            # Запуск поллинга с таймаутом для предотвращения зависаний
            await dp.start_polling(bot, handle_signals=True, polling_timeout=30)
            
        except TelegramConflictError:
            logger.warning("Telegram conflict error (another instance running). Retrying...")
            if bot:
                await bot.session.close()
            await asyncio.sleep(retry_delay)
            continue
            
        except TelegramAPIError as e:
            logger.error(f"Telegram API error: {e}")
            if bot:
                await bot.session.close()
            await asyncio.sleep(retry_delay)
            continue
            
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            if bot:
                await bot.session.close()
            await asyncio.sleep(retry_delay)
            continue
            
        finally:
            if bot:
                await bot.session.close()
            logger.info("Bot session closed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")