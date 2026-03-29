from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import BaseModel, generate_uuid

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.photo import Photo


class FaceEmbedding(BaseModel):
    __tablename__ = "face_embeddings"
    __table_args__ = (
        UniqueConstraint("photo_id", "face_index", name="uq_face_per_photo"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=generate_uuid
    )
    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    photo_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("photos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    bounding_box: Mapped[dict] = mapped_column(JSON, nullable=False)
    detection_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    face_index: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    photo: Mapped["Photo"] = relationship("Photo", back_populates="faces")
    event: Mapped["Event"] = relationship("Event", back_populates="face_embeddings")
