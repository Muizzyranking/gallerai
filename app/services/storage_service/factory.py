from pathlib import Path

from app.core.config import settings

from .base import BaseStorage
from .cloudinary import CloudinaryStorage
from .local import LocalStorage
from .schemas import StorageBackendType


def get_storage(backend: StorageBackendType | None = None, **kwargs) -> BaseStorage:
    target = backend or settings.storage_backend
    if target == "local":
        return LocalStorage(Path(settings.local_storage_path))
    if target == "cloudinary":
        return CloudinaryStorage(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            folder_prefix=settings.cloudinary_folder_prefix,
        )
    raise NotImplementedError(f"Storage backend '{target}' is not implemented.")
