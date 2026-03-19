from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.enums import FlagReason
from app.schemas.photo import PhotoSchema


class GalleryPhotoResponse(BaseModel):
    """A photo in a user's personal gallery, with match metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    photo: PhotoSchema
    match_score: float | None
    is_flagged: bool
    flag_reason: str | None
    flagged_at: datetime | None
    created_at: datetime


class GalleryResponse(BaseModel):
    """Paginated gallery response."""

    event_id: str
    total: int
    page: int
    page_size: int
    photos: list[GalleryPhotoResponse]


class AnonymousGalleryResponse(BaseModel):
    """Gallery returned for anonymous scan token — no saved state."""

    event_id: str
    total: int
    photos: list[PhotoSchema]


class FlagPhotoRequest(BaseModel):
    reason: FlagReason | None = None
