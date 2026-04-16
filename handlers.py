from aiogram import Router, F
from aiogram.types import Message, ChatMemberUpdated
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.exceptions import TelegramBadRequest
import config
from database import (
    add_file, get_pending_files, clear_pending_files, add_notification,
    get_chat_config, set_chat_config, get_all_chats, is_admin_of_chat,
    check_duplicate_file,
)
import time
from yandex_disk import YandexDisk
from loguru import logger
from loader import queue, dp
import mimetypes
import urllib.parse
import hashlib

router = Router()
start_time = time.time()


async def can_use_admin_commands(message: Message) -> bool:
    """Может ли пользователь использовать команды админа: владелец бота или админ текущего чата."""
    if message.from_user.id == config.ADMIN_ID:
        return True
    if message.chat.type != "private":
        return await is_admin_of_chat(message.from_user.id, message.chat.id, message.bot)
    chats = await get_all_chats()
    for row in chats:
        if row[2] == message.from_user.id:
            return True
    return False


class SetupStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_folder = State()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> IS_MEMBER))
async def on_user_join(event: ChatMemberUpdated, bot):
    """Бот добавлен в новый чат"""
    chat_id = event.chat.id
    chat_title = event.chat.title or "Private Chat"

    # Проверяем, есть ли уже конфиг
    chat_config = await get_chat_config(chat_id)
    if not chat_config:
        # Устанавливаем дефолтные значения
        await set_chat_config(
            chat_id, root_folder=chat_title, admin_id=event.from_user.id,
        )

        await bot.send_message(
            chat_id,
            f"👋 Привет! Я добавлен в чат **{chat_title}**.\n"
            f"📂 Папка для загрузки по умолчанию: `{chat_title}`\n"
            f"🔑 Токен Яндекс.Диска используется общий (из настроек бота).\n\n"
            f"Чтобы настроить свой токен или изменить папку, используйте команду /configure"
        )


@router.message(Command("configure"))
async def configure_command(message: Message, state: FSMContext):
    """Настройка бота для текущего чата"""
    # Разрешаем настройку только админам чата или владельцу бота
    chat_member = await message.bot.get_chat_member(
        message.chat.id, message.from_user.id,
    )
    is_not_private = message.chat.type != "private"
    is_not_admin = chat_member.status not in ("administrator", "creator")
    is_not_owner = message.from_user.id != config.ADMIN_ID
    if is_not_private and is_not_admin and is_not_owner:
        await message.reply("❌ Настройка доступна только администраторам чата.")
        return

    # Если команда вызвана в группе, просим перейти в ЛС
    if message.chat.type != "private":
        try:
            # Отправляем инструкцию в ЛС
            await message.bot.send_message(
                message.from_user.id,
                f"⚙️ **Настройка интеграции для чата: {message.chat.title}**\n\n"
                "Для работы бота необходимо указать OAuth токен Яндекс.Диска.\n\n"
                "📝 **Как получить токен:**\n"
                "1. Перейдите по ссылке: "
                "https://oauth.yandex.ru/authorize?response_type=token"
                "&client_id=YOUR_CLIENT_ID\n"
                "   *(Если у вас нет своего приложения, создайте его на https://oauth.yandex.ru/)*\n"
                "2. Разрешите доступ приложению.\n"
                "3. Скопируйте полученный токен.\n\n"
                "🆔 **Ваш Telegram ID:**\n"
                f"`{message.from_user.id}`\n"
                "*(Этот ID используется для настройки прав администратора)*\n\n"
                "👇 **Введите ваш OAuth токен Яндекс.Диска:**",
                parse_mode="Markdown"
            )
            # Создаём/обновляем состояние в ЛС пользователя (чтобы дальнейшие сообщения в ЛС ловил FSM)
            storage = dp.storage
            pm_key = StorageKey(
                bot_id=message.bot.id,
                chat_id=message.from_user.id,
                user_id=message.from_user.id,
            )
            pm_state = FSMContext(storage=storage, key=pm_key)
            await pm_state.update_data(configuring_chat_id=message.chat.id)
            await pm_state.set_state(SetupStates.waiting_for_token)

            await message.reply(
                "📩 Инструкция по настройке отправлена вам в личные сообщения.",
            )
        except Exception:
            await message.reply(
                "❌ Не удалось отправить сообщение в ЛС. "
                "Пожалуйста, сначала напишите боту /start в личные сообщения, "
                "а затем повторите команду.",
            )
        return

    # Если команда вызвана в ЛС, настраиваем этот же чат (ЛС) или просим выбрать группу?
    # Пока оставим настройку текущего чата (ЛС)
    await message.reply(
        "⚙️ **Настройка интеграции**\n\n"
        "Для работы бота необходимо указать OAuth токен Яндекс.Диска.\n\n"
        "📝 **Как получить токен:**\n"
        "1. Перейдите по ссылке: https://oauth.yandex.ru/authorize"
        "?response_type=token&client_id=YOUR_CLIENT_ID\n"
        "   *(Если у вас нет своего приложения, создайте его на https://oauth.yandex.ru/)*\n"
        "2. Разрешите доступ приложению.\n"
        "3. Скопируйте полученный токен.\n\n"
        "🆔 **Ваш Telegram ID:**\n"
        f"`{message.from_user.id}`\n"
        "*(Этот ID используется для настройки прав администратора)*\n\n"
        "👇 **Введите ваш OAuth токен Яндекс.Диска:**",
        parse_mode="Markdown"
    )
    # Если настраиваем ЛС, то configuring_chat_id = message.chat.id
    await state.update_data(configuring_chat_id=message.chat.id)
    await state.set_state(SetupStates.waiting_for_token)


