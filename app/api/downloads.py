import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import DB, AccessibleEvent, CurrentUser
from app.services.download_service import (
    get_gallery_photos_for_download,
    get_single_photo_for_download,
    stream_zip,
)

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
    file_path, filename, mime_type = get_single_photo_for_download(
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
    photos = get_gallery_photos_for_download(
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
        stream_zip(photos, zip_filename),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
            "Cache-Control": "private, no-store",
        },
    )
