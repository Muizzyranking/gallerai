from fastapi import APIRouter

from .auth import router as auth_router
from .event import router as event_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(event_router, prefix="/events", tags=["events"])