@router.message(SetupStates.waiting_for_token)
async def process_token(message: Message, state: FSMContext):
    token = message.text.strip()
    if token.lower() == "skip":
        await message.reply(
            "❌ Токен обязателен. Пожалуйста, введите токен Яндекс.Диска:",
        )
        return

    # Проверяем токен Яндекс.Диска перед сохранением
    try:
        disk = YandexDisk(token=token)
        disk_info = await disk.get_disk_info()
        if not disk_info:
            await message.reply(
                "❌ Не удалось подключиться к Яндекс.Диску с указанным токеном.\n"
                "Проверьте токен и отправьте его ещё раз."
            )
            return
    except Exception:
        logger.exception("Error while validating Yandex token")
        await message.reply(
            "❌ Произошла ошибка при проверке токена Яндекс.Диска.\n"
            "Попробуйте ещё раз чуть позже или проверьте корректность токена."
        )
        return

    # Чат, который мы настраиваем (может быть группой, даже если пользователь пишет в ЛС)
    data = await state.get_data()
    configuring_chat_id = data.get("configuring_chat_id", message.chat.id)

    # Сохраняем токен и обновляем администратора для этого чата.
    # Одновременно очищаем сохранённый путь к папке, чтобы новый токен
    # всегда настраивался с "чистой" папкой (путь будет выбран на следующем шаге).
    await set_chat_config(
        configuring_chat_id,
        yandex_token=token,
        root_folder=None,
        admin_id=message.from_user.id
    )
    # Формируем сообщение об успешной проверке/сохранении
    msg_lines = ["✅ Токен Яндекс.Диска проверен и сохранён."]
    try:
        total_space = disk_info.get("total_space", 0) / (1024**3)
        used_space = disk_info.get("used_space", 0) / (1024**3)
        free_space = total_space - used_space
        msg_lines.append(
            f"☁️ Диск: всего {total_space:.2f} GB, свободно {free_space:.2f} GB."
        )
    except Exception:
        pass
    msg = "\n".join(msg_lines)

    await message.reply(f"{msg}\nВведите название корневой папки для этого чата (или 'skip' для текущей):")
    await state.set_state(SetupStates.waiting_for_folder)


