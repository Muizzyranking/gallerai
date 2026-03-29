import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import DB, AccessibleEvent, CurrentUser
from app.schemas.photo import PhotosDownload
from app.services import download_service

router = APIRouter()


logger = logging.getLogger(__name__)


@router.get(
    "/photos/{photo_id}/download",
    summary="Download a single photo",
    response_class=FileResponse,
)
def download_single_photo(
    photo_id: str,
    event: AccessibleEvent,
    db: DB,
):
    """
    Download a single photo as a raw image file.
    Opens directly in the device's photo gallery app.
    """
    file_path, filename, mime_type = download_service.get_single_photo_for_download(
        photo_id=photo_id,
        event_id=event.id,
        db=db,
    )
    return FileResponse(
        path=file_path,
        media_type=mime_type,
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("photos/download")
def download_selected_photos(event: AccessibleEvent, db: DB, ids: PhotosDownload):
    photos = download_service.get_photos_from_id(
        event_id=event.id,
        photo_ids=ids,
        db=db,
    )

    zip_filename = f"galleria-{event.id[:8]}-{len(photos)}-photos.zip"
    logger.info(
        f"Starting selected zip download —"
        f"event={event.id} requested={len(ids)} found={len(photos)}"
    )

    return StreamingResponse(
        download_service.stream_zip(photos, zip_filename),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/gallery/me/download",
    summary="Download all matched photos as a zip",
)
async def download_my_gallery(
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
) -> StreamingResponse:
    """
    Stream a zip file containing all non-flagged, non-private photos
    from the current user's matched gallery for this event.
    Memory usage stays flat regardless of gallery size — photos are
    streamed into the zip in chunks.
    """
    photos = download_service.get_gallery_photos_for_download(
        user=current_user,
        event_id=event.id,
        db=db,
    )

    zip_filename = f"galleria-{event.id[:8]}.zip"
    logger.info(
        f"Starting zip download — user={current_user.id} "
        f"event={event.id} photos={len(photos)}"
    )

    return StreamingResponse(
        download_service.stream_zip(photos, zip_filename),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
            "Cache-Control": "private, no-store",
        },
    )
