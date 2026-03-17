import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
STREAM_CHUNK_SIZE = 1024 * 256  # 256KB
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


class StorageError(Exception):
    """Raise when storage operations fails"""


class FileTooLarge(Exception):
    """Raise when file is too large"""


class InvalidFileType(Exception):
    """Raise when file type is invalid"""


class BaseStorage(ABC):
    """Abstract base class for photo storage implementations."""

    @abstractmethod
    async def save(
        self, file: UploadFile, event_id: str, subfolder: str = "photos"
    ) -> str:
        """
        Validates and saves a file
        Returns a key the storage can use to retrieve the file later.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a photo by its URL."""

    @abstractmethod
    def load(self, key: str) -> Path:
        """
        Resolves a key and returns a local file path to the photo.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a photo exists by its URL."""


class LocalStorage(BaseStorage):
    """
    Local file system storage implementation.
    Stores file in local filesystem under settings.local_storage_path
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, key: str) -> Path | None:
        """
        Resolves a key to a path by searching the base path for a file matching the key.
        Key are stored as filenames to allow for easy retrieval and deletion.
        """
        matches = list(self.base_path.rglob(f"{key}.*"))
        return matches[0] if matches else None

    def _event_dir(self, event_id: str, subfolder: str) -> Path:
        event_dir = self.base_path / "events" / event_id / subfolder
        event_dir.mkdir(parents=True, exist_ok=True)
        return event_dir

    async def save(
        self, file: UploadFile, event_id: str, subfolder: str = "photos"
    ) -> str:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(
                f"Unsupported file type: {file.content_type}. Allowed types: {ALLOWED_CONTENT_TYPES}"
            )
        extension = Path(file.filename).suffix.lower() if file.filename else ".jpg"
        key = uuid.uuid4().hex
        destination_dir = self._event_dir(event_id, subfolder)
        destination_path = destination_dir / f"{key}{extension}"

        total_bytes = 0
        try:
            async with aiofiles.open(destination_path, "wb") as f:
                while chunk := await file.read(STREAM_CHUNK_SIZE):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_FILE_SIZE_BYTES:
                        raise FileTooLarge(
                            "File size exceeds the maximum allowed limit of 10MB."
                        )
                    await f.write(chunk)
        except (FileTooLarge, InvalidFileType):
            if destination_path.exists():
                destination_path.unlink()
            raise
        logger.info(f"Saved file {file.filename} to {destination_path} with key {key}")
        return key

    async def delete(self, key: str) -> None:
        path = self._get_file_path(key)
        if path and path.exists():
            path.unlink()
            logger.info(f"Deleted file with key {key} at {path}")

    def load(self, key: str) -> Path:
        path = self._get_file_path(key)
        if not path:
            raise StorageError(f"File with key {key} not found.")
        return path

    def exists(self, key: str) -> bool:
        return self._get_file_path(key) is not None


def get_storage() -> BaseStorage:
    if settings.storage_backend == "local":
        return LocalStorage(Path(settings.local_storage_path))
    raise NotImplementedError(
        f"Storage backend {settings.storage_backend} is not implemented."
    )


storage = get_storage()