@router.message(SetupStates.waiting_for_folder)
async def process_folder(message: Message, state: FSMContext):
    folder = message.text.strip()
    # Чат, который мы настраиваем (группа/канал), а не ЛС
    data = await state.get_data()
    configuring_chat_id = data.get("configuring_chat_id", message.chat.id)

    # Всегда фиксируем админа чата для уведомлений — тот, кто завершил настройку в ЛС
    await set_chat_config(configuring_chat_id, admin_id=message.from_user.id)

    link_text = ""

    if folder.lower() != 'skip':
        # Убираем слеши чтобы не ломать пути
        folder = folder.replace("/", "_").replace("\\", "_")
        await set_chat_config(configuring_chat_id, root_folder=folder)
        msg = f"✅ Папка изменена на: `{folder}`"

        # Пытаемся создать корневую папку на Яндекс.Диске для этого чата
        try:
            chat_config = await get_chat_config(configuring_chat_id)
            token = chat_config[0] if chat_config else None
            disk = YandexDisk(token=token) if token else YandexDisk()
            created_ok = await disk.create_folder(folder)
            if created_ok:
                encoded = urllib.parse.quote(folder)
                folder_url = f"https://disk.yandex.ru/client/disk/{encoded}"
                link_text = (
                    f"\n📂 Папка на Яндекс.Диске: `{folder}`\n"
                    f"[Открыть в Яндекс.Диске]({folder_url})"
                )
        except Exception as e:
            logger.warning(f"Failed to create root folder '{folder}' on Yandex.Disk: {e}")
    else:
        msg = "⏩ Папка оставлена без изменений."

    # Сообщаем в ЛС, какой чат был настроен
    target_chat_id = configuring_chat_id
    target_chat_text = f"ID чата: `{target_chat_id}`"
    try:
        chat = await message.bot.get_chat(target_chat_id)
        if chat.title:
            target_chat_text = f"чат: **{chat.title}** (ID: `{target_chat_id}`)"
    except Exception:
        pass

    await message.reply(
        f"{msg}{link_text}\n\n🎉 Настройка завершена!\n"
        f"Теперь файлы из {target_chat_text} будут загружаться с новыми параметрами.",
        parse_mode="Markdown",
    )

    # Пытаемся отправить короткое подтверждение прямо в настраиваемый чат (если это не ЛС)
    try:
        if target_chat_id != message.chat.id:
            await message.bot.send_message(
                target_chat_id,
                "✅ Настройка интеграции с Яндекс.Диском завершена.\n"
                "Теперь файлы из этого чата будут автоматически загружаться на Диск."
            )
    except Exception:
        pass

    await state.clear()


@router.message(Command("start"))
async def start_command(message: Message):
    welcome_text = (
        "👋 **Привет! Я бот для загрузки файлов на Яндекс.Диск.**\n\n"
        "Я умею автоматически сохранять фото, видео и документы из этого чата в вашу папку на Диске.\n\n"
        "🚀 **Как начать:**\n"
        "1. Добавьте меня в чат (если это группа).\n"
        "2. Назначьте меня администратором (для доступа к сообщениям).\n"
        "3. Введите команду /configure для настройки.\n\n"
        "⚙️ **Настройка (/configure):**\n"
        "- Вам понадобится **OAuth токен** Яндекс.Диска.\n"
        "- Вы сможете выбрать **папку** для сохранения файлов.\n\n"
        "📋 **Команды:**\n"
        "/configure - Настройка токена и папки\n"
        "/status - Проверка статуса подключения\n"
    )

    # Дополнительные команды для владельца бота и админов чатов
    if await can_use_admin_commands(message):
        welcome_text += (
            "\n👑 **Админ-панель:**\n"
            "/status - Статус и очередь\n"
            "/clear_queue - Очистить очередь\n"
            "/export_all - Инфо об экспорте\n"
        )

    await message.reply(welcome_text, parse_mode="Markdown")


def _is_global_owner(user_id):
    return user_id == config.ADMIN_ID


