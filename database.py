import aiosqlite
import config
from loguru import logger

async def init_db():
    async with aiosqlite.connect(config.DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_file_id TEXT NOT NULL,
                telegram_file_unique_id TEXT UNIQUE NOT NULL,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                file_name TEXT,
                disk_path TEXT,
                status TEXT DEFAULT 'pending',
                status_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                yandex_token TEXT,
                root_folder TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
        logger.info("Database initialized")

async def add_file(file_id, unique_id, msg_id, chat_id, f_type, f_name, path):
    async with aiosqlite.connect(config.DB_NAME) as db:
        try:
            await db.execute("""
                INSERT INTO files (telegram_file_id, telegram_file_unique_id, message_id, chat_id, file_type, file_name, disk_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (file_id, unique_id, msg_id, chat_id, f_type, f_name, path))
            await db.commit()
            logger.info(f"Added file to DB: {f_name} ({unique_id})")
            return True
        except aiosqlite.IntegrityError:
            logger.warning(f"File {unique_id} already exists in DB")
            return False

async def clear_pending_files():
    """Очистить все pending файлы из базы данных"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        cursor = await db.execute("DELETE FROM files WHERE status = 'pending'")
        await db.commit()
        cleared_count = cursor.rowcount
        logger.info(f"Cleared {cleared_count} pending files from DB")
        return cleared_count

async def update_status(unique_id, status, reason=None):
    async with aiosqlite.connect(config.DB_NAME) as db:
        if reason:
            await db.execute("UPDATE files SET status = ?, status_reason = ? WHERE telegram_file_unique_id = ?", (status, reason, unique_id))
        else:
            await db.execute("UPDATE files SET status = ? WHERE telegram_file_unique_id = ?", (status, unique_id))
        await db.commit()

async def get_pending_files():
    async with aiosqlite.connect(config.DB_NAME) as db:
        async with db.execute("SELECT * FROM files WHERE status = 'pending'") as cursor:
            return await cursor.fetchall()

async def get_pending_files_with_ids():
    """Получить pending файлы с telegram_file_id для восстановления очереди"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        async with db.execute(
            "SELECT telegram_file_id, telegram_file_unique_id, disk_path, chat_id, message_id FROM files WHERE status = 'pending'"
        ) as cursor:
            return await cursor.fetchall()

async def add_notification(chat_id, message_id):
    """Добавить уведомление для последующего удаления"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        await db.execute(
            "INSERT INTO notifications (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id)
        )
        await db.commit()

async def get_expired_notifications(hours=6):
    """Получить уведомления старше указанного количества часов"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        async with db.execute(
            f"SELECT id, chat_id, message_id FROM notifications WHERE created_at < datetime('now', '-{hours} hours')"
        ) as cursor:
            return await cursor.fetchall()

async def delete_notification(notification_id):
    """Удалить запись об уведомлении из БД"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        await db.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
        await db.commit()

async def get_chat_config(chat_id):
    """Получить настройки чата"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        async with db.execute("SELECT yandex_token, root_folder, admin_id FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchone()

async def get_all_chats():
    """Получить список всех чатов с настройками"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        async with db.execute(
            "SELECT chat_id, root_folder, admin_id, created_at FROM chats ORDER BY created_at DESC"
        ) as cursor:
            return await cursor.fetchall()


async def is_admin_of_chat(user_id, chat_id, bot) -> bool:
    """Проверяет, является ли user_id админом чата (в Telegram или в нашей БД)."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "creator"):
            return True
    except Exception:
        pass
    row = await get_chat_config(chat_id)
    return row is not None and row[2] == user_id

async def set_chat_config(chat_id, yandex_token=None, root_folder=None, admin_id=None):
    """Сохранить или обновить настройки чата"""
    async with aiosqlite.connect(config.DB_NAME) as db:
        # Проверяем существование
        async with db.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            exists = await cursor.fetchone()
        
        if exists:
            updates = []
            params = []
            if yandex_token is not None:
                updates.append("yandex_token = ?")
                params.append(yandex_token)
            if root_folder is not None:
                updates.append("root_folder = ?")
                params.append(root_folder)
            if admin_id is not None:
                updates.append("admin_id = ?")
                params.append(admin_id)
            
            if updates:
                params.append(chat_id)
                await db.execute(f"UPDATE chats SET {', '.join(updates)} WHERE chat_id = ?", params)
        else:
            await db.execute(
                "INSERT INTO chats (chat_id, yandex_token, root_folder, admin_id) VALUES (?, ?, ?, ?)",
                (chat_id, yandex_token, root_folder, admin_id)
            )
        await db.commit()