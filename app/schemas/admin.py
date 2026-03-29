from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminUserResponse(BaseModel):
    """Full user representation for admin — includes sensitive fields."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    is_admin: bool
    is_active: bool
    face_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserUpdate(BaseModel):
    """Admin-only user update — can toggle admin and active flags."""

    is_admin: bool | None = None
    is_active: bool | None = None
    display_name: str | None = None


class PlatformSettingResponse(BaseModel):
    """A single platform setting."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    description: str | None
    updated_by: str | None
    updated_at: datetime


class PlatformSettingUpdate(BaseModel):
    """Request body to update a platform setting."""

    value: object
    description: str | None = None


class PlatformStatsResponse(BaseModel):
    """High-level platform statistics."""

    total_users: int
    total_events: int
    total_photos: int
    total_processed_photos: int
    total_face_embeddings: int
