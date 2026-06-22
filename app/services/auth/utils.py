import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    TokenPurpose,
    create_access_token_for_user,
    create_refresh_token,
    create_token,
    decode_token,
)
from app.models.user import User
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

email_service = EmailService.from_settings()

RESEND_COOLDOWN_SECONDS = 60
EMAIL_CONFIRM_TTL = timedelta(hours=24)
EMAIL_CHANGE_TTL = timedelta(hours=1)
PASSWORD_RESET_TTL = timedelta(hours=1)


def frontend_url() -> str:
    return settings.frontend_url.rstrip("/")


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def issue_token_pair(db: Session, user: User, request: Request) -> tuple[str, str]:
    """Return (access_token, raw_refresh_token)."""
    access_token = create_access_token_for_user(user)
    raw_refresh = create_refresh_token(
        db,
        str(user.id),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return access_token, raw_refresh


def generate_email_confirm_token(email: str) -> str:
    return create_token(
        {"sub": email},
        expires=EMAIL_CONFIRM_TTL,
        purpose=TokenPurpose.EMAIL_CONFIRM,
    )


def generate_pending_email_token(new_email: str) -> str:
    return create_token(
        {"sub": new_email},
        expires=EMAIL_CHANGE_TTL,
        purpose=TokenPurpose.EMAIL_CHANGE,
    )


def generate_password_reset_token(email: str, token_version: int = 0) -> str:
    return create_token(
        {"sub": email, "version": token_version},
        expires=PASSWORD_RESET_TTL,
        purpose=TokenPurpose.PASSWORD_RESET,
    )


def decode_typed_payload(token: str, purpose: TokenPurpose) -> dict[str, Any]:
    """
    Decode a JWT, assert its purpose, and return its payload.
    Raises 400 on any failure so callers don't leak token details.
    """
    try:
        payload = decode_token(token, expected_purpose=purpose)
    except HTTPException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This link is invalid or has expired.",
        ) from e
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This link is invalid or has expired.",
        )
    return payload


def decode_typed_token(token: str, purpose: TokenPurpose) -> str:
    """
    Decode a JWT, assert its purpose, and return the `sub` claim.
    """
    return str(decode_typed_payload(token, purpose)["sub"])
