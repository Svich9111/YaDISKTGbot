import asyncio
import aiohttp
import config
from loguru import logger
from yandex_disk import YandexDisk
from database import update_status, add_notification


class UploadQueue:
    def __init__(self, bot=None):
        self.queue = asyncio.PriorityQueue()
        self.disk = YandexDisk()
        self.workers = []
        self.bot = bot
        self.active_uploads = {}
        # {unique_id: {"file_name": str, "progress": int, "status": str}}

    def set_bot(self, bot):
        self.bot = bot

    async def add_task(
        self, file_path, disk_path, unique_id, bot,
        chat_id=None, status_msg_id=None,
        yandex_token=None, file_size=None,
    ):
        file_name = disk_path.split("/")[-1]
        self.active_uploads[unique_id] = {
            "file_name": file_name,
            "progress": 0,
            "status": "pending",
        }
        # Priority: smaller files first. Use file_size as priority (0 if None)
        priority = file_size if file_size else float("inf")
        await self.queue.put((
            priority, (
                file_path, disk_path, unique_id, bot,
                chat_id, status_msg_id, yandex_token, file_size,
            ),
        ))
        logger.info(
            f"Added task: {disk_path} | unique_id: {unique_id} | "
            f"chat_id: {chat_id} | size: {file_size} | priority: {priority}",
        )

    async def update_progress_message(self, bot, chat_id, message_id, text):
        """Безопасное обновление сообщения статуса"""
        if not (bot and chat_id and message_id):
            return
        try:
            await bot.edit_message_text(text, chat_id, message_id)
        except Exception:
            logger.warning("Failed to update status message")

    async def worker(self):
        while True:
            priority, task_data = await self.queue.get()
            file_path, disk_path, unique_id, bot, chat_id, status_msg_id, yandex_token, file_size = task_data

            if bot is None:
                bot = self.bot

            # Используем токен чата или дефолтный
            disk_client = YandexDisk(token=yandex_token) if yandex_token else self.disk

            try:
                logger.info(f"Processing task: {disk_path} | unique_id: {unique_id}")

                if unique_id in self.active_uploads:
                    self.active_uploads[unique_id]["status"] = "downloading"

                # 1. Статус: Скачивание
                queue_size = self.queue.qsize()
                file_name = disk_path.split("/")[-1]
                safe_file_name = file_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                await self.update_progress_message(
                    bot, chat_id, status_msg_id,
                    f"📥 Скачивание файла **{safe_file_name}**...\n📂 В очереди: {queue_size}"
                )

                # 2. Создание папок
                folder = "/".join(disk_path.split("/")[:-1])
                if not await disk_client.create_folder(folder):
                    raise RuntimeError("Failed to create folder structure")

                # 3. Подготовка к потоковой загрузке
                success = False

                if unique_id in self.active_uploads:
                    self.active_uploads[unique_id]["status"] = "uploading"

                # Используем last_reported_percent, чтобы обновлять сообщение чаще и плавнее
                last_reported_percent = 0

                async def progress_callback(current, total):
                    nonlocal last_reported_percent
                    if not total:
                        return
                    percent = int(current / total * 100)

                    if unique_id in self.active_uploads:
                        self.active_uploads[unique_id]["progress"] = percent

                    if chat_id and status_msg_id:
                        # Обновляем каждые 5% или при 100% для более плавного прогресса
                        if percent >= last_reported_percent + 5 or percent == 100:
                            last_reported_percent = percent
                            q_size = self.queue.qsize()
                            # Текст всегда меняется, иначе Telegram выдаст "message is not modified"
                            try:
                                file_name = disk_path.split("/")[-1]
                                safe_file_name = file_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                                await bot.edit_message_text(
                                    f"☁️ Загрузка **{safe_file_name}** на Яндекс.Диск: {percent}%\n📂 В очереди: {q_size}",
                                    chat_id,
                                    status_msg_id,
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                # Игнорируем ошибки редактирования
                                pass

                for attempt in range(config.RETRY_ATTEMPTS):
                    logger.info(f"Upload attempt {attempt + 1}/{config.RETRY_ATTEMPTS} for {file_name}")
                    
                    async def telegram_stream():
                        """
                        Поток читается напрямую из Telegram и сразу отправляется в Яндекс.Диск.
                        """
                        chunk_size = 64 * 1024
                        bytes_read = 0
                        # Формируем URL файла Telegram
                        tg_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
                        connector = aiohttp.TCPConnector(ssl=False)
                        async with aiohttp.ClientSession(connector=connector) as session:
                            async with session.get(tg_url) as resp:
                                if resp.status != 200:
                                    logger.error(f"Telegram download failed: {resp.status} for {file_path}")
                                    raise RuntimeError(f"Telegram download failed with status {resp.status}")
                                async for chunk in resp.content.iter_chunked(chunk_size):
                                    bytes_read += len(chunk)
                                    if file_size:
                                        await progress_callback(bytes_read, file_size)
                                    yield chunk

                    try:
                        if await disk_client.upload_file_stream(telegram_stream, disk_path):
                            success = True
                            logger.success(f"Successfully uploaded {file_name} to Yandex.Disk")
                            break
                    except Exception as e:
                        logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                    logger.warning(
                        f"Upload failed, retrying "
                        f"({attempt+1}/{config.RETRY_ATTEMPTS})...",
                    )
                    await asyncio.sleep(config.RETRY_DELAYS[attempt])

                # 5. Финальный статус
                if success:
                    await update_status(unique_id, "success")
                    file_name = disk_path.split("/")[-1]
                    safe_file_name = file_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                    try:
                        await bot.edit_message_text(
                            f"✅ Файл загружен: **{safe_file_name}**",
                            chat_id, status_msg_id,
                            parse_mode="Markdown",
                        )
                    except Exception:
                        # Если не удалось отредактировать, отправляем новое
                        msg = await bot.send_message(
                            chat_id,
                            f"✅ Файл загружен: **{safe_file_name}**",
                            parse_mode="Markdown",
                        )
                        await add_notification(chat_id, msg.message_id)
                    logger.success(f"Uploaded: {disk_path}")
                else:
                    raise RuntimeError("Upload failed after retries")

            except Exception as exc:
                logger.exception("Worker error")
                await update_status(unique_id, "error", reason=str(exc))
                file_name = disk_path.split("/")[-1]
                safe_file_name = file_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                await self.update_progress_message(
                    bot, chat_id, status_msg_id,
                    f"❌ Ошибка при загрузке **{safe_file_name}**: {exc}",
                )
            finally:
                if unique_id in self.active_uploads:
                    del self.active_uploads[unique_id]
                self.queue.task_done()

    async def clear_queue(self):
        """Очистить очередь загрузки"""
        cleared = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        logger.info(f"Queue cleared, removed {cleared} tasks")
        return cleared

    async def restore_queue(self):
        """Восстановить очередь из БД при старте"""
        from database import get_pending_files_with_ids
        pending_files = await get_pending_files_with_ids()
        count = 0
        for row in pending_files:
            # (telegram_file_id, telegram_file_unique_id, disk_path,
            #  chat_id, message_id, file_size)
            # Note: We need to get file_path from telegram again,
            # but we only have file_id.
            # Since we can't easily get file_path without an API call,
            # we'll rely on the worker to handle it.
            # Actually, the worker expects a file_path (relative path
            # on TG server).
            # We need to fetch the file info again.
            try:
                if self.bot:
                    file_info = await self.bot.get_file(row[0])
                    file_path = file_info.file_path
                    await self.add_task(
                        file_path, row[2], row[1], self.bot, row[3], row[4], None, row[5]
                    )
                    count += 1
            except Exception:
                logger.error(f"Failed to restore task {row[1]}")
                await update_status(row[1], "error", reason="Restore failed")

        logger.info(f"Restored {count} tasks from database")

    def start_workers(self):
        for _ in range(config.MAX_CONCURRENT_UPLOADS):
            task = asyncio.create_task(self.worker())
            self.workers.append(task)
