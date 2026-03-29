from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import BaseModel


class PlatformSettings(BaseModel):
    """
    Admin-controlled platform-wide settings.
    Stored as key/value pairs so new settings can be added without migrations.
    """

    __tablename__ = "platform_settings"
    __table_args__ = (UniqueConstraint("key"),)

    key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
