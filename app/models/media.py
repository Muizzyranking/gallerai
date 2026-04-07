from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import MediaStatus, MediaType, StorageBackend, StorageStatus
from app.db.postgres import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.face_embedding import FaceEmbedding
    from app.models.gallery import UserEventGallery


class Media(BaseModel, TimestampMixin):
    __tablename__ = "media"
    __table_args__ = (
        UniqueConstraint("event_id", "file_hash", name="uq_event_media_filehash"),
    )

    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )

    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="image/jpeg"
    )
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    storage_backend: Mapped[StorageBackend] = mapped_column(
        Enum(StorageBackend, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=StorageBackend.LOCAL,
        comment="Which backend currently holds the file.",
    )
    storage_status: Mapped[StorageStatus] = mapped_column(
        Enum(StorageStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=StorageStatus.LOCAL,
        index=True,
    )
    extras: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Backend-specific metadata. Parse with the backend's typed Extras model. ",
    )

    # Processing state
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=MediaStatus.PENDING,
        index=True,
        comment="Face-detection pipeline status.",
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when cloud promotion completed successfully.",
    )

    event: Mapped["Event"] = relationship("Event", back_populates="media")
    faces: Mapped[list["FaceEmbedding"]] = relationship(
        "FaceEmbedding", back_populates="media", cascade="all, delete-orphan"
    )
    gallery_entries: Mapped[list["UserEventGallery"]] = relationship(
        "UserEventGallery", back_populates="media"
    )

    @property
    def is_on_cloud(self) -> bool:
        return self.storage_status == StorageStatus.UPLOADED

    @property
    def is_local_safe_to_delete(self) -> bool:
        """
        True when the local scratch file can be removed.
        Both the upload AND processing pipeline must be in a terminal state.
        For images: processing must be PROCESSED or FAILED (no pending retries).
        For videos: processing is not applicable — UPLOADED alone is enough.
        """
        upload_done = self.storage_status == StorageStatus.UPLOADED
        if self.media_type == MediaType.VIDEO:
            return upload_done
        processing_terminal = self.status in {MediaStatus.PROCESSED, MediaStatus.FAILED}
        return upload_done and processing_terminal

    @property
    def is_video(self) -> bool:
        return self.media_type == MediaType.VIDEO

    @property
    def is_image(self) -> bool:
        return self.media_type == MediaType.IMAGE
