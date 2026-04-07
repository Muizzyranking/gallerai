from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.api.dependencies import (
    DB,
    AccessibleEvent,
    CurrentUser,
    OrganizerEvent,
)
from app.core.enums import MediaStatus, StorageBackend
from app.core.schemas import ApiResponse
from app.schemas.media import (
    AnonymousGalleryDownloadRequest,
    MediaBulkUploadResponse,
    MediaCardOrganiser,
    MediaDetail,
    MediaDetailOrganiser,
    MediaListOrganiserResponse,
    MediaListResponse,
    MediaUpdateRequest,
    ProcessingStatusResponse,
)
from app.services import gallery_service, media_service
from app.services.download_service import zip_streaming_response
from app.services.storage_service import StorageError, cloud_storage, local_storage
from app.workers.media_tasks import dispatch_image_tasks, dispatch_video_tasks

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[MediaBulkUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk upload photos/videos for an event",
)
async def bulk_upload_media(
    event: OrganizerEvent,
    current_user: CurrentUser,
    db: DB,
    files: Annotated[list[UploadFile], File()],
) -> ApiResponse[MediaBulkUploadResponse]:
    """
    Save files to local scratch storage immediately, then dispatch background
    tasks for cloud promotion and (for images) face detection.
    """
    media_ids: list[str] = []
    skipped = 0

    for file in files:
        media = await media_service.create_media_record(
            file=file, event=event, uploader=current_user, db=db
        )
        if media is None:
            skipped += 1
            continue
        media_ids.append(media.id)
        if media.is_video:
            dispatch_video_tasks(media.id)
        else:
            dispatch_image_tasks(media.id)

    await media_service.invalidate_event_media_cache(event.id)
    return ApiResponse[MediaBulkUploadResponse](
        message=f"Uploaded: {len(media_ids)} accepted, {skipped} duplicate(s) skipped",
        data=MediaBulkUploadResponse(
            accepted=len(media_ids), media_ids=media_ids, skipped=skipped
        ),
    )


@router.post(
    "/attendee",
    response_model=ApiResponse[MediaBulkUploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload photos as an attendee",
)
async def attendee_upload_media(
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
    files: Annotated[list[UploadFile], File()],
) -> ApiResponse[MediaBulkUploadResponse]:
    """
    Upload photos as an attendee. Requires allow_attendee_uploads=True on the event.
    If require_upload_approval=True, items land in PENDING_APPROVAL and must be
    approved by the organiser before face detection runs.
    """
    if not event.settings.get("allow_attendee_uploads", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This event does not allow attendee uploads",
        )

    media_ids: list[str] = []
    for file in files:
        media = await media_service.create_media_record(
            file=file,
            event=event,
            uploader=current_user,
            db=db,
            is_attendee_upload=True,
        )
        if media is None:
            continue
        media_ids.append(media.id)
        if media.status == MediaStatus.PENDING:
            if media.is_video:
                dispatch_video_tasks(media.id)
            else:
                dispatch_image_tasks(media.id)

    await media_service.invalidate_event_media_cache(event.id)
    return ApiResponse(
        message=f"{len(media_ids)} file(s) uploaded",
        data=MediaBulkUploadResponse(accepted=len(media_ids), media_ids=media_ids),
    )


@router.get(
    "",
    response_model=ApiResponse[MediaListResponse],
    summary="List processed media for an event (gallery grid)",
)
async def list_event_media(
    event: AccessibleEvent,
    db: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApiResponse[MediaListResponse]:
    """
    Returns processed, non-private media only.
    Each item includes thumbnail, display, and download URLs.
    """
    return ApiResponse(
        message="Media retrieved successfully",
        data=await media_service.list_event_media_public(event.id, db, page, page_size),
    )


@router.get(
    "/{media_id}",
    response_model=ApiResponse[MediaDetail],
    summary="Get single media detail (public)",
)
async def get_media_detail(
    media_id: str, event: AccessibleEvent, db: DB
) -> ApiResponse[MediaDetail]:
    """
    Returns full detail for a single processed, non-private media item.
    Includes all three URL variants (thumbnail / display / download).
    """
    return ApiResponse(
        message="Media retrieved successfully",
        data=await media_service.get_media_detail_public(media_id, event.id, db),
    )


@router.get(
    "/serve/{storage_key}",
    summary="Serve or redirect a media file",
    response_class=FileResponse,
)
def serve_media(storage_key: str, event: AccessibleEvent, db: DB):
    """
    Local files are served inline (FileResponse).
    Cloud files trigger a 302 redirect to the CDN display URL.
    """
    from app.models.media import Media as MediaModel

    media = (
        db.query(MediaModel)
        .filter(
            MediaModel.storage_key == storage_key,
            MediaModel.event_id == event.id,
        )
        .first()
    )
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media not found"
        )
    if media.is_private:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This media is private"
        )

    if media.storage_backend != StorageBackend.LOCAL:
        urls = cloud_storage.get_urls(media.storage_key, media.extras)
        return RedirectResponse(url=urls.display, status_code=status.HTTP_302_FOUND)

    try:
        file_path = local_storage.load(media.storage_key, media.extras)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found in storage"
        ) from exc
    return FileResponse(
        path=str(file_path),
        media_type=media.mime_type,
        headers={"Cache-Control": "public, max-age=3600", "X-Media-ID": str(media.id)},
    )


