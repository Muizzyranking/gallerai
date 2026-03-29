import io
import logging
import zipfile
from collections.abc import AsyncGenerator

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.models.gallery import UserEventGallery
from app.models.photo import Photo
from app.models.user import User
from app.services.storage_service import StorageError, storage

logger = logging.getLogger(__name__)

ZIP_CHUNK_SIZE = 5


def get_single_photo_for_download(
    photo_id: str,
    event_id: str,
    db: Session,
) -> tuple[str, str, str]:
    """
    Resolve a photo for download.
    Returns (file_path, filename, mime_type).
    """
    photo = (
        db.query(Photo).filter(Photo.id == photo_id, Photo.event_id == event_id).first()
    )
    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Photo not found",
        )
    if photo.is_private:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This photo is private",
        )
    try:
        file_path = storage.load(photo.storage_key)
    except StorageError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Photo file not found in storage",
        ) from e
    filename = photo.filename or f"{photo.id}.jpg"
    return str(file_path), filename, photo.mime_type


def get_gallery_photos_for_download(
    user: User,
    event_id: str,
    db: Session,
) -> list[Photo]:
    """
    Fetch all non-flagged, non-private matched photos for a user's gallery.
    Used to build the zip download.
    """
    entries = (
        db.query(UserEventGallery)
        .options(joinedload(UserEventGallery.photo))
        .filter(
            UserEventGallery.user_id == user.id,
            UserEventGallery.event_id == event_id,
            UserEventGallery.is_flagged == False,  # noqa: E712
        )
        .all()
    )
    photos = [
        entry.photo for entry in entries if entry.photo and not entry.photo.is_private
    ]
    if not photos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No photos available for download",
        )
    return photos


def get_photos_from_id(event_id: str, photo_ids: list[str], db: Session):
    entries = (
        db.query(UserEventGallery)
        .options(joinedload(UserEventGallery.photo))
        .filter(
            UserEventGallery.event_id == event_id,
            UserEventGallery.photo_id.in_(photo_ids),
            UserEventGallery.is_flagged == False,  # noqa: E712
        )
        .all()
    )
    photo_map = {
        entry.photo_id: entry.photo
        for entry in entries
        if entry.photo and not entry.photo.is_private
    }

    photos = [photo_map[pid] for pid in photo_ids if pid in photo_map]
    if not photos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid photos found for download",
        )

    missing = len(photo_ids) - len(photos)
    if missing > 0:
        logger.warning(
            f"Skipped {missing} photos (private, flagged, or not in gallery)"
        )

    return photos

def get_photos_from_id(user: User, event_id: str, photo_ids: list[str], db: Session):
    entries = (
        db.query(UserEventGallery)
        .options(joinedload(UserEventGallery.photo))
        .filter(
            UserEventGallery.user_id == user.id,
            UserEventGallery.event_id == event_id,
            UserEventGallery.photo_id.in_(photo_ids),
            UserEventGallery.is_flagged == False,  # noqa: E712
        )
        .all()
    )
    photo_map = {
        entry.photo_id: entry.photo
        for entry in entries
        if entry.photo and not entry.photo.is_private
    }

    photos = [photo_map[pid] for pid in photo_ids if pid in photo_map]
    if not photos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid photos found for download",
        )

    missing = len(photo_ids) - len(photos)
    if missing > 0:
        logger.warning(
            f"Skipped {missing} photos (private, flagged, or not in gallery)"
        )

    return photos


async def stream_zip(
    photos: list[Photo],
    zip_filename: str = "galleria-photos.zip",
) -> AsyncGenerator[bytes, None]:
    """
    Stream a zip file containing all given photos.
    Writes photos in chunks to keep memory usage flat.
    Yields bytes chunks suitable for FastAPI StreamingResponse.

    Each photo is stored in the zip under its original filename,
    with a numeric prefix to avoid name collisions:
        001_photo.jpg
        002_photo.jpg
    """
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, photo in enumerate(photos, start=1):
            try:
                file_path = storage.load(photo.storage_key)
            except StorageError:
                logger.warning(f"Skipping photo {photo.id} — file not found in storage")
                continue

            original_name = photo.filename or f"{photo.id}.jpg"
            zip_entry_name = f"{i:03d}_{original_name}"

            zf.write(str(file_path), zip_entry_name)
            logger.debug(f"Added {zip_entry_name} to zip")

            if i % ZIP_CHUNK_SIZE == 0:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

    remaining = buffer.getvalue()
    if remaining:
        yield remaining

    logger.info(f"Zip stream complete — {len(photos)} photos — {zip_filename}")