@router.message(Command("status"))
async def status_command(message: Message):
    if not await can_use_admin_commands(message):
        return

    uptime = time.time() - start_time
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)

    disk = YandexDisk()
    disk_info = await disk.get_disk_info()

    pending_files = await get_pending_files()
    queue_size = len(pending_files)

    status_text = (
        f"🤖 **Bot Status**\n"
        f"⏱ Uptime: {int(hours)}h {int(minutes)}m {int(seconds)}s\n"
        f"📂 Queue size: {queue_size}\n"
    )

    if pending_files:
        status_text += "\n**Pending Files:**\n"

        # Получаем активные загрузки из очереди
        active_uploads = queue.active_uploads if queue else {}

        for file in pending_files[:5]:  # Show top 5
            # file structure: (id, telegram_file_id, telegram_file_unique_id, message_id,
            # chat_id, file_type, file_name, disk_path, status, status_reason,
            # file_size, file_hash, created_at)
            # Note: indices shifted because of new columns
            unique_id = file[2]
            file_name = file[6]

            if unique_id in active_uploads:
                upload_info = active_uploads[unique_id]
                status = upload_info.get("status")
                if status == "uploading":
                    progress = upload_info.get("progress", 0)
                    status_text += f"- {file_name} - {progress}%\n"
                elif status == "downloading":
                    status_text += f"- {file_name} - Скачивание...\n"
                else:
                    status_text += f"- {file_name} - В обработке\n"
            else:
                status_text += f"- {file_name} - ожидает загрузки\n"

        if len(pending_files) > 5:
            status_text += f"... and {len(pending_files) - 5} more\n"
    else:
        status_text += "\n**Pending Files:** None\n"

    if disk_info:
        total_space = disk_info.get('total_space', 0) / (1024**3)
        used_space = disk_info.get('used_space', 0) / (1024**3)
        free_space = total_space - used_space

        # Получаем настройки чата для ссылки на папку
        chat_config = await get_chat_config(message.chat.id)
        root_folder = chat_config[1] if chat_config and chat_config[1] else config.ROOT_FOLDER
        
        # Формируем ссылку на корень диска или конкретную папку
        # Яндекс.Диск ожидает путь в формате /disk/path/to/folder
        encoded_folder = urllib.parse.quote(root_folder)
        folder_url = f"https://disk.yandex.ru/client/disk/{encoded_folder}"

        status_text += (
            f"☁️ **Yandex Disk**\n"
            f"Total: {total_space:.2f} GB\n"
            f"Used: {used_space:.2f} GB\n"
            f"Free: {free_space:.2f} GB\n"
            f"📂 [Открыть папку бота]({folder_url})\n"
        )
    else:
        status_text += "☁️ **Yandex Disk**: ❌ Error connecting\n"

    # Проверка доставки уведомлений в ЛС (без упоминания ID создателя для админов чата)
    target_dm_id = config.ADMIN_ID if _is_global_owner(message.from_user.id) else message.from_user.id
    try:
        await message.bot.send_message(
            target_dm_id,
            (
                "🔔 Проверка доставки уведомлений (/status). "
                "Если вы видите это — уведомления о загрузках "
                "будут приходить в ЛС."
            ),
        )
        status_text += "\n✅ **ЛС (уведомления):** бот может отправлять сообщения в личку."
    except Exception as e:
        status_text += (
            "\n⚠️ **ЛС (уведомления):** не удалось отправить сообщение.\n"
            "Напишите боту /start в личные сообщения — тогда сюда будут приходить уведомления о загрузках."
        )
        logger.warning(f"Cannot send DM to {target_dm_id}: {e}")

    # Информация о чатах, где настроен бот
    chats = await get_all_chats()
    if chats:
        status_text += "\n📌 **Чаты с настроенной интеграцией:**\n"
        # Ограничим вывод, чтобы не раздувать сообщение
        max_chats_to_show = 10
        shown = 0
        for chat_row in chats:
            if shown >= max_chats_to_show:
                break
            chat_id, root_folder, admin_id, created_at = chat_row
            chat_label = f"ID {chat_id}"
            try:
                chat_obj = await message.bot.get_chat(chat_id)
                if getattr(chat_obj, "title", None):
                    chat_label = chat_obj.title
                elif getattr(chat_obj, "username", None):
                    chat_label = f"@{chat_obj.username}"
            except Exception:
                pass

            folder_display = root_folder or config.ROOT_FOLDER
            status_text += (
                    f"- {chat_label} (ID: `{chat_id}`), "
                    f"папка: `{folder_display}`\n"
                )
            shown += 1

        if len(chats) > max_chats_to_show:
            status_text += f"... и ещё {len(chats) - max_chats_to_show} чатов\n"

    # Добавляем ссылки на диагностику для админа
    if message.from_user.id == config.ADMIN_ID:
        webhook_host = config.WEBHOOK_HOST or "http://localhost:10000"
        status_text += (
            f"\n🛠 **Диагностика сервера:**\n"
            f"🔗 [Health Check]({webhook_host}/health)\n"
            f"🔗 [Ready Check]({webhook_host}/ready)\n"
            f"🔗 [Root API]({webhook_host}/)\n"
        )

    await message.reply(status_text, parse_mode="Markdown")


