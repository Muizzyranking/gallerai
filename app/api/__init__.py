from fastapi import APIRouter

from . import auth, downloads, event, faces, gallery, photos

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(event.router, prefix="/events", tags=["events"])
router.include_router(
    photos.router,
    prefix="/events/{event_id}/photos",
    tags=["photos"],
)
router.include_router(
    faces.router,
    prefix="/events/{event_id}/faces",
    tags=["faces"],
)
router.include_router(
    gallery.router,
    prefix="/events/{event_id}/gallery",
    tags=["gallery"],
)
router.include_router(
    downloads.router,
    prefix="/events/{event_id}",
    tags=["downloads"],
)
