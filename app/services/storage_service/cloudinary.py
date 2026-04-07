import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

import cloudinary
import cloudinary.api
import cloudinary.uploader
from fastapi import UploadFile

from .base import BaseStorage
from .constants import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_VIDEO_TYPES,
    CLOUDINARY_PRESETS,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
)
from .exceptions import FileTooLarge, InvalidFileType, StorageError
from .schemas import (
    BatchDeleteByTagResult,
    BatchDeleteResult,
    CloudinaryExtras,
    MediaURLs,
    SaveResult,
    StorageBackendType,
)

logger = logging.getLogger(__name__)


class CloudinaryStorage(BaseStorage):
    """
    Cloudinary backend.

    Transformation URLs are pre-generated at upload time and cached in
    ``extras.eager_urls`` so ``get_urls`` never makes a network call.
    """

    backend_type: StorageBackendType = "cloudinary"
    _BULK_DELETE_CHUNK = 100

    def __init__(
        self,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        folder_prefix: str = "gallerai",
    ) -> None:
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )
        self.folder_prefix = folder_prefix

    def _public_id(self, event_id: str, subfolder: str, key: str) -> str:
        return f"{self.folder_prefix}/{event_id}/{subfolder}/{key}"

    @staticmethod
    def _resource_type(content_type: str | None) -> Literal["image", "video", "raw"]:
        if content_type in ALLOWED_VIDEO_TYPES:
            return "video"
        return "image"

    @staticmethod
    def _build_transformation_url(
        public_id: str,
        preset_name: str,
        resource_type: str,
    ) -> str:
        transformations = CLOUDINARY_PRESETS.get(preset_name, [])
        return cloudinary.CloudinaryImage(public_id).build_url(
            transformation=transformations,
            resource_type=resource_type,
        )

    async def save(
        self,
        file: UploadFile,
        event_id: str,
        subfolder: str = "media",
    ) -> SaveResult:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(f"Unsupported file type: {file.content_type}.")
        resource_type = self._resource_type(file.content_type)
        key = uuid.uuid4().hex
        public_id = self._public_id(event_id, subfolder, key)

        content = await file.read()
        if len(content) > (
            MAX_VIDEO_BYTES if resource_type == "video" else MAX_IMAGE_BYTES
        ):
            raise FileTooLarge("File exceeds size limit.")

        # Tags enable one-call event wipes via delete_resources_by_tag.
        tags = [
            f"event:{event_id}",
            f"subfolder:{subfolder}",
            resource_type,
        ]

        # Eager transformations for images
        eager = (
            [{"transformation": t} for t in CLOUDINARY_PRESETS.values()]
            if resource_type == "image"
            else []
        )

        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            content,
            public_id=public_id,
            resource_type=resource_type,
            overwrite=False,
            tags=tags,
            eager=eager,
            eager_async=False,
        )

        eager_urls: dict[str, str] = {}
        if resource_type == "image":
            for preset in CLOUDINARY_PRESETS:
                eager_urls[preset] = self._build_transformation_url(
                    public_id, preset, resource_type
                )

        extras = CloudinaryExtras(
            public_id=public_id,
            resource_type=resource_type,
            format=result.get("format", "jpg"),
            version=result.get("version", 0),
            tags=tags,
            eager_urls=eager_urls,
        )

        logger.info(
            "CloudinaryStorage.save: %s → %s (tags: %s)", file.filename, public_id, tags
        )
        return SaveResult(
            key=key,
            backend="cloudinary",
            extras=extras.model_dump(),
            media_type="video" if resource_type == "video" else "image",
        )

    async def delete(self, key: str, extras: dict[str, Any] | None = None) -> None:
        if not extras:
            raise StorageError(
                "CloudinaryStorage.delete requires extras to resolve public_id."
            )
        parsed = CloudinaryExtras(**extras)
        await asyncio.to_thread(
            cloudinary.uploader.destroy,
            parsed.public_id,
            resource_type=parsed.resource_type,
            invalidate=True,
        )
        logger.info("CloudinaryStorage.delete: %s", parsed.public_id)

    def load(self, key: str, extras: dict[str, Any] | None = None) -> Path:
        raise StorageError(
            "CloudinaryStorage does not support local file loading. "
            "Use get_urls() to obtain a CDN URL instead."
        )

    def exists(self, key: str, extras: dict[str, Any] | None = None) -> bool:
        if not extras:
            return False
        try:
            parsed = CloudinaryExtras(**extras)
            cloudinary.api.resource(
                parsed.public_id, resource_type=parsed.resource_type
            )
            return True
        except Exception:
            return False

    def get_urls(self, key: str, extras: dict[str, Any]) -> MediaURLs:
        parsed = self.parse_extras(extras)
        eager = parsed.eager_urls

        def _url(preset: str) -> str:
            if preset in eager:
                return eager[preset]
            return self._build_transformation_url(
                parsed.public_id, preset, parsed.resource_type
            )

        return MediaURLs(
            original=_url("download"),
            display=_url("display"),
            thumbnail=_url("thumbnail"),
        )

    def parse_extras(self, raw: dict[str, Any]) -> CloudinaryExtras:
        return CloudinaryExtras(**raw)

    async def bulk_delete(
        self,
        keys: list[str],
        extras_map: dict[str, dict[str, Any]] | None = None,
    ) -> BatchDeleteResult:
        """
        Delete specific assets by public_id using Cloudinary's Admin API
        """
        extras_map = extras_map or {}
        deleted: list[str] = []
        failed: list[tuple[str, Exception]] = []

        # Split into resolvable (have extras) and unresolvable
        batchable: list[tuple[str, CloudinaryExtras]] = []
        for key in keys:
            raw = extras_map.get(key)
            if raw:
                batchable.append((key, CloudinaryExtras(**raw)))
            else:
                failed.append(
                    (
                        key,
                        StorageError(
                            f"Missing extras for key '{key}' — cannot resolve public_id"
                        ),
                    )
                )

        def _chunks(lst: list, n: int):
            for i in range(0, len(lst), n):
                yield lst[i : i + n]

        for chunk in _chunks(batchable, self._BULK_DELETE_CHUNK):
            by_type: dict[str, list[tuple[str, CloudinaryExtras]]] = {}
            for key, parsed in chunk:
                by_type.setdefault(parsed.resource_type, []).append((key, parsed))

            for resource_type, items in by_type.items():
                public_ids = [p.public_id for _, p in items]
                chunk_keys = [k for k, _ in items]
                try:
                    await asyncio.to_thread(
                        cloudinary.api.delete_resources,
                        public_ids,
                        resource_type=resource_type,
                        invalidate=True,
                    )
                    deleted.extend(chunk_keys)
                    logger.info(
                        "CloudinaryStorage.bulk_delete: removed %d %s resources",
                        len(public_ids),
                        resource_type,
                    )
                except Exception as exc:
                    logger.warning(
                        "CloudinaryStorage.bulk_delete: chunk failed: %s", exc
                    )
                    for k in chunk_keys:
                        failed.append((k, exc))

        return BatchDeleteResult(deleted=deleted, failed=failed)

    async def delete_event(self, event_id: str) -> BatchDeleteByTagResult:
        """
        Wipe ALL Cloudinary assets for an event in one or more paginated API calls.
        """
        tag = f"event:{event_id}"
        deleted_count = 0
        failed_cursors: list[str] = []
        cursor: str | None = None
        partial = False

        while True:
            kwargs: dict[str, Any] = {"invalidate": True}
            if cursor:
                kwargs["next_cursor"] = cursor

            try:
                response = await asyncio.to_thread(
                    cloudinary.api.delete_resources_by_tag,
                    tag,
                    **kwargs,
                )
                batch_deleted = response.get("deleted", {})
                deleted_count += len(batch_deleted)
                partial = response.get("partial", False)
                cursor = response.get("next_cursor")

                logger.info(
                    "CloudinaryStorage.delete_event: tag=%s page deleted=%d partial=%s",
                    tag,
                    len(batch_deleted),
                    partial,
                )

                if not partial or not cursor:
                    break

            except Exception as exc:
                logger.error(
                    "CloudinaryStorage.delete_event: error at cursor=%s: %s",
                    cursor,
                    exc,
                )
                failed_cursors.append(cursor or "initial")
                break  # stop paginating on error; caller can retry

        return BatchDeleteByTagResult(
            tag=tag,
            deleted_count=deleted_count,
            partial=partial and bool(cursor),
            failed_cursors=failed_cursors,
        )

    async def delete_by_prefix(self, prefix: str) -> BatchDeleteByTagResult:
        """
        Delete all Cloudinary assets whose public_id starts with ``prefix``,
        paginating with ``next_cursor`` until all resources are removed.
        """
        deleted_count = 0
        failed_cursors: list[str] = []
        cursor: str | None = None
        partial = False

        while True:
            kwargs: dict[str, Any] = {"invalidate": True}
            if cursor:
                kwargs["next_cursor"] = cursor

            try:
                response = await asyncio.to_thread(
                    cloudinary.api.delete_resources_by_prefix,
                    prefix,
                    **kwargs,
                )
                batch_deleted = response.get("deleted", {})
                deleted_count += len(batch_deleted)
                partial = response.get("partial", False)
                cursor = response.get("next_cursor")

                logger.info(
                    "CloudinaryStorage.delete_by_prefix: prefix=%s page deleted=%d partial=%s",
                    prefix,
                    len(batch_deleted),
                    partial,
                )

                if not partial or not cursor:
                    break

            except Exception as exc:
                logger.error(
                    "CloudinaryStorage.delete_by_prefix: error at cursor=%s: %s",
                    cursor,
                    exc,
                )
                failed_cursors.append(cursor or "initial")
                break

        return BatchDeleteByTagResult(
            tag=prefix,
            deleted_count=deleted_count,
            partial=partial and bool(cursor),
            failed_cursors=failed_cursors,
        )
