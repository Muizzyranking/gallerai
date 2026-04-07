import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.cache import Cache
from app.core.enums import MediaStatus, MediaType, StorageBackend, StorageStatus
from app.core.utils import compute_file_hash
from app.models.event import Event
from app.models.media import Media
from app.models.user import User
from app.schemas.media import (
    MediaCard,
    MediaCardOrganiser,
    MediaDetail,
    MediaDetailOrganiser,
    MediaListOrganiserResponse,
    MediaListResponse,
    MediaURLs,
    ProcessingStatusResponse,
)
from app.services.storage_service import (
    ALLOWED_VIDEO_TYPES,
    FileTooLarge,
    InvalidFileType,
    cloud_storage,
    get_media_urls,
    local_storage,
)

logger = logging.getLogger(__name__)

media_cache = Cache(namespace="media", ttl=600)
media_list_cache = Cache(namespace="media_lists", ttl=300)

_MAX_KEY_ATTEMPTS = 5


def _generate_unique_key(db: Session) -> str:
    for attempt in range(1, _MAX_KEY_ATTEMPTS + 1):
        candidate = uuid.uuid4().hex
        exists = db.query(Media.id).filter(Media.storage_key == candidate).first()
        if not exists:
            return candidate
        logger.warning(
            "_generate_unique_key: collision on attempt %d (key=%s)",
            attempt,
            candidate,
        )
    raise RuntimeError(
        f"Could not generate a unique storage_key after {_MAX_KEY_ATTEMPTS} attempts."
    )


def _get_image_dimensions(file_path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _resolve_urls(media: Media) -> MediaURLs:
    urls = get_media_urls(media)
    return MediaURLs(
        thumbnail=urls.thumbnail, display=urls.display, download=urls.download
    )


def _to_media_card(media: Media) -> MediaCard:
    return MediaCard(
        id=media.id,
        event_id=media.event_id,
        media_type=media.media_type,
        filename=media.filename,
        face_count=media.face_count,
        urls=_resolve_urls(media),
        created_at=media.created_at,
    )


def _to_media_card_organiser(media: Media) -> MediaCardOrganiser:
    return MediaCardOrganiser(
        id=media.id,
        event_id=media.event_id,
        media_type=media.media_type,
        filename=media.filename,
        face_count=media.face_count,
        urls=_resolve_urls(media),
        created_at=media.created_at,
        status=media.status,
        storage_status=media.storage_status,
        is_private=media.is_private,
    )


def _to_media_detail(media: Media) -> MediaDetail:
    return MediaDetail(
        id=media.id,
        event_id=media.event_id,
        media_type=media.media_type,
        filename=media.filename,
        face_count=media.face_count,
        urls=_resolve_urls(media),
        created_at=media.created_at,
        width=media.width,
        height=media.height,
        file_size=media.file_size,
        mime_type=media.mime_type,
        processed_at=media.processed_at,
        uploaded_at=media.uploaded_at,
    )


def _to_media_detail_organiser(media: Media) -> MediaDetailOrganiser:
    return MediaDetailOrganiser(
        id=media.id,
        event_id=media.event_id,
        media_type=media.media_type,
        filename=media.filename,
        face_count=media.face_count,
        urls=_resolve_urls(media),
        created_at=media.created_at,
        width=media.width,
        height=media.height,
        file_size=media.file_size,
        mime_type=media.mime_type,
        processed_at=media.processed_at,
        uploaded_at=media.uploaded_at,
        status=media.status,
        storage_status=media.storage_status,
        storage_backend=media.storage_backend,
        is_private=media.is_private,
        error_message=media.error_message,
    )


async def create_media_record(
    file: UploadFile,
    event: Event,
    uploader: User,
    db: Session,
    is_attendee_upload: bool = False,
) -> Media | None:
    """
    Save file to local scratch storage and create a Media DB record.
    Returns None for duplicates (same file_hash in the same event).
    """
    file_hash = await compute_file_hash(file)

    existing = (
        db.query(Media)
        .filter(Media.event_id == event.id, Media.file_hash == file_hash)
        .first()
    )
    if existing:
        logger.info(
            "create_media_record: duplicate hash=%s event=%s — skipping",
            file_hash,
            event.id,
        )
        return None

    storage_key = _generate_unique_key(db)

    try:
        result = await local_storage.save(
            file,
            event_id=event.id,
            subfolder="photos",
            key=storage_key,
        )
    except (InvalidFileType, FileTooLarge) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    media_type = (
        MediaType.VIDEO if file.content_type in ALLOWED_VIDEO_TYPES else MediaType.IMAGE
    )

    width, height = None, None
    if media_type == MediaType.IMAGE:
        local_path = local_storage.load(storage_key, result.extras)
        width, height = _get_image_dimensions(local_path)

    initial_status = (
        MediaStatus.PENDING_APPROVAL
        if is_attendee_upload and event.settings.get("require_upload_approval", True)
        else MediaStatus.PENDING
    )

    media = Media(
        event_id=event.id,
        uploaded_by=uploader.id,
        file_hash=file_hash,
        filename=file.filename,
        file_size=file.size,
        mime_type=file.content_type,
        media_type=media_type,
        width=width,
        height=height,
        storage_key=storage_key,
        storage_backend=StorageBackend.LOCAL,
        storage_status=StorageStatus.LOCAL,
        extras=result.extras,
        status=initial_status,
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    logger.info(
        "create_media_record: %s key=%s type=%s", media.id, storage_key, media_type
    )
    return media


def get_media_or_404(media_id: str, event_id: str, db: Session) -> Media:
    media = (
        db.query(Media).filter(Media.id == media_id, Media.event_id == event_id).first()
    )
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media not found"
        )
    return media


async def list_event_media_organiser(
    event_id: str, db: Session, page: int = 1, page_size: int = 50
) -> MediaListOrganiserResponse:
    """All statuses, all items including private. Cached per page."""
    cache_key = f"org:list:{event_id}:p:{page}:ps:{page_size}"

    async def fetch():
        query = (
            db.query(Media)
            .filter(Media.event_id == event_id)
            .order_by(Media.created_at.desc())
        )
        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return MediaListOrganiserResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[_to_media_card_organiser(m) for m in items],
        ).model_dump()

    return await media_list_cache.get_or_set(cache_key, fetch)


