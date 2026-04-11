import aiohttp
import config
from loguru import logger
import ssl
import urllib.parse
import io


class YandexDisk:
    BASE_URL = "https://cloud-api.yandex.net/v1/disk/resources"

    def __init__(self, token=None):
        if token is None:
            token = config.YANDEX_DISK_TOKEN

        if not token.startswith("OAuth "):
            token = f"OAuth {token}"
        self.headers = {
            "Authorization": token,
            "Content-Type": "application/json",
        }
        # Disable SSL verification for compatibility
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def create_folder(self, path):
        """Recursively create folders"""
        parts = [p for p in path.split("/") if p]
        current_path = ""
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)

        async with aiohttp.ClientSession(connector=connector) as session:
            for part in parts:
                current_path = f"{current_path}/{part}" if current_path else part
                encoded_path = urllib.parse.quote(current_path)
                url = f"{self.BASE_URL}?path={encoded_path}"

                async with session.put(url, headers=self.headers) as resp:
                    if resp.status == 201:
                        logger.info(f"Created folder: {current_path}")
                    elif resp.status == 409:
                        pass  # Folder exists
                    else:
                        logger.error(
                            f"Failed to create folder {current_path}: {resp.status}",
                        )
                        return False
        return True

    async def upload_file_content(
        self, file_content, disk_path, progress_callback=None,
    ):
        """Upload file content to Disk"""
        encoded_path = urllib.parse.quote(disk_path)
        upload_url_req = (
            f"{self.BASE_URL}/upload?path={encoded_path}&overwrite=true"
        )
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)

        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Get upload URL
            async with session.get(
                upload_url_req, headers=self.headers,
            ) as resp:
                if resp.status == 409:
                    logger.warning(f"File {disk_path} already exists")
                    return True
                if resp.status != 200:
                    logger.error(f"Failed to get upload URL: {resp.status}")
                    return False
                data = await resp.json()
                upload_link = data.get("href")

            # 2. Upload content
            if progress_callback:
                async def file_sender():
                    chunk_size = 64 * 1024  # 64KB chunks
                    total_size = len(file_content)
                    bytes_read = 0

                    with io.BytesIO(file_content) as f:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            bytes_read += len(chunk)
                            await progress_callback(bytes_read, total_size)
                            yield chunk

                async with session.put(
                    upload_link, data=file_sender(),
                ) as upload_resp:
                    if upload_resp.status in (201, 202):
                        return True
                    else:
                        logger.error(f"Upload failed: {upload_resp.status}")
                        return False
            else:
                async with session.put(
                    upload_link, data=file_content,
                ) as upload_resp:
                    if upload_resp.status in (201, 202):
                        return True
                    else:
                        logger.error(f"Upload failed: {upload_resp.status}")
                        return False

    async def upload_file_stream(self, stream_factory, disk_path):
        """
        Upload file to Disk using async byte stream (for large files).
        stream_factory: callable, возвращает async-итератор байтов
        (новый на каждую попытку).
        """
        encoded_path = urllib.parse.quote(disk_path)
        upload_url_req = (
            f"{self.BASE_URL}/upload?path={encoded_path}&overwrite=true"
        )
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)

        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Получаем upload URL
            async with session.get(
                upload_url_req, headers=self.headers,
            ) as resp:
                if resp.status == 409:
                    logger.warning(f"File {disk_path} already exists")
                    return True
                if resp.status != 200:
                    logger.error(
                        f"Failed to get upload URL for stream: {resp.status}",
                    )
                    return False
                data = await resp.json()
                upload_link = data.get("href")

            # 2. Отправляем поток в Яндекс.Диск
            async with session.put(
                upload_link, data=stream_factory(),
            ) as upload_resp:
                if upload_resp.status in (201, 202):
                    return True
                else:
                    logger.error(f"Stream upload failed: {upload_resp.status}")
                    return False

    async def get_disk_info(self):
        """Get disk information"""
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                self.BASE_URL.replace("/resources", ""),
                headers=self.headers,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
