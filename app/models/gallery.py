from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import FlagReason
from app.db import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.media import Media
    from app.models.user import User


class UserEventGallery(BaseModel, TimestampMixin):
    __tablename__ = "user_event_galleries"
    __table_args__ = (UniqueConstraint("user_id", "event_id", "media_id"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("media.id", ondelete="CASCADE"), nullable=False
    )
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[FlagReason | None] = mapped_column(
        Enum(FlagReason, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
        comment="Reason the user flagged this photo — null if not flagged",
    )
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="gallery_entries")
    event: Mapped["Event"] = relationship("Event")
    media: Mapped["Media"] = relationship("Media", back_populates="gallery_entries")
