from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EventAccessConfig(BaseModel):
    """Defines how attendees gain access to an event."""

    access_mode: str = "link"  # link | code | approved_list | combined
    access_code: str | None = None  # plain text, hashed before storage


class EventCreate(BaseModel):
    title: str
    description: str | None = None
    event_date: datetime | None = None
    access_config: EventAccessConfig = EventAccessConfig()


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    event_date: datetime | None = None
    is_private: bool | None = None
    status: str | None = None  # active | archived


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str | None
    title: str
    description: str | None
    event_date: datetime | None
    cover_photo_url: str | None
    status: str
    is_private: bool
    access_mode: str
    created_at: datetime
    updated_at: datetime


class EventAccessVerify(BaseModel):
    """Payload to verify event access via code."""

    access_code: str


class InviteCreate(BaseModel):
    """Add one or more emails to the approved list."""

    emails: list[str]


class MemberAdd(BaseModel):
    """Add a co-organizer by user id."""

    user_id: str
