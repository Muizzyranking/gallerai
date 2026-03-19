import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.dependencies import DB, AccessibleEvent, CurrentUser, OptionalCurrentUser
from app.core.schemas import ApiResponse
from app.db.mongo import get_mongo_db
from app.schemas.face import (
    AnonymousScanResponse,
    ClaimGalleryRequest,
    FaceScanResponse,
)
from app.services import gallery_service, search_service
from app.services.face_service import extract_single_embedding
from app.services.storage_service import (
    FileTooLarge,
    InvalidFileType,
    storage,
)

router = APIRouter()

logger = logging.getLogger(__name__)


async def _extract_embedding_from_upload(
    file: UploadFile,
    event_id: str,
) -> tuple[list[float], str]:
    """
    Save uploaded face image temporarily, extract embedding, return both.
    Caller is responsible for deleting the storage key after use.
    Raises 400 if no face detected or file is invalid.
    """
    try:
        key = await storage.save(file, event_id, subfolder="faces")
    except InvalidFileType as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except FileTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    try:
        image_path = storage.load(key)
        # Run synchronous DeepFace in thread pool — never block the event loop
        embedding = await asyncio.to_thread(extract_single_embedding, image_path)
    except Exception as e:
        await storage.delete(key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Face extraction failed: {str(e)}",
        ) from e

    if embedding is None:
        await storage.delete(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No face detected in the uploaded image. Please use a clear, well-lit photo.",
        )

    return embedding, key


@router.post(
    "/scan",
    response_model=ApiResponse[FaceScanResponse],
    summary="Scan face and build personal gallery (registered users)",
)
async def scan_face(
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
    file: Annotated[UploadFile, File(description="Clear photo of your face")],
) -> ApiResponse[FaceScanResponse]:
    """
    Extract face embedding from uploaded image, save to user profile,
    run similarity search against event photos, and build the user's gallery.
    The temporary scan image is deleted after processing regardless of outcome.
    """
    mongo_db = get_mongo_db()
    embedding, key = await _extract_embedding_from_upload(file, event.id)

    try:
        current_user.face_embedding = embedding
        from datetime import datetime, timezone

        current_user.face_updated_at = datetime.now(timezone.utc)
        db.commit()

        # Run similarity search
        matches = await search_service.search_event_for_user(
            embedding=embedding,
            event_id=event.id,
            db=mongo_db,
        )

        gallery_service.upsert_gallery_entries(current_user, event.id, matches, db)

    finally:
        await storage.delete(key)

    return ApiResponse(
        message="Face scan complete",
        data=FaceScanResponse(
            face_detected=True,
            match_count=len(matches),
        ),
    )


@router.post(
    "/scan/anonymous",
    response_model=ApiResponse[AnonymousScanResponse],
    summary="Scan face anonymously — results stored temporarily in Redis",
)
async def scan_face_anonymous(
    event: AccessibleEvent,
    file: Annotated[UploadFile, File(description="Clear photo of your face")],
    optional_current_user: OptionalCurrentUser,
) -> ApiResponse[AnonymousScanResponse]:
    """
    Extract face embedding and run similarity search without saving anything permanently.
    Returns a short-lived scan token the client uses to retrieve matched photos.
    Embedding is never stored. Temporary image is deleted after extraction.
    """
    if optional_current_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already authenticated — use regular scan endpoint",
        )
    mongo_db = get_mongo_db()
    embedding, key = await _extract_embedding_from_upload(file, event.id)

    try:
        matches = await search_service.search_event_for_user(
            embedding=embedding,
            event_id=event.id,
            db=mongo_db,
        )
        token = await gallery_service.store_anonymous_results(event.id, matches)
    finally:
        await storage.delete(key)

    return ApiResponse(
        message="Face scan complete",
        data=AnonymousScanResponse(
            scan_token=token,
            expires_in_seconds=600,
            match_count=len(matches),
        ),
    )


@router.post(
    "/claim",
    response_model=ApiResponse[FaceScanResponse],
    summary="Claim anonymous gallery into registered account",
)
async def claim_gallery(
    payload: ClaimGalleryRequest,
    event: AccessibleEvent,
    current_user: CurrentUser,
    db: DB,
) -> ApiResponse[FaceScanResponse]:
    """
    Merge an anonymous scan token's results into the authenticated user's gallery.
    Consumes the token — it cannot be used again.
    If the user already has a gallery for this event, results are merged
    keeping the higher match score per photo and preserving existing flags.
    """
    new_count = await gallery_service.claim_anonymous_gallery(
        token=payload.scan_token,
        event_id=event.id,
        user=current_user,
        db=db,
    )
    return ApiResponse(
        message=f"Gallery claimed — {new_count} new photos added",
        data=FaceScanResponse(
            face_detected=True,
            match_count=new_count,
        ),
    )
