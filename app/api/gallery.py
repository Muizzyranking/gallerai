from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import DB, AccessibleEvent, CurrentUser
from app.core.schemas import ApiResponse
from app.models.photo import Photo
from app.schemas.gallery import (
    AnonymousGalleryResponse,
    FlagPhotoRequest,
    GalleryResponse,
)
from app.schemas.photo import PhotoSchema
from app.services import gallery_service
from app.services.photo_service import get_event_photos

router = APIRouter()


@router.get(
    "",
    response_model=ApiResponse[list[PhotoSchema]],
    summary="Full event gallery — all non-private photos",
)
def get_full_gallery(
    event: AccessibleEvent,
    db: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """
    Return all non-private photos for an event.
    Requires valid event access. Paginated.
    """
    photos, total = get_event_photos(event.id, db, page, page_size)
    return ApiResponse(
        message=f"{total} photos found",
        data=[PhotoSchema.model_validate(p) for p in photos],
    )


@router.get(
    "/me",
    response_model=ApiResponse[GalleryResponse],
    summary="Personal face-matched gallery for registered user",
)
def get_my_gallery(
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    include_flagged: Annotated[
        bool, Query(description="Include flagged photos")
    ] = False,
):
    """
    Return photos where the current user's face was matched.
    Excludes flagged entries by default.
    Requires the user to have previously scanned their face for this event.
    """
    gallery = gallery_service.get_user_gallery(
        user=current_user,
        event_id=event.id,
        db=db,
        page=page,
        page_size=page_size,
        include_flagged=include_flagged,
    )
    return ApiResponse(
        message=f"{gallery.total} matched photos found",
        data=gallery,
    )


@router.get(
    "/anonymous",
    response_model=ApiResponse[AnonymousGalleryResponse],
    summary="Anonymous face-matched gallery via scan token",
)
async def get_anonymous_gallery(
    event: AccessibleEvent,
    db: DB,
    token: Annotated[str, Query(description="Scan token from anonymous face scan")],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """
    Return matched photos for an anonymous scan token.
    Token is validated and scoped to this event.
    Expires after settings.anonymous_scan_ttl_seconds (default 2 hours).
    """
    matches = await gallery_service.get_anonymous_results(token, event.id)

    # Paginate photo_ids
    photo_ids = list(matches.keys())
    total = len(photo_ids)
    paginated_ids = photo_ids[(page - 1) * page_size : page * page_size]

    # Fetch photo records
    photos = (
        db.query(Photo)
        .filter(
            Photo.id.in_(paginated_ids),
            Photo.is_private == False,  # noqa: E712
        )
        .all()
    )

    return ApiResponse(
        message=f"{total} matched photos found",
        data=AnonymousGalleryResponse(
            event_id=event.id,
            total=total,
            photos=[PhotoSchema.model_validate(p) for p in photos],
        ),
    )


@router.post(
    "/{photo_id}/flag",
    response_model=ApiResponse[dict],
    summary="Flag a photo in personal gallery",
)
def flag_photo(
    photo_id: str,
    payload: FlagPhotoRequest,
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
):
    """
    Soft-flag a photo in the user's gallery.
    Flagged photos are hidden from normal gallery view but never deleted.
    Reasons: not_me (false match), dislike (personal preference), removed (no reason).
    """
    gallery_service.flag_gallery_entry(
        user=current_user,
        event_id=event.id,
        photo_id=photo_id,
        db=db,
        reason=payload.reason,
    )
    return ApiResponse(
        message="Photo flagged successfully", data={"photo_id": photo_id}
    )


@router.delete(
    "/{photo_id}/flag",
    response_model=ApiResponse[dict],
    summary="Restore a flagged photo to gallery",
)
def unflag_photo(
    photo_id: str,
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
):
    """Restore a previously flagged photo back to the user's visible gallery."""
    gallery_service.unflag_gallery_entry(
        user=current_user,
        event_id=event.id,
        photo_id=photo_id,
        db=db,
    )
    return ApiResponse(message="Photo restored to gallery", data={"photo_id": photo_id})
