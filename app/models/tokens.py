import hashlib
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


RAW_TOKEN_BYTES = 64  # 512-bit token → URL-safe hex string of 128 chars


class RefreshToken(BaseModel, TimestampMixin):
    """
    Persisted refresh token.

    Only the SHA-256 hash is stored; the raw token is issued once and never
    saved, so a DB breach cannot be used to hijack sessions.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    @staticmethod
    def generate_raw() -> str:
        """Return a cryptographically-secure random token (hex string)."""
        return secrets.token_hex(RAW_TOKEN_BYTES)

    @staticmethod
    def hash(raw_token: str) -> str:
        """SHA-256 hash of the raw token – what we persist in the DB."""
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @property
    def is_valid(self) -> bool:

        return not self.revoked and self.expires_at > datetime.now(tz=timezone.utc)


class PasswordResetToken(BaseModel):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User")
