from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.photo import Photo
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel, TimestampMixin):
    __tablename__ = "events"

    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cover_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active | archived | deleted
    status: Mapped[str] = mapped_column(String(50), default="active")
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    # link | code | approved_list | combined
    access_mode: Mapped[str] = mapped_column(String(50), default="link")
    access_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    owner: Mapped["User"] = relationship(
        "User", back_populates="owned_events", foreign_keys=[owner_id]
    )
    photos: Mapped[list["Photo"]] = relationship("Photo", back_populates="event")
    members: Mapped[list["EventMember"]] = relationship(
        "EventMember", back_populates="event"
    )
    invites: Mapped[list["EventInvite"]] = relationship(
        "EventInvite", back_populates="event"
    )


class EventMember(BaseModel):
    __tablename__ = "event_members"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # organizer | attendee
    status: Mapped[str] = mapped_column(
        String(50), default="active"
    )  # active | removed
    added_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="members")
    user: Mapped["User"] = relationship(
        "User", back_populates="event_memberships", foreign_keys=[user_id]
    )


class EventInvite(BaseModel):
    __tablename__ = "event_invites"
    __table_args__ = (UniqueConstraint("event_id", "email"),)

    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invite_token: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending"
    )  # pending | accepted | revoked
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="invites")
