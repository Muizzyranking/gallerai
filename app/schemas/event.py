from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.core.enums import AccessMode, EventStatus


class EventAccessConfig(BaseModel):
    access_mode: AccessMode = AccessMode.LINK
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
    status: EventStatus | None = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str | None
    title: str
    description: str | None
    event_date: datetime | None
    cover_photo_url: str | None
    status: EventStatus
    is_private: bool
    access_mode: AccessMode
    created_at: datetime
    updated_at: datetime


class EventAccessVerify(BaseModel):
    access_code: str | None = None


class InviteCreate(BaseModel):
    emails: list[str]


class MemberAdd(BaseModel):
    user_id: str | None = None
    email: EmailStr | None = None