async def get_media_detail_organiser(
    media_id: str, event_id: str, db: Session
) -> MediaDetailOrganiser:
    async def fetch():
        return _to_media_detail_organiser(
            get_media_or_404(media_id, event_id, db)
        ).model_dump()

    return await media_cache.get_or_set(f"org:detail:{media_id}", fetch)


async def list_event_media_public(
    event_id: str, db: Session, page: int = 1, page_size: int = 50
) -> MediaListResponse:
    """PROCESSED + non-private only."""
    cache_key = f"pub:list:{event_id}:p:{page}:ps:{page_size}"

    async def fetch():
        query = (
            db.query(Media)
            .filter(
                Media.event_id == event_id,
                Media.status == MediaStatus.PROCESSED,
                Media.is_private == False,  # noqa: E712
            )
            .order_by(Media.created_at.desc())
        )
        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return MediaListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=[_to_media_card(m) for m in items],
        ).model_dump()

    return await media_list_cache.get_or_set(cache_key, fetch)


async def get_media_detail_public(
    media_id: str, event_id: str, db: Session
) -> MediaDetail:
    async def fetch():
        media = get_media_or_404(media_id, event_id, db)
        if media.status != MediaStatus.PROCESSED or media.is_private:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Media not found"
            )
        return _to_media_detail(media).model_dump()

    return await media_cache.get_or_set(f"pub:detail:{media_id}", fetch)


async def get_processing_status(event_id: str, db: Session) -> ProcessingStatusResponse:
    """Both MediaStatus and StorageStatus breakdown. Organiser-only."""

    async def fetch():
        status_rows = (
            db.query(Media.status, func.count(Media.id))
            .filter(Media.event_id == event_id)
            .group_by(Media.status)
            .all()
        )
        storage_rows = (
            db.query(Media.storage_status, func.count(Media.id))
            .filter(Media.event_id == event_id)
            .group_by(Media.storage_status)
            .all()
        )
        status_counts = {s: 0 for s in MediaStatus}
        total = 0
        for s, count in status_rows:
            status_counts[MediaStatus(s)] = count
            total += count

        storage_counts = {s: 0 for s in StorageStatus}
        for s, count in storage_rows:
            storage_counts[StorageStatus(s)] = count

        return ProcessingStatusResponse(
            event_id=event_id,
            total=total,
            pending=status_counts[MediaStatus.PENDING],
            pending_approval=status_counts[MediaStatus.PENDING_APPROVAL],
            processing=status_counts[MediaStatus.PROCESSING],
            processed=status_counts[MediaStatus.PROCESSED],
            failed=status_counts[MediaStatus.FAILED],
            storage_local=storage_counts[StorageStatus.LOCAL],
            storage_uploading=storage_counts[StorageStatus.UPLOADING],
            storage_uploaded=storage_counts[StorageStatus.UPLOADED],
            storage_upload_failed=storage_counts[StorageStatus.UPLOAD_FAILED],
        ).model_dump()

    return await media_list_cache.get_or_set(f"status:{event_id}", fetch)


