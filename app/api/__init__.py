from fastapi import APIRouter

from . import auth, event, photos

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(event.router, prefix="/events", tags=["events"])
router.include_router(
    photos.router,
    prefix="/events/{event_id}/photos",
    tags=["photos"],
)
