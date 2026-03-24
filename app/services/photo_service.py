from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.orm import Session

from app.core.enums import PhotoStatus
from app.models import Event, Photo, User
from app.schemas.photo import PhotoResponse, PhotoSchema, ProcessingStatusResponse
from app.services.storage_service import FileTooLarge, InvalidFileType, storage


def _get_image_dimensions(key: str) -> tuple[int | None, int | None]:
    """
    Read image dimension from storage using Pillow
    Returns (width, height)
    """
    try:
        path = storage.load(key)
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


async def create_photo_record(
    file: UploadFile,
    event: Event,
    uploader: User,
    db: Session,
    is_attendee_upload: bool = False,
) -> Photo:
    """
    Create a Photo record in the database after saving the file to storage.
    """
    try:
        key = await storage.save(file, event.id, subfolder="photos")
    except InvalidFileType as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except FileTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    width, height = _get_image_dimensions(key)

    if is_attendee_upload and event.settings.get("require_upload_approval", True):
        initial_status = PhotoStatus.PENDING_APPROVAL
    else:
        initial_status = PhotoStatus.PENDING

    photo = Photo(
        event_id=event.id,
        uploaded_by=uploader.id,
        storage_key=key,
        filename=file.filename,
        file_size=file.size,
        width=width,
        height=height,
        status=initial_status,
        mime_type=file.content_type,
    )

    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo


def get_photo_or_404(photo_id: str, event_id: str, db: Session) -> Photo:
    """
    Retrieve a Photo by ID or raise 404 if not found.
    """
    photo = (
        db.query(Photo).filter(Photo.id == photo_id, Photo.event_id == event_id).first()
    )
    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found"
        )
    return photo


def get_photo_url(photo: Photo) -> str:
    """Build the serve URL for a photo."""
    return f"/events/{photo.event_id}/photos/serve/{photo.id}"


def get_processing_status(event_id: str, db: Session) -> ProcessingStatusResponse:
    """
    Get the count of photos in each processing status for a given event.
    """
    photos = db.query(Photo).filter(Photo.event_id == event_id).all()
    counts = {s: 0 for s in PhotoStatus}

    pending = counts[PhotoStatus.PENDING]
    processing = counts[PhotoStatus.PROCESSING]
    processed = counts[PhotoStatus.PROCESSED]
    failed = counts[PhotoStatus.FAILED]

    return ProcessingStatusResponse(
        event_id=event_id,
        total=len(photos),
        pending=pending,
        processing=processing,
        processed=processed,
        failed=failed,
    )


def get_event_photos(
    event_id: str, db: Session, page: int = 1, page_size: int = 50
) -> PhotoResponse:
    """
    Get all photos for a given event.
    """

    query = (
        db.query(Photo)
        .filter(
            Photo.event_id == event_id,
            Photo.is_private == False,  # noqa: E712
        )
        .order_by(Photo.created_at.desc())
    )
    total = query.count()
    photos = query.offset((page - 1) * page_size).limit(page_size).all()
    return PhotoResponse(
        total=str(total),
        photos=[
            PhotoSchema(
                id=p.id,
                event_id=p.event_id,
                filename=p.filename,
                file_size=p.file_size,
                mime_type=p.mime_type,
                width=p.width,
                height=p.height,
                face_count=p.face_count,
                status=p.status,
                is_private=p.is_private,
                processed_at=p.processed_at,
                created_at=p.created_at,
            )
            for p in photos
        ],
    )


def get_pending_approval_photos(
    event_id: str,
    db: Session,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Photo], int]:
    """Return photos awaiting organizer approval. Organizer only."""
    query = (
        db.query(Photo)
        .filter(
            Photo.event_id == event_id,
            Photo.status == PhotoStatus.PENDING_APPROVAL,
        )
        .order_by(Photo.created_at.asc())
    )
    total = query.count()
    photos = query.offset((page - 1) * page_size).limit(page_size).all()
    return photos, total


def get_pending_approval_photo(photo_id: str, event: Event, db: Session) -> Photo:
    photo = get_photo_or_404(photo_id, event.id, db)
    if photo.status != PhotoStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Photo is not pending approval (current status: {photo.status})",
        )
    return photo


def approve_photo(photo_id: str, event: Event, db: Session) -> Photo:
    """
    Approve a pending photo — sets status to pending so Celery picks it up.
    """
    photo = get_pending_approval_photo(photo_id, event, db)
    photo.status = PhotoStatus.PENDING
    db.commit()
    db.refresh(photo)
    return photo


def reject_photo(photo_id: str, event: Event, db: Session) -> Photo:
    """
    Reject a pending photo — marks as rejected (soft delete).
    """
    photo = get_pending_approval_photo(photo_id, event, db)
    photo.status = PhotoStatus.REJECTED
    db.commit()
    db.refresh(photo)
    return photo


def set_photo_privacy(
    photo_id: str, is_private: bool, event: Event, db: Session
) -> Photo:
    """
    Set the privacy status of a photo.
    """
    photo = get_photo_or_404(photo_id, event.id, db)
    photo.is_private = is_private
    db.commit()
    db.refresh(photo)
    return photo


async def delete_photo(photo_id: str, event: Event, db: Session) -> None:
    photo = get_photo_or_404(photo_id, event.id, db)
    await storage.delete(photo.storage_key)
    db.delete(photo)
    db.commit()


def mark_photo_as_processing(photo: Photo, db: Session) -> None:
    photo.status = PhotoStatus.PROCESSING
    db.commit()


def mark_photo_as_processed(photo: Photo, face_count: int, db: Session) -> None:
    photo.status = PhotoStatus.PROCESSED
    photo.face_count = face_count
    photo.processed_at = datetime.now(timezone.utc)
    db.commit()


def mark_photo_as_failed(photo: Photo, error_message: str, db: Session) -> None:
    photo.status = PhotoStatus.FAILED
    photo.error_message = error_message
    db.commit()
