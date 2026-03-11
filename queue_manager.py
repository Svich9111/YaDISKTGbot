import asyncio
import aiohttp
import config
from loguru import logger
from yandex_disk import YandexDisk
from database import update_status, add_notification

class UploadQueue:
    def __init__(self, bot=None):
        self.queue = asyncio.Queue()
        self.disk = YandexDisk()
        self.workers = []
        self.bot = bot
        self.active_uploads = {}  # {unique_id: {"file_name": str, "progress": int, "status": str}}

    def set_bot(self, bot):
        self.bot = bot

    async def add_task(self, file_path, disk_path, unique_id, bot, chat_id=None, status_msg_id=None, yandex_token=None, file_size=None):
        file_name = disk_path.split('/')[-1]
        self.active_uploads[unique_id] = {
            "file_name": file_name,
            "progress": 0,
            "status": "pending"
        }
        await self.queue.put((file_path, disk_path, unique_id, bot, chat_id, status_msg_id, yandex_token, file_size))
        logger.info(f"Added task: {disk_path} | unique_id: {unique_id} | chat_id: {chat_id} | size: {file_size}")

    async def update_progress_message(self, bot, chat_id, message_id, text):
        """Безопасное обновление сообщения статуса"""
        if not (bot and chat_id and message_id):
            return
        try:
            await bot.edit_message_text(text, chat_id, message_id)
        except Exception as e:
            logger.warning(f"Failed to update status message: {e}")

    async def worker(self):
        while True:
            file_path, disk_path, unique_id, bot, chat_id, status_msg_id, yandex_token, file_size = await self.queue.get()
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
                file_name = disk_path.split('/')[-1]
                await self.update_progress_message(
                    bot, chat_id, status_msg_id,
                    f"📥 Скачивание файла **{file_name}**...\n📂 В очереди: {queue_size}"
                )
                
                # 2. Создание папок
                folder = "/".join(disk_path.split("/")[:-1])
                if not await disk_client.create_folder(folder):
                    raise Exception("Failed to create folder structure")
                
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
                                file_name = disk_path.split('/')[-1]
                                await bot.edit_message_text(
                                    f"☁️ Загрузка **{file_name}** на Яндекс.Диск: {percent}%\n📂 В очереди: {q_size}",
                                    chat_id,
                                    status_msg_id,
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                # Игнорируем ошибки редактирования (например, если сообщение не изменилось или удалено)
                                pass

                for attempt in range(config.RETRY_ATTEMPTS):
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
                                    raise Exception(f"Telegram download failed with status {resp.status}")
                                async for chunk in resp.content.iter_chunked(chunk_size):
                                    bytes_read += len(chunk)
                                    if file_size:
                                        await progress_callback(bytes_read, file_size)
                                    yield chunk

                    if await disk_client.upload_file_stream(telegram_stream, disk_path):
                        success = True
                        break
                    logger.warning(f"Upload failed, retrying ({attempt+1}/{config.RETRY_ATTEMPTS})...")
                    await asyncio.sleep(config.RETRY_DELAYS[attempt])
                
                # 5. Финальный статус
                if success:
                    await update_status(unique_id, "success")
                    file_name = disk_path.split('/')[-1]
                    try:
                        await bot.edit_message_text(f"✅ Файл загружен: **{file_name}**", chat_id, status_msg_id, parse_mode="Markdown")
                    except Exception:
                        # Если не удалось отредактировать, отправляем новое
                        msg = await bot.send_message(chat_id, f"✅ Файл загружен: **{file_name}**", parse_mode="Markdown")
                        await add_notification(chat_id, msg.message_id)
                    logger.success(f"Uploaded: {disk_path}")
                else:
                    raise Exception("Upload failed after retries")

            except Exception as e:
                logger.exception(f"Worker error: {e}")
                await update_status(unique_id, "error", reason=str(e))
                file_name = disk_path.split('/')[-1]
                await self.update_progress_message(
                    bot, chat_id, status_msg_id,
                    f"❌ Ошибка при загрузке **{file_name}**: {str(e)}"
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

    def start_workers(self):
        for _ in range(config.MAX_CONCURRENT_UPLOADS):
            task = asyncio.create_task(self.worker())
            self.workers.append(task)