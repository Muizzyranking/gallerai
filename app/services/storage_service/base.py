import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .schemas import (
    BatchDeleteByTagResult,
    BatchDeleteResult,
    BatchSaveResult,
    MediaURLs,
    SaveResult,
    StorageBackendType,
    StorageExtras,
)

logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    """
    Contract every storage backend must fulfil.
    """

    backend_type: StorageBackendType

    @abstractmethod
    async def save(
        self, file: UploadFile, event_id: str, subfolder: str = "photos"
    ) -> SaveResult:
        """Validate, persist, and return a ``SaveResult`` with key + extras."""
        pass

    @abstractmethod
    async def delete(self, key: str, extras: dict[str, Any] | None = None) -> None:
        """Delete a file. ``extras`` lets cloud backends avoid an extra lookup."""
        pass

    @abstractmethod
    def load(self, key: str, extras: dict[str, Any] | None = None) -> Path:
        """
        Return a local ``Path`` to the file.
        Only meaningful for ``LocalStorage``; cloud backends should raise
        ``StorageError`` — callers should check ``backend_type`` first.
        """
        pass

    @abstractmethod
    def exists(self, key: str, extras: dict[str, Any] | None = None) -> bool:
        """Return True if the file is reachable."""
        pass

    @abstractmethod
    def get_urls(self, key: str, extras: dict[str, Any]) -> MediaURLs:
        """
        Build all URL variants for a stored file.
        """
        pass

    @abstractmethod
    def parse_extras(self, raw: dict[str, Any]) -> StorageExtras:
        """Deserialise JSONB extras into the backend's typed model."""
        pass

    async def bulk_save(
        self,
        files: list[UploadFile],
        event_id: str,
        subfolder: str = "photos",
        concurrency: int = 5,
    ) -> BatchSaveResult:
        """
        Save multiple files concurrently, bounded by ``concurrency``.
        Failures are collected; successful saves are not rolled back.
        """
        semaphore = asyncio.Semaphore(concurrency)
        succeeded: list[SaveResult] = []
        failed: list[tuple[str, Exception]] = []

        async def _save_one(f: UploadFile):
            async with semaphore:
                try:
                    result = await self.save(f, event_id, subfolder)
                    succeeded.append(result)
                except Exception as exc:
                    logger.warning("bulk_save: failed to save %s: %s", f.filename, exc)
                    failed.append((f.filename or "unknown", exc))

        await asyncio.gather(*[_save_one(f) for f in files])
        return BatchSaveResult(succeeded=succeeded, failed=failed)

    async def bulk_delete(
        self,
        keys: list[str],
        extras_map: dict[str, dict[str, Any]] | None = None,
        concurrency: int = 10,
    ) -> BatchDeleteResult:
        """
        Delete multiple files concurrently, collecting failures.
        """
        semaphore = asyncio.Semaphore(concurrency)
        deleted: list[str] = []
        failed: list[tuple[str, Exception]] = []
        extras_map = extras_map or {}

        async def _delete_one(key: str):
            async with semaphore:
                try:
                    await self.delete(key, extras_map.get(key))
                    deleted.append(key)
                except Exception as exc:
                    logger.warning("bulk_delete: failed to delete %s: %s", key, exc)
                    failed.append((key, exc))

        await asyncio.gather(*[_delete_one(k) for k in keys])
        return BatchDeleteResult(deleted=deleted, failed=failed)

    async def delete_event(self, event_id: str) -> BatchDeleteByTagResult:
        """
        Delete ALL assets belonging to an event in one (or a few paginated) API calls.
        """
        logger.warning(
            "delete_event: backend '%s' has no tag-based delete — "
            "use bulk_delete with explicit keys from the DB instead.",
            self.backend_type,
        )
        return BatchDeleteByTagResult(
            tag=f"event:{event_id}", deleted_count=0, partial=False, failed_cursors=[]
        )

    async def delete_by_prefix(self, prefix: str) -> BatchDeleteByTagResult:
        """
        Delete all assets whose public_id starts with ``prefix``.
        """
        return BatchDeleteByTagResult(
            tag=prefix, deleted_count=0, partial=False, failed_cursors=[]
        )
