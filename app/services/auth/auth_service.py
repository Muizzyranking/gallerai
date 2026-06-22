import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.enums import AuthProvider
from app.core.security import (
    TokenPurpose,
    create_access_token_for_user,
    create_refresh_token,
    decode_token,
    hash_password,
    revoke_all_user_tokens,
    revoke_refresh_token,
    verify_refresh_token,
)
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.services.auth.utils import (
    generate_email_confirm_token,
    generate_pending_email_token,
)
from app.services.email_service import EmailService, EmailTemplate

logger = logging.getLogger(__name__)

email_service = EmailService.from_settings()

RESEND_COOLDOWN_SECONDS = 60


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def issue_token_pair(
    db: Session,
    user: User,
    request: Request,
) -> tuple[str, str]:
    """Return (access_token, raw_refresh_token)."""
    access_token = create_access_token_for_user(user)
    raw_refresh = create_refresh_token(
        db,
        str(user.id),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return access_token, raw_refresh


def register_user(
    payload: UserCreate,
    db: Session,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Create a new user, fire confirmation email in background.
    Returns (TokenResponse, raw_refresh_token).
    """
    if get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=payload.email,
        display_name=payload.display_name,
        auth_provider=AuthProvider.LOCAL,
        is_email_confirmed=False,
    )
    user.hash_password(payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)

    schedule_confirmation_email(background_tasks, user)


def login_user(
    payload: UserLogin,
    db: Session,
    request: Request,
) -> TokenResponse:
    """
    Validate credentials, issue token pair.
    Returns (TokenResponse, raw_refresh_token).
    """
    user = get_user_by_email(db, payload.email)

    if not user or not user.verify_password(payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    if not user.is_email_confirmed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address is not confirmed. Please check your inbox for the confirmation email.",
        )

    access_token, raw_refresh = issue_token_pair(db, user, request)
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


def logout_user(db: Session, user: User, raw_refresh_token: str | None) -> None:
    """Revoke the provided refresh token (single-device logout)."""
    user.bump_token_version()
    if raw_refresh_token:
        revoke_refresh_token(db, raw_refresh_token)
    db.commit()


def refresh_access_token(
    db: Session,
    raw_refresh_token: str | None,
    request: Request,
) -> TokenResponse:
    """
    Validate the refresh token cookie, revoke the old one (rotation),
    and issue a fresh access + refresh token pair.
    """
    if not raw_refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided.",
        )

    token_obj = verify_refresh_token(db, raw_refresh_token)
    if not token_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    user = get_user_by_id(db, str(token_obj.user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )

    revoke_refresh_token(db, raw_refresh_token)
    access_token, new_raw_refresh = issue_token_pair(db, user, request)
    return TokenResponse(access_token=access_token, refresh_token=new_raw_refresh)


def update_me(
    payload: UserUpdate,
    current_user: User,
    db: Session,
    background_tasks: BackgroundTasks,
) -> UserResponse:
    if payload.display_name is not None:
        current_user.display_name = payload.display_name

    if payload.email and payload.email != current_user.email:
        if get_user_by_email(db, payload.email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use.",
            )
        current_user.pending_email = payload.email

        confirm_token = generate_pending_email_token(payload.email)
        background_tasks.add_task(
            email_service.send,
            payload.email,
            EmailTemplate.VERIFY_EMAIL,
            {
                "subject": "Confirm your new email address",
                "display_name": current_user.display_name or current_user.email,
                "confirm_url": f"{_frontend_url()}/confirm-email?token={confirm_token}&type=email_change",
            },
        )

    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


def change_password(
    payload: ChangePasswordRequest,
    current_user: User,
    db: Session,
) -> None:
    if current_user.auth_provider != AuthProvider.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth accounts cannot change passwords here.",
        )
    if not current_user.verify_password(payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if current_user.verify_password(payload.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )
    current_user.hash_password(payload.new_password)
    current_user.bump_token_version()
    revoke_all_user_tokens(db, str(current_user.id))
    db.commit()


def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Always returns 200 – never reveal whether an email exists.
    """
    user = get_user_by_email(db, payload.email)
    if user and user.auth_provider == AuthProvider.LOCAL:
        token = ""
        background_tasks.add_task(
            email_service.send,
            user.email,
            EmailTemplate.PASSWORD_RESET,
            {
                "subject": "Reset your password",
                "display_name": user.display_name or user.email,
                "reset_url": f"{_frontend_url()}/reset-password?token={token}",
                "expires_in_minutes": 60,
            },
        )


def reset_password(
    payload: ResetPasswordRequest,
    db: Session,
) -> None:
    # verify password
    email = ""
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired.",
        )

    user = get_user_by_email(db, email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found.",
        )

    user.password_hash = hash_password(payload.new_password)
    revoke_all_user_tokens(db, str(user.id))
    db.commit()


def confirm_email(token: str, db: Session) -> None:
    """
    Handles both initial confirmation (EMAIL_CONFIRM) and email-change
    confirmation (EMAIL_CHANGE). Decode once, branch on purpose.
    """
    try:
        payload = decode_token(token)
    except HTTPException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This confirmation link is invalid or has expired.",
        ) from e

    purpose = payload.get("purpose")
    email = payload.get("sub")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This confirmation link is invalid or has expired.",
        )

    if purpose == str(TokenPurpose.EMAIL_CHANGE):
        user = db.query(User).filter(User.pending_email == email).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token is invalid or this change has already been applied.",
            )
        user.email = email
        user.pending_email = None
        user.is_email_confirmed = True
        user.bump_token_version()
        revoke_all_user_tokens(db, str(user.id))
        db.commit()

    elif purpose == str(TokenPurpose.EMAIL_CONFIRM):
        user = get_user_by_email(db, email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found.",
            )
        if user.is_email_confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already confirmed.",
            )
        user.is_email_confirmed = True
        db.commit()

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This confirmation link is invalid or has expired.",
        )


def resend_confirmation(
    email: str, db: Session, background_tasks: BackgroundTasks
) -> None:
    """Rate-limited via last_confirmation_sent_at on the User model (no Redis needed)."""
    user = get_user_by_email(db, email)

    if not user or user.is_email_confirmed:
        return

    now = datetime.now(tz=timezone.utc)
    if user.last_confirmation_sent_at:
        elapsed = (now - user.last_confirmation_sent_at).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {int(RESEND_COOLDOWN_SECONDS - elapsed)}s before requesting another email.",
                headers={"Retry-After": str(int(RESEND_COOLDOWN_SECONDS - elapsed))},
            )

    user.last_confirmation_sent_at = now
    db.commit()

    schedule_confirmation_email(background_tasks, user)


def schedule_confirmation_email(background_tasks: BackgroundTasks, user: User) -> None:
    token = generate_email_confirm_token(user.email)
    background_tasks.add_task(
        email_service.send,
        user.email,
        EmailTemplate.VERIFY_EMAIL,
        {
            "subject": "Confirm your email address",
            "display_name": user.display_name or user.email,
            "verification_link": f"{_frontend_url()}/confirm-email?token={token}",
        },
    )


def _frontend_url() -> str:
    from app.core.config import settings

    return settings.frontend_url.rstrip("/")
