from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings

from .base import BaseStorage
from .cloudinary import CloudinaryStorage
from .constants import ALLOWED_CONTENT_TYPES, ALLOWED_IMAGE_TYPES, ALLOWED_VIDEO_TYPES
from .exceptions import FileTooLarge, InvalidFileType, StorageError
from .factory import get_storage
from .local import LocalStorage
from .schemas import (
    BatchDeleteResult,
    BatchSaveResult,
    MediaURLs,
    SaveResult,
    StorageBackendType,
)

local_storage: LocalStorage = LocalStorage(Path(settings.local_storage_path))
cloud_storage: BaseStorage = get_storage()

if TYPE_CHECKING:
    from app.models.media import Media


def get_media_urls(media: "Media") -> MediaURLs:
    """
    Resolve all URL variants for a Media record.
    Picks the correct backend based on ``media.storage_backend`` — not the
    currently configured backend — so URLs remain valid during a migration.
    """
    from app.core.enums import StorageBackend

    backend = (
        local_storage
        if media.storage_backend == StorageBackend.LOCAL
        else cloud_storage
    )
    return backend.get_urls(media.storage_key, media.extras or {})


def get_download_url(media: "Media") -> str:
    """
    Return just the download URL for a Media record.
    Always resolves against the backend the file actually lives on.
    """
    return get_media_urls(media).download


__all__ = [
    "BaseStorage",
    "LocalStorage",
    "CloudinaryStorage",
    "get_storage",
    "local_storage",
    "cloud_storage",
    "SaveResult",
    "BatchSaveResult",
    "BatchDeleteResult",
    "MediaURLs",
    "StorageBackendType",
    "FileTooLarge",
    "InvalidFileType",
    "StorageError",
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_IMAGE_TYPES",
    "ALLOWED_VIDEO_TYPES",
]
