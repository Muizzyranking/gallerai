from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import (
    DB,
    AccessibleEvent,
    CurrentUser,
    OrganizerEvent,
)
from app.core.enums import PhotoStatus
from app.core.schemas import ApiResponse
from app.schemas.photo import (
    PhotoBulkUploadResponse,
    PhotoResponse,
    PhotoSchema,
    PhotoUpdateRequest,
    ProcessingStatusResponse,
)
from app.services import photo_service
from app.services.storage_service import StorageError, storage
from app.workers.photo_tasks import process_photo_task

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[PhotoBulkUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk upload photos for an event",
)
async def bulk_upload_photos(
    event: OrganizerEvent,
    current_user: CurrentUser,
    db: DB,
    files: Annotated[list[UploadFile], File()],
):
    """
    Upload one or more photos to an event.
    Each photo is saved and a DB record is created immediately, but the actual processing (face detection, embedding generation) is done asynchronously in the background.
    """
    photo_ids = []
    for file in files:
        photo = await photo_service.create_photo_record(
            file=file, event=event, uploader=current_user, db=db
        )
        photo_ids.append(photo.id)
        process_photo_task.delay(photo_id=photo.id)  # type: ignore

    return ApiResponse[PhotoBulkUploadResponse](
        message="Photos uploaded successfully",
        data=PhotoBulkUploadResponse(accepted=len(photo_ids), photo_ids=photo_ids),
    )


@router.post(
    "/attendee",
    response_model=ApiResponse[PhotoBulkUploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload photos as an attendee",
)
async def attendee_upload_photos(
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
    files: Annotated[list[UploadFile], File(description="One or more image files")],
) -> ApiResponse[PhotoBulkUploadResponse]:
    """
    Upload photos as an attendee.
    Requires event setting allow_attendee_uploads=True.
    If require_upload_approval=True, photos go to pending_approval state
    and must be approved by organizer before processing.
    """
    if not event.settings.get("allow_attendee_uploads", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This event does not allow attendee uploads",
        )

    photo_ids = []
    for file in files:
        photo = await photo_service.create_photo_record(
            file, event, current_user, db, is_attendee_upload=True
        )
        photo_ids.append(photo.id)
        # Only dispatch processing if no approval required
        if photo.status == PhotoStatus.PENDING:
            process_photo_task.delay(photo.id)  # type: ignore

    return ApiResponse(
        message=f"{len(photo_ids)} photo(s) uploaded",
        data=PhotoBulkUploadResponse(accepted=len(photo_ids), photo_ids=photo_ids),
    )


@router.get(
    "",
    response_model=ApiResponse[PhotoResponse],
    summary="List all photos in an event",
)
def list_photos(
    event: AccessibleEvent,
    db: DB,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 50,
):
    """
    Return a paginated list of non-private photos for an event.
    Requires valid event access (link, code, or approved list).
    """
    return ApiResponse[PhotoResponse](
        message="Photos retrieved successfully",
        data=photo_service.get_event_photos(event.id, db, page, page_size),
    )


@router.get(
    "/status",
    response_model=ApiResponse[ProcessingStatusResponse],
    summary="Get photo processing status for an event",
)
def get_processing_status(event: OrganizerEvent, db: DB):
    """
    Return a count breakdown of photo processing statuses.
    Organizer only — used to poll bulk upload progress.
    """
    # return photo_service.get_processing_status(event.id, db)
    return ApiResponse[ProcessingStatusResponse](
        message="Processing status retrieved successfully",
        data=photo_service.get_processing_status(event.id, db),
    )


@router.get(
    "/serve/{photo_id}", summary="Serve a photo file", response_class=FileResponse
)
def serve_photo(photo_id: str, event: AccessibleEvent, db: DB) -> FileResponse:
    """
    Serve the actual photo file for a given photo ID.
    Requires valid event access (link, code, or approved list).
    """
    photo = photo_service.get_photo_or_404(photo_id, event.id, db)
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

    return FileResponse(
        path=str(file_path),
        media_type=photo.mime_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Photo-ID": photo_id,
        },
    )


@router.patch(
    "/{photo_id}",
    response_model=ApiResponse[PhotoSchema],
    summary="Update a photo's visibility",
)
def update_photo_privacy(
    photo_id: str, payload: PhotoUpdateRequest, event: OrganizerEvent, db: DB
):
    """Set a photo as private or public. Organizer only."""
    if payload.is_private is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update"
        )
    photo = photo_service.set_photo_privacy(photo_id, payload.is_private, event, db)
    return ApiResponse[PhotoSchema](
        message="Photo updated successfully",
        data=PhotoSchema.model_validate(photo),
    )


@router.delete(
    "/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a photo",
)
async def delete_photo(photo_id: str, event: OrganizerEvent, db: DB) -> None:
    """Permanently delete a photo and its file from storage. Organizer only."""
    await photo_service.delete_photo(photo_id, event, db)
