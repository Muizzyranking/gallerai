from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PhotoStatus
from app.db.postgres import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.gallery import UserEventGallery


class Photo(BaseModel, TimestampMixin):
    __tablename__ = "photos"

    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[PhotoStatus] = mapped_column(
        Enum(PhotoStatus, values_callable=lambda x: [e.value for e in x]),
        default=PhotoStatus.PENDING,
        nullable=False,
        index=True,
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="photos")
    gallery_entries: Mapped[list["UserEventGallery"]] = relationship(
        "UserEventGallery", back_populates="photo"
    )
