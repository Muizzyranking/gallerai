import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

import aiofiles
import aiofiles.os
from fastapi import UploadFile

from .base import BaseStorage
from .constants import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_VIDEO_TYPES,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    STREAM_CHUNK_SIZE,
)
from .exceptions import FileTooLarge, InvalidFileType, StorageError
from .schemas import (
    BatchDeleteByTagResult,
    LocalExtras,
    MediaURLs,
    SaveResult,
    StorageBackendType,
)

logger = logging.getLogger(__name__)


class LocalStorage(BaseStorage):
    """
    Filesystem storage.

    Serve URL is constructed as ``/api/v1/photos/serve/<key>``
    """

    backend_type: StorageBackendType = "local"
    _SERVE_BASE = "/api/v1/photos/serve"

    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _event_dir(self, event_id: str, subfolder: str) -> Path:
        d = self.base_path / "events" / event_id / subfolder
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _resolve(self, key: str) -> Path | None:
        matches = list(self.base_path.rglob(f"{key}.*"))
        return matches[0] if matches else None

    @staticmethod
    def _max_bytes(content_type: str | None) -> int:
        if content_type in ALLOWED_VIDEO_TYPES:
            return MAX_VIDEO_BYTES
        return MAX_IMAGE_BYTES

    @staticmethod
    def _media_type(content_type: str | None) -> Literal["image", "video"]:
        return "video" if content_type in ALLOWED_VIDEO_TYPES else "image"

    async def save(
        self,
        file: UploadFile,
        event_id: str,
        subfolder: str = "media",
        *,
        key: str | None = None,
    ) -> SaveResult:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(
                f"Unsupported file type: {file.content_type}. "
                f"Allowed: {ALLOWED_CONTENT_TYPES}"
            )
        max_bytes = self._max_bytes(file.content_type)
        extension = Path(file.filename).suffix.lower() if file.filename else ".jpg"
        key = key or uuid.uuid4().hex
        dest_dir = self._event_dir(event_id, subfolder)
        dest_path = dest_dir / f"{key}{extension}"
        relative_path = str(dest_path.relative_to(self.base_path))

        total = 0
        try:
            async with aiofiles.open(dest_path, "wb") as fh:
                while chunk := await file.read(STREAM_CHUNK_SIZE):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileTooLarge(
                            f"File exceeds {max_bytes // (1024 * 1024)} MB limit."
                        )
                    await fh.write(chunk)
        except (FileTooLarge, InvalidFileType):
            if dest_path.exists():
                dest_path.unlink()
            raise

        extras = LocalExtras(relative_path=relative_path)
        logger.info("LocalStorage.save: %s → %s", file.filename, dest_path)
        return SaveResult(
            key=key,
            backend="local",
            extras=extras.model_dump(),
            media_type=self._media_type(file.content_type),
        )

    async def delete(self, key: str, extras: dict[str, Any] | None = None) -> None:
        if extras:
            path = self.base_path / LocalExtras(**extras).relative_path
        else:
            path = self._resolve(key)
        if path and path.exists():
            await aiofiles.os.remove(path)
            logger.info("LocalStorage.delete: removed %s", path)

    def load(self, key: str, extras: dict[str, Any] | None = None) -> Path:
        if extras:
            path = self.base_path / LocalExtras(**extras).relative_path
            if path.exists():
                return path
        path = self._resolve(key)
        if not path:
            raise StorageError(f"File with key {key} not found in local storage.")
        return path

    def exists(self, key: str, extras: dict[str, Any] | None = None) -> bool:
        if extras:
            path = self.base_path / LocalExtras(**extras).relative_path
            return path.exists()
        return self._resolve(key) is not None

    def get_urls(self, key: str, extras: dict[str, Any]) -> MediaURLs:
        """Local serve endpoint — all variants point to the same file."""
        serve_url = f"{self._SERVE_BASE}/{key}"
        download_url = f"/api/v1/photos/download/{key}"
        return MediaURLs(thumbnail=serve_url, display=serve_url, download=download_url)

    def parse_extras(self, raw: dict[str, Any]) -> LocalExtras:
        return LocalExtras(**raw)

    async def delete_event(self, event_id: str) -> BatchDeleteByTagResult:
        """
        Delete all local files for an event by removing the entire event folder.
        """
        import shutil

        event_dir = self.base_path / "events" / event_id
        if not event_dir.exists():
            logger.info("LocalStorage.delete_event: folder not found — %s", event_dir)
            return BatchDeleteByTagResult(
                tag=f"event:{event_id}",
                deleted_count=0,
                partial=False,
                failed_cursors=[],
            )

        # Count files before removal
        file_count = sum(1 for _ in event_dir.rglob("*") if _.is_file())

        try:
            await asyncio.to_thread(shutil.rmtree, event_dir)
            logger.info(
                "LocalStorage.delete_event: removed %s (%d files)",
                event_dir,
                file_count,
            )
            return BatchDeleteByTagResult(
                tag=f"event:{event_id}",
                deleted_count=file_count,
                partial=False,
                failed_cursors=[],
            )
        except Exception as exc:
            logger.error(
                "LocalStorage.delete_event: failed to remove %s: %s", event_dir, exc
            )
            return BatchDeleteByTagResult(
                tag=f"event:{event_id}",
                deleted_count=0,
                partial=True,
                failed_cursors=[str(event_dir)],
            )