@router.message(Command("clear_queue"))
async def clear_queue_command(message: Message):
    if not await can_use_admin_commands(message):
        return

    if not queue:
        await message.reply("❌ Ошибка: Очередь загрузки не инициализирована.")
        return

    cleared_count = await queue.clear_queue()
    db_cleared = await clear_pending_files()

    await message.reply(
        f"🗑 Очередь очищена\n"
        f"📂 Удалено из очереди: {cleared_count}\n"
        f"💾 Удалено из БД: {db_cleared}"
    )


@router.message(F.content_type.in_({'photo', 'video', 'document', 'audio', 'voice', 'video_note', 'animation'}))
async def handle_file(message: Message):
    global queue
    if not queue:
        # Если очередь не инициализирована, попробуем получить её из loader
        from loader import queue as q
        if not q:
            await message.reply("❌ Ошибка: Очередь загрузки не инициализирована.")
            return
        queue = q

    file_id = None
    unique_id = None
    file_name = None
    file_type = None
    mime_type = None

    # Определение типа файла и извлечение данных
    if message.photo:
        file_id = message.photo[-1].file_id
        unique_id = message.photo[-1].file_unique_id
        file_name = f"photo_{unique_id}.jpg"
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        unique_id = message.video.file_unique_id
        file_name = message.video.file_name or f"video_{unique_id}.mp4"
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        unique_id = message.document.file_unique_id
        mime_type = message.document.mime_type
        file_type = "video" if mime_type and mime_type.startswith("video/") else "document"
        file_name = message.document.file_name or f"{file_type}_{unique_id}"
    elif message.audio:
        file_id = message.audio.file_id
        unique_id = message.audio.file_unique_id
        file_name = message.audio.file_name or f"audio_{unique_id}.mp3"
        file_type = "audio"
    elif message.voice:
        file_id = message.voice.file_id
        unique_id = message.voice.file_unique_id
        file_name = f"voice_{unique_id}.ogg"
        file_type = "voice"
    elif message.video_note:
        file_id = message.video_note.file_id
        unique_id = message.video_note.file_unique_id
        file_name = f"videonote_{unique_id}.mp4"
        file_type = "video_note"
    elif message.animation:
        file_id = message.animation.file_id
        unique_id = message.animation.file_unique_id
        file_name = message.animation.file_name or f"animation_{unique_id}.mp4"
        file_type = "animation"

    if not file_id or not unique_id:
        await message.reply("❌ Не удалось определить файл. Попробуйте отправить его снова.")
        return

    # Generate a simple hash for deduplication based on unique_id and file_size (if available)
    # Note: Real content hash requires downloading, so we use metadata hash
    file_hash = hashlib.md5(f"{unique_id}".encode()).hexdigest()

    # Check for duplicates
    existing_path = await check_duplicate_file(file_hash)
    if existing_path:
        logger.info(f"Duplicate file detected: {file_name} ({unique_id})")
        # Notify user about duplicate
        try:
            await message.reply(f"⚠️ Файл **{file_name}** уже был загружен ранее.")
        except Exception:
            pass
        return

    if mime_type and file_name and "." not in file_name:
        extension = mimetypes.guess_extension(mime_type)
        if not extension:
            extension_map = {
                "video/mp4": ".mp4",
                "video/quicktime": ".mov",
                "video/x-matroska": ".mkv",
                "video/x-msvideo": ".avi",
                "video/webm": ".webm",
                "image/jpeg": ".jpg",
                "image/png": ".png",
            }
            extension = extension_map.get(mime_type)
        if extension:
            file_name += extension

    date = message.date
    year = date.strftime("%Y")

    months_ru = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }
    month = months_ru[date.month]

    # Получаем настройки чата
    chat_config = await get_chat_config(message.chat.id)

    # Дефолтные настройки: используем глобальный токен и корневую папку.
    # Уведомления по умолчанию отправляем в ЛС пользователя, который прислал файл.
    root_folder = config.ROOT_FOLDER
    yandex_token = config.YANDEX_DISK_TOKEN
    notification_chat_id = message.from_user.id

    # Если запись для этого чата ещё не создана (новая группа/канал) — создаём её
    if not chat_config and message.chat.type != "private":
        await set_chat_config(
            message.chat.id,
            yandex_token=None,  # отдельный токен не задан, используется глобальный
            root_folder=message.chat.title or config.ROOT_FOLDER,
            admin_id=message.from_user.id,
        )
        chat_config = await get_chat_config(message.chat.id)

    if chat_config:
        token, folder, admin_id = chat_config
        if token:
            yandex_token = token
        if folder:
            root_folder = folder
        if admin_id:
            notification_chat_id = admin_id
        else:
            # Если админ для чата не задан, по умолчанию считаем инициатором текущего пользователя
            # и сохраняем его как администратора для уведомлений
            await set_chat_config(message.chat.id, admin_id=message.from_user.id)
            notification_chat_id = message.from_user.id

    logger.info(
        "handle_file: chat_id=%s chat_config_admin_id=%s notification_chat_id=%s",
        message.chat.id,
        chat_config[2] if chat_config and len(chat_config) > 2 else None,
        notification_chat_id,
    )

    # Если ни глобальный, ни чатовый токен не заданы, просим настроить
    if not yandex_token:
        await message.reply(
            "❌ Для этого чата не настроен токен Яндекс.Диска.\n"
            "Пожалуйста, используйте команду /configure для настройки.\n\n"
            f"ℹ️ ID этого чата: `{message.chat.id}`"
        )
        return

    # Проверяем файл на стороне Telegram (до записи в БД и постановки в очередь)
    try:
        file = await message.bot.get_file(file_id)
        file_size = getattr(file, "file_size", None)
        
        # Лимит Telegram Bot API для скачивания файлов - 20 МБ (только если не используем локальный сервер)
        TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
        if not config.TELEGRAM_API_URL and file_size is not None and file_size > TG_DOWNLOAD_LIMIT:
            await message.bot.send_message(
                notification_chat_id,
                f"⚠️ Файл **{file_name}** слишком большой ({file_size / (1024**2):.1f} МБ).\n"
                f"Без локального сервера API боты не могут скачивать файлы более 20 МБ."
            )
            return

        if file_size is not None and file_size > config.MAX_FILE_SIZE:
            max_gb = config.MAX_FILE_SIZE / (1024**3)
            await message.bot.send_message(
                notification_chat_id,
                f"❌ Файл слишком большой для загрузки через бота.\n"
                f"Максимальный размер: {max_gb:.2f} GB."
            )
            return
    except TelegramBadRequest as e:
        if "file is too big" in str(e):
            await message.bot.send_message(
                notification_chat_id,
                f"⚠️ Telegram блокирует скачивание файла **{file_name}**, так как он больше 20 МБ."
            )
            return
        logger.exception(f"TelegramBadRequest while getting file {file_id}: {e}")
        await message.bot.send_message(
            notification_chat_id,
            f"❌ Telegram не позволяет загрузить файл **{file_name}** (Bad Request)."
        )
        return
    except Exception as e:
        logger.exception(f"Unexpected error while getting file {file_id}: {e}")
        await message.reply(
            "❌ Произошла ошибка при получении файла от Telegram. Попробуйте снова."
        )
        return

    # Определение пути на диске
    ext = file_name.split('.')[-1].lower() if '.' in file_name else ""
    main_formats = {'jpg', 'jpeg', 'heic', 'mov', 'mp4', 'png', 'avi', 'mkv', 'webm'}

    # Если файл пришел как документ, но имеет расширение видео/фото, кладем в основную папку
    if ext in main_formats:
        disk_path = f"{root_folder}/{year}/{month}/{file_name}"
    else:
        # Для прочих файлов создаем папку "Прочее" внутри месяца
        disk_path = f"{root_folder}/{year}/{month}/Прочее/{file_name}"

    # Очищаем путь от двойных слешей, которые могут возникнуть при пустом album_folder
    disk_path = disk_path.replace("//", "/")

    # Добавление в БД и очередь (на этом этапе файл уже успешно получен от Telegram)
    if await add_file(
        file_id, unique_id, message.message_id, message.chat.id,
        file_type or message.content_type, file_name, disk_path, file_size, file_hash,
    ):
        try:
            # Отправляем сообщение о добавлении в очередь
            from loader import queue as q_instance
            current_queue = queue or q_instance
            
            queue_size = current_queue.queue.qsize() + 1
            
            # Пытаемся отправить уведомление в ЛС админу чата
            # Если это приватный чат, уведомление идет туда же.
            # Если это группа, уведомление идет админу в ЛС.
            target_chat_id = notification_chat_id
            
            # Пытаемся отправить уведомление ТОЛЬКО в ЛС админу чата
            target_chat_id = notification_chat_id
            
            try:
                # Экранируем имя файла для Markdown или используем HTML
                safe_file_name = file_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                
                status_msg = await message.bot.send_message(
                    target_chat_id,
                    f"⏳ Файл **{safe_file_name}** добавлен в очередь.\n📂 Позиция: {queue_size}",
                    parse_mode="Markdown"
                )
                # Регистрируем сообщение для последующего удаления
                await add_notification(target_chat_id, status_msg.message_id)
            except Exception as e:
                logger.warning(f"Could not send DM to admin {target_chat_id}: {e}. Falling back to source chat.")
                # Если не удалось отправить в ЛС (например, бот не стартован у юзера), шлем в чат источника
                status_msg = await message.reply(
                    f"⏳ Файл **{file_name}** добавлен в очередь (позиция: {queue_size})..."
                )
                await add_notification(message.chat.id, status_msg.message_id)
                target_chat_id = message.chat.id

            # Добавляем задачу в очередь
            await current_queue.add_task(
                file.file_path,
                disk_path,
                unique_id,
                message.bot,
                target_chat_id,
                status_msg.message_id,
                yandex_token,  # Передаем токен чата
                file_size      # Размер файла для прогресса и стриминга
            )
            logger.info(f"Task added to queue: {file_name}")
        except Exception as e:
            logger.exception(f"Error adding task to queue: {e}")
            # Отправляем ошибку в тот же чат, куда и уведомления
            try:
                err_msg = await message.bot.send_message(
                    notification_chat_id,
                    f"❌ Ошибка при добавлении файла **{file_name}** "
                    f"в очередь: {e}",
                )
                await add_notification(notification_chat_id, err_msg.message_id)
            except Exception:
                await message.reply(
                    f"❌ Ошибка при добавлении файла "
                    f"**{file_name}** в очередь: {e}"
                )
    else:
        logger.warning(f"File {file_name} already exists in DB or failed to add")
        # Уведомляем о дубликате
        try:
            dup_msg = await message.bot.send_message(
                notification_chat_id,
                f"⚠️ Файл **{file_name}** уже был загружен "
                f"или находится в очереди."
            )
            await add_notification(notification_chat_id, dup_msg.message_id)
        except Exception as e:
            logger.warning(f"Could not send duplicate notification to {notification_chat_id}: {e}")
            # Если не удалось в ЛС, можно отправить в чат, но лучше не спамить
            # await message.reply(
            #     f"⚠️ Файл **{file_name}** уже был загружен "
            #     f"или находится в очереди."
            # )


@router.message(Command("export_all"))
async def export_all(message: Message):
    if not await can_use_admin_commands(message):
        return

    await message.reply("⚠️ Standard Telegram Bots cannot read chat history due to API limitations.\n"
                        "Only new messages since the bot joined are processed.\n"
                        "To export old history, please forward messages to me or use a Userbot solution.")
