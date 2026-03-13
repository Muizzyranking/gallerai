from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    DateTime,
    Float,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import BaseModel

if TYPE_CHECKING:
    from app.models.event import Event, EventMember
    from app.models.gallery import UserEventGallery


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    face_embedding: Mapped[list[float] | None] = mapped_column(
        ARRAY(Float), nullable=True
    )
    face_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    owned_events: Mapped[list["Event"]] = relationship(
        "Event", back_populates="owner", foreign_keys="Event.owner_id"
    )
    event_memberships: Mapped[list["EventMember"]] = relationship(
        "EventMember", back_populates="user", foreign_keys="EventMember.user_id"
    )
    gallery_entries: Mapped[list["UserEventGallery"]] = relationship(
        "UserEventGallery", back_populates="user"
    )