async def list_pending_approval(
    event_id: str, db: Session, page: int = 1, page_size: int = 50
) -> MediaListOrganiserResponse:
    query = (
        db.query(Media)
        .filter(
            Media.event_id == event_id, Media.status == MediaStatus.PENDING_APPROVAL
        )
        .order_by(Media.created_at.asc())
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return MediaListOrganiserResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_to_media_card_organiser(m) for m in items],
    )


def _get_pending_or_404(media_id: str, event_id: str, db: Session) -> Media:
    media = get_media_or_404(media_id, event_id, db)
    if media.status != MediaStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Media is not pending approval (status: {media.status})",
        )
    return media


async def approve_media(media_id: str, event: Event, db: Session) -> Media:
    media = _get_pending_or_404(media_id, event.id, db)
    media.status = MediaStatus.PENDING
    db.commit()
    db.refresh(media)
    await invalidate_event_media_cache(event.id)
    return media


async def reject_media(media_id: str, event: Event, db: Session) -> Media:
    media = _get_pending_or_404(media_id, event.id, db)
    media.status = MediaStatus.REJECTED
    db.commit()
    db.refresh(media)
    await invalidate_event_media_cache(event.id)
    return media


def set_media_privacy(
    media_id: str, is_private: bool, event: Event, db: Session
) -> Media:
    media = get_media_or_404(media_id, event.id, db)
    media.is_private = is_private
    db.commit()
    db.refresh(media)
    return media


async def delete_media(media_id: str, event: Event, db: Session) -> None:
    """
    Delete the media record and its file from whichever backend holds it.
    Routes via media.storage_backend — not the currently configured backend —
    so deletion is correct even during a backend migration.
    """
    media = get_media_or_404(media_id, event.id, db)
    backend = (
        local_storage
        if media.storage_backend == StorageBackend.LOCAL
        else cloud_storage
    )
    try:
        await backend.delete(media.storage_key, media.extras)
    except Exception as exc:
        # Log but don't block — orphaned files are handled by reconciliation job
        logger.error(
            "delete_media: storage delete failed for %s (%s): %s",
            media_id,
            media.storage_backend,
            exc,
        )
    db.delete(media)
    db.commit()
    await invalidate_event_media_cache(event.id)
    logger.info("delete_media: %s deleted from %s", media_id, media.storage_backend)


async def invalidate_event_media_cache(event_id: str) -> None:
    await asyncio.gather(
        media_list_cache.invalidate(f"status:{event_id}"),
        media_list_cache.invalidate_pattern(f"pub:list:{event_id}:*"),
        media_list_cache.invalidate_pattern(f"org:list:{event_id}:*"),
        media_list_cache.invalidate_pattern(f"pending:{event_id}:*"),
    )


def mark_media_as_processing(media: Media, db: Session) -> None:
    media.status = MediaStatus.PROCESSING
    db.commit()


def mark_media_as_processed(media: Media, face_count: int, db: Session) -> None:
    media.status = MediaStatus.PROCESSED
    media.face_count = face_count
    media.processed_at = datetime.now(timezone.utc)
    media.error_message = None
    db.commit()


def mark_media_as_failed(media: Media, reason: str, db: Session) -> None:
    media.status = MediaStatus.FAILED
    media.error_message = reason
    db.commit()


def mark_media_upload_started(media: Media, db: Session) -> None:
    media.storage_status = StorageStatus.UPLOADING
    db.commit()


def mark_media_upload_complete(
    media: Media, backend: StorageBackend, extras: dict, db: Session
) -> None:
    media.storage_backend = backend
    media.storage_status = StorageStatus.UPLOADED
    media.extras = extras
    media.uploaded_at = datetime.now(timezone.utc)
    db.commit()


def mark_media_upload_failed(media: Media, reason: str, db: Session) -> None:
    media.storage_status = StorageStatus.UPLOAD_FAILED
    media.error_message = reason
    db.commit()
