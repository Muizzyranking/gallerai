from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AuthProvider
from app.db.postgres import BaseModel, TimestampMixin

if TYPE_CHECKING:
    from app.models.event import Event, EventMember
    from app.models.gallery import UserEventGallery
    from app.models.tokens import RefreshToken


class User(BaseModel, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # face scan
    face_embedding: Mapped[list[float] | None] = mapped_column(
        ARRAY(Float), nullable=True
    )
    face_scan_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    face_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # admin
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # auth provider
    auth_provider: Mapped[str] = mapped_column(
        String(50), default=AuthProvider.LOCAL, nullable=False
    )
    google_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    # email confirmation
    is_email_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    pending_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_confirmation_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # token version for access tokens, incremented on password change or logout
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def hash_password(self, password: str) -> None:
        from app.core.security import hash_password

        self.password_hash = hash_password(password)

    def verify_password(self, password: str) -> bool:
        from app.core.security import verify_password_hash

        if not self.password_hash:
            dummy_hash = "$2b$12$KIXpGUsMMbLQHjGwHy8ByOFOlbR5yEWV7dR9UGnR2wSDH3Bj8UGQW"
            verify_password_hash(password, dummy_hash)
            return False

        return verify_password_hash(password, self.password_hash)

    def bump_token_version(self) -> None:
        self.token_version += 1
