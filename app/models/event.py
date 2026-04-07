from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AccessMode,
    EventRole,
    EventStatus,
    InviteStatus,
    MemberStatus,
)
from app.db import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.face_embedding import FaceEmbedding
    from app.models.media import Media
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_EVENT_SETTINGS = {
    "allow_attendee_uploads": False,
    "require_upload_approval": True,
    "downloads_enabled": True,
    "gallery_visible": True,
}


class Event(BaseModel, TimestampMixin):
    __tablename__ = "events"

    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cover_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, values_callable=lambda x: [e.value for e in x]),
        default=EventStatus.ACTIVE,
        nullable=False,
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    access_mode: Mapped[AccessMode] = mapped_column(
        Enum(AccessMode, values_callable=lambda x: [e.value for e in x]),
        default=AccessMode.LINK,
        nullable=False,
    )
    access_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settings: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: DEFAULT_EVENT_SETTINGS.copy(),
        server_default="{}",
        nullable=False,
        comment="Flexible event configuration — see DEFAULT_EVENT_SETTINGS for available keys",
    )

    # Relationships
    owner: Mapped["User"] = relationship(
        "User", back_populates="owned_events", foreign_keys=[owner_id]
    )
    media: Mapped[list["Media"]] = relationship("Media", back_populates="event")
    members: Mapped[list["EventMember"]] = relationship(
        "EventMember", back_populates="event"
    )
    invites: Mapped[list["EventInvite"]] = relationship(
        "EventInvite", back_populates="event"
    )
    face_embeddings: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding", back_populates="event", cascade="all, delete-orphan"
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
    role: Mapped[EventRole] = mapped_column(
        Enum(EventRole, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[MemberStatus] = mapped_column(
        Enum(MemberStatus, values_callable=lambda x: [e.value for e in x]),
        default=MemberStatus.ACTIVE,
        nullable=False,
    )
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
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, values_callable=lambda x: [e.value for e in x]),
        default=InviteStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="invites")