@router.get(
    "/download/{storage_key}",
    summary="Download a local media file as attachment",
    response_class=FileResponse,
)
def download_local_media(storage_key: str) -> FileResponse:
    """
    Serves a local file with Content-Disposition: attachment.
    This is the local-storage counterpart to Cloudinary's fl_attachment flag.
    Access is intentionally ungated — the client only receives this URL after
    passing event-level access checks.
    """
    try:
        file_path = local_storage.load(storage_key)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        ) from exc
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{file_path.name}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/manage",
    response_model=ApiResponse[MediaListOrganiserResponse],
    summary="List all event media (organiser)",
)
async def list_event_media_organiser(
    event: OrganizerEvent,
    db: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApiResponse[MediaListOrganiserResponse]:
    """All media regardless of status or privacy. Includes pipeline status fields."""
    return ApiResponse(
        message="Media retrieved successfully",
        data=await media_service.list_event_media_organiser(
            event.id, db, page, page_size
        ),
    )


@router.get(
    "/manage/status",
    response_model=ApiResponse[ProcessingStatusResponse],
    summary="Get processing and storage status breakdown (organiser)",
)
async def get_processing_status(
    event: OrganizerEvent, db: DB
) -> ApiResponse[ProcessingStatusResponse]:
    """Count breakdown of both MediaStatus and StorageStatus for polling upload progress."""
    return ApiResponse(
        message="Processing status retrieved successfully",
        data=await media_service.get_processing_status(event.id, db),
    )


@router.get(
    "/manage/pending-approval",
    response_model=ApiResponse[MediaListOrganiserResponse],
    summary="List media awaiting approval (organiser)",
)
async def list_pending_approval(
    event: OrganizerEvent,
    db: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApiResponse[MediaListOrganiserResponse]:
    return ApiResponse(
        message="Pending approval retrieved successfully",
        data=await media_service.list_pending_approval(event.id, db, page, page_size),
    )


@router.get(
    "/manage/{media_id}",
    response_model=ApiResponse[MediaDetailOrganiser],
    summary="Get single media detail (organiser)",
)
async def get_media_detail_organiser(
    media_id: str, event: OrganizerEvent, db: DB
) -> ApiResponse[MediaDetailOrganiser]:
    return ApiResponse(
        message="Media retrieved successfully",
        data=await media_service.get_media_detail_organiser(media_id, event.id, db),
    )


@router.post(
    "/manage/{media_id}/approve",
    response_model=ApiResponse[MediaCardOrganiser],
    summary="Approve a pending media upload (organiser)",
)
async def approve_media(
    media_id: str, event: OrganizerEvent, db: DB
) -> ApiResponse[MediaCardOrganiser]:
    """Approve an attendee upload — dispatches face detection and cloud upload tasks."""

    media = await media_service.approve_media(media_id, event, db)
    if media.is_video:
        dispatch_video_tasks(media.id)
    else:
        dispatch_image_tasks(media.id)
    return ApiResponse(
        message="Media approved and queued for processing",
        data=media_service._to_media_card_organiser(media),
    )


@router.post(
    "/manage/{media_id}/reject",
    response_model=ApiResponse[MediaCardOrganiser],
    summary="Reject a pending media upload (organiser)",
)
async def reject_media(
    media_id: str, event: OrganizerEvent, db: DB
) -> ApiResponse[MediaCardOrganiser]:
    media = await media_service.reject_media(media_id, event, db)
    return ApiResponse(
        message="Media rejected",
        data=media_service._to_media_card_organiser(media),
    )


@router.patch(
    "/manage/{media_id}",
    response_model=ApiResponse[MediaCardOrganiser],
    summary="Update media privacy (organiser)",
)
def update_media_privacy(
    media_id: str, payload: MediaUpdateRequest, event: OrganizerEvent, db: DB
) -> ApiResponse[MediaCardOrganiser]:
    if payload.is_private is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update"
        )
    media = media_service.set_media_privacy(media_id, payload.is_private, event, db)
    return ApiResponse(
        message="Media updated successfully",
        data=media_service._to_media_card_organiser(media),
    )


@router.delete(
    "/manage/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a media item (organiser)",
)
async def delete_media(media_id: str, event: OrganizerEvent, db: DB) -> None:
    """Deletes the DB record and the file from whichever backend holds it."""
    await media_service.delete_media(media_id, event, db)


@router.get(
    "/download/event",
    summary="Download all processed event media as zip",
)
async def download_event_media(event: AccessibleEvent, db: DB) -> StreamingResponse:
    """
    Streams a zip of all processed, non-private media for the event.
    Each file is fetched via its backend-appropriate download URL so this
    works correctly across mixed local/cloud backends.
    """
    from app.models.media import Media as MediaModel

    items = (
        db.query(MediaModel)
        .filter(
            MediaModel.event_id == event.id,
            MediaModel.status == MediaStatus.PROCESSED,
            MediaModel.is_private == False,  # noqa: E712
        )
        .order_by(MediaModel.created_at.desc())
        .all()
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No processed media found for this event",
        )
    return zip_streaming_response(items, zip_filename=f"event-{event.id}.zip")


@router.get(
    "/download/my-gallery",
    summary="Download authenticated user's matched gallery as zip",
)
async def download_my_gallery(
    event: AccessibleEvent, current_user: CurrentUser, db: DB
) -> StreamingResponse:
    """
    Downloads the personal gallery built after the user scanned their face.
    Requires authentication.
    """
    items = await gallery_service.get_user_gallery_media(
        user_id=current_user.id, event_id=event.id, db=db
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No gallery found — scan your face first",
        )
    return zip_streaming_response(items, zip_filename=f"my-gallery-{event.id}.zip")


@router.post(
    "/download/anonymous",
    summary="Download anonymous scan results as zip",
)
async def download_anonymous_gallery(
    event: AccessibleEvent,
    payload: AnonymousGalleryDownloadRequest,
    db: DB,
) -> StreamingResponse:
    """
    Downloads matched photos for an anonymous face scan using the short-lived
    scan token returned by POST /scan/anonymous.
    """
    items = await gallery_service.get_anonymous_gallery_media(
        scan_token=payload.scan_token, event_id=event.id, db=db
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan token expired or no matches found",
        )
    return zip_streaming_response(items, zip_filename=f"my-photos-{event.id}.zip")
