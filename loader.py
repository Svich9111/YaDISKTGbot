from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from queue_manager import UploadQueue

# Инициализация диспетчера
dp = Dispatcher(storage=MemoryStorage())

# Инициализация очереди
queue = UploadQueue()
dp["queue"] = queue