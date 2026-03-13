from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.photo import PhotoResponse


class GalleryPhotoResponse(BaseModel):
    """A photo in a user's personal gallery, with match metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    photo: PhotoResponse
    match_score: float | None
    is_flagged: bool
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
    photos: list[PhotoResponse]


class FlagPhotoRequest(BaseModel):
    reason: str | None = None  # optional, for future moderation use
