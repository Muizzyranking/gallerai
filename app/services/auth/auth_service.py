import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.db.postgres import fetch_one
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
    decode_typed_payload,
    generate_email_confirm_token,
    generate_password_reset_token,
    generate_pending_email_token,
)
from app.services.email_service import EmailService, EmailTemplate

logger = logging.getLogger(__name__)

email_service = EmailService.from_settings()

RESEND_COOLDOWN_SECONDS = 60


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await fetch_one(db, User, User.email == email)


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    return await fetch_one(db, User, User.id == user_id)


async def issue_token_pair(
    db: AsyncSession,
    user: User,
    request: Request,
) -> tuple[str, str]:
    """Return (access_token, raw_refresh_token)."""
    access_token = create_access_token_for_user(user)
    raw_refresh = await create_refresh_token(
        db,
        str(user.id),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return access_token, raw_refresh


def derive_display_name(email: str, fallback: str | None) -> str:
    if fallback and fallback.strip():
        return fallback.strip()

    local = email.split("@")[0]
    name = local.replace(".", " ").replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in name.split())


async def register_user(
    payload: UserCreate,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Create a new user, fire confirmation email in background.
    Returns (TokenResponse, raw_refresh_token).
    """
    if await get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=payload.email,
        display_name=derive_display_name(payload.email, payload.display_name),
        auth_provider=AuthProvider.LOCAL,
        is_email_confirmed=False,
    )
    user.hash_password(payload.password)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    schedule_confirmation_email(background_tasks, user)


async def login_user(
    payload: UserLogin,
    db: AsyncSession,
    request: Request,
) -> TokenResponse:
    """
    Validate credentials, issue token pair.
    Returns (TokenResponse, raw_refresh_token).
    """
    user = await get_user_by_email(db, payload.email)

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

    # if not user.is_email_confirmed:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Email address is not confirmed. Please check your inbox for the confirmation email.",
    #     )

    access_token, raw_refresh = await issue_token_pair(db, user, request)
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


async def logout_user(
    db: AsyncSession,
    user: User,
    raw_refresh_token: str | None,
    *,
    all_sessions: bool = False,
) -> None:
    """Revoke refresh tokens for single-session or all-session logout."""
    user.bump_token_version()
    if all_sessions:
        await revoke_all_user_tokens(db, str(user.id))
    elif raw_refresh_token:
        await revoke_refresh_token(db, raw_refresh_token)
    await db.commit()


async def refresh_access_token(
    db: AsyncSession,
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

    token_obj = await verify_refresh_token(db, raw_refresh_token)
    if not token_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    user = await get_user_by_id(db, str(token_obj.user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )
    if not user.is_email_confirmed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address is not confirmed.",
        )

    await revoke_refresh_token(db, raw_refresh_token)
    access_token, new_raw_refresh = await issue_token_pair(db, user, request)
    return TokenResponse(access_token=access_token, refresh_token=new_raw_refresh)


async def update_me(
    payload: UserUpdate,
    current_user: User,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> UserResponse:
    if payload.display_name is not None:
        current_user.display_name = payload.display_name

    if payload.email and payload.email != current_user.email:
        email_owner = await get_user_by_email(db, payload.email)
        pending_owner = await fetch_one(db, User, User.pending_email == payload.email)
        if email_owner or (pending_owner and pending_owner.id != current_user.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use.",
            )
        if current_user.pending_email == payload.email:
            await db.commit()
            await db.refresh(current_user)
            return UserResponse.model_validate(current_user)

        current_user.pending_email = payload.email

        confirm_token = generate_pending_email_token(payload.email)
        background_tasks.add_task(
            email_service.send,
            payload.email,
            EmailTemplate.VERIFY_EMAIL,
            {
                "display_name": current_user.display_name or current_user.email,
                "verification_link": f"{_frontend_url()}/confirm-email?token={confirm_token}&type=email_change",
            },
            subject="Confirm your new email address",
        )

    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


async def change_password(
    payload: ChangePasswordRequest,
    current_user: User,
    db: AsyncSession,
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
    await revoke_all_user_tokens(db, str(current_user.id))
    await db.commit()


async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> None:
    """
    Always returns 200 – never reveal whether an email exists.
    """
    user = await get_user_by_email(db, payload.email)
    if not user or user.auth_provider != AuthProvider.LOCAL or not user.is_active:
        return

    token = generate_password_reset_token(user.email, user.token_version or 0)
    background_tasks.add_task(
        email_service.send_password_reset,
        to=user.email,
        reset_url=f"{_frontend_url()}/reset-password?token={token}",
        expires_in_minutes=60,
        display_name=user.display_name or user.email,
    )


async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession,
) -> None:
    token_payload = decode_typed_payload(payload.token, TokenPurpose.PASSWORD_RESET)
    email = str(token_payload["sub"])

    user = await get_user_by_email(db, email)
    if not user or not user.is_active or user.auth_provider != AuthProvider.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired.",
        )
    if token_payload.get("version") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired.",
        )

    if user.verify_password(payload.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )
    user.password_hash = hash_password(payload.new_password)
    user.bump_token_version()
    await revoke_all_user_tokens(db, str(user.id))
    await db.commit()


async def confirm_email(token: str, db: AsyncSession) -> None:
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
        user = await fetch_one(db, User, User.pending_email == email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token is invalid or this change has already been applied.",
            )
        existing_user = await get_user_by_email(db, email)
        if existing_user and existing_user.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use.",
            )
        user.email = email
        user.pending_email = None
        user.is_email_confirmed = True
        user.bump_token_version()
        await revoke_all_user_tokens(db, str(user.id))
        await db.commit()

    elif purpose == str(TokenPurpose.EMAIL_CONFIRM):
        user = await get_user_by_email(db, email)
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
        await db.commit()

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This confirmation link is invalid or has expired.",
        )


async def resend_confirmation(
    email: str, db: AsyncSession, background_tasks: BackgroundTasks
) -> None:
    """Rate-limited via last_confirmation_sent_at on the User model (no Redis needed)."""
    user = await get_user_by_email(db, email)

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
    await db.commit()

    schedule_confirmation_email(background_tasks, user)


async def delete_account(
    current_user: User,
    db: AsyncSession,
    *,
    current_password: str | None = None,
) -> None:
    """Soft-delete the account by deactivating it and revoking all sessions."""
    if current_user.auth_provider == AuthProvider.LOCAL:
        if not current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required to delete this account.",
            )
        if not current_user.verify_password(current_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )

    if not current_user.is_active:
        return

    current_user.is_active = False
    current_user.pending_email = None
    current_user.bump_token_version()
    await revoke_all_user_tokens(db, str(current_user.id))
    await db.commit()


def schedule_confirmation_email(background_tasks: BackgroundTasks, user: User) -> None:
    token = generate_email_confirm_token(user.email)
    background_tasks.add_task(
        email_service.send_email_verification,
        user.email,
        f"{_frontend_url()}/confirm-email?token={token}",
        user.display_name or user.email,
    )


def _frontend_url() -> str:
    from app.core.config import settings

    return settings.frontend_url.rstrip("/")
