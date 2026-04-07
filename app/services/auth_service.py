import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token_for_user,
    create_password_reset_token,
    create_refresh_token,
    hash_token,
    hash_value,
    verify_hash,
)
from app.models.tokens import PasswordResetToken, RefreshToken
from app.models.user import User
from app.schemas.auth import (
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
)
from app.services import email_service

logger = logging.getLogger(__name__)


def register_user(payload: UserCreate, db: Session) -> TokenResponse:
    """Register new user with access and refresh tokens."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=payload.email,
        password_hash=hash_value(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return _create_tokens(user, db)


def login_user(payload: UserLogin, db: Session) -> TokenResponse:
    """Authenticate user and issue new tokens."""
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not verify_hash(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return _create_tokens(user, db)


def refresh_access_token(refresh_token: str, db: Session) -> TokenResponse:
    """
    Rotate refresh token: issue new access + refresh, invalidate old.
    """
    token_hash = hash_token(refresh_token)

    stored = (
        db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    )

    if not stored or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Rotate: delete old token, create new ones
    user = stored.user
    db.delete(stored)

    return _create_tokens(user, db)


def logout_user(
    user_id: str, refresh_token: str | None, db: Session, all_sessions: bool = False
) -> None:
    """Revoke refresh token(s)."""
    if all_sessions:
        # Logout everywhere: delete all user refresh tokens
        db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete()
    elif refresh_token:
        # Logout single session: delete specific token
        token_hash = hash_token(refresh_token)
        db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id, RefreshToken.token_hash == token_hash
        ).delete()

    db.commit()


async def request_password_reset(payload: PasswordResetRequest, db: Session) -> None:
    """
    Initiate password reset. Always returns success (no email enumeration).
    """
    user = db.query(User).filter(User.email == payload.email).first()

    # Always return success, even if email doesn't exist (security)
    if not user:
        logger.info(f"Password reset requested for non-existent email: {payload.email}")
        return

    # Invalidate any existing tokens for this user
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()

    # Create new token
    token = create_password_reset_token()
    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.password_reset_token_expire_hours),
    )
    db.add(reset)
    db.commit()

    # Send email
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    await email_service.send_password_reset(user.email, reset_url)


def confirm_password_reset(payload: PasswordResetConfirm, db: Session) -> None:
    """Verify token and update password."""
    token_hash = hash_token(payload.token)

    reset = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
        .first()
    )

    if not reset or reset.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Update password
    user = reset.user
    user.password_hash = hash_value(payload.new_password)
    reset.used_at = datetime.now(timezone.utc)

    # Invalidate all refresh tokens (force re-login everywhere)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()

    db.commit()


def _create_tokens(user: User, db: Session) -> TokenResponse:
    """Generate access and refresh tokens."""
    access_token = create_access_token_for_user(str(user.id))
    refresh_token_value = create_refresh_token()

    refresh = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token_value),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(refresh)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_value,
        token_type="bearer",
    )
