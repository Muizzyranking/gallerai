import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import AuthProvider
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.services.auth.auth_service import get_user_by_email, issue_token_pair

logger = logging.getLogger(__name__)


async def get_user_by_google_id(db: AsyncSession, google_id: str) -> User | None:
    stmt = select(User).where(User.google_id == google_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _get_google_user_info(code: str, redirect_uri: str) -> dict[str, Any]:
    """
    Exchange the authorisation code for tokens, then fetch the user profile.
    Raises HTTPException on any failure.
    """
    _ensure_google_configured()

    import httpx

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        logger.error("Google token exchange failed: %s", token_resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to exchange Google authorisation code.",
        )

    google_tokens = token_resp.json()
    access_token = google_tokens.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No access token returned by Google.",
        )

    # 2. Fetch user profile
    profile_resp = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if profile_resp.status_code != 200:
        logger.error("Google profile fetch failed: %s", profile_resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch Google user profile.",
        )

    return profile_resp.json()


async def google_callback(
    code: str,
    db: AsyncSession,
    request: Request,
    state: str | None = None,
) -> TokenResponse:
    """
    Handle the OAuth callback:
    - If google_id already linked → log in.
    - If email exists as local account → merge (link google_id).
    - Otherwise → create a new user.
    """
    redirect_uri = _google_redirect_uri()
    profile = _get_google_user_info(code, redirect_uri)

    google_id: str = profile.get("id", "")
    email: str = profile.get("email", "").lower()
    name: str = profile.get("name", "")
    verified: bool = profile.get("verified_email", False)

    if state:
        logger.debug("Google OAuth callback state received")

    if not google_id or not email or not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return a verified email profile.",
        )

    user = await get_user_by_google_id(db, google_id)
    if user:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been deactivated.",
            )
        access_token, raw_refresh = await issue_token_pair(db, user, request)
        return TokenResponse(access_token=access_token, refresh_token=raw_refresh)

    # Email already registered locally → merge
    user = await get_user_by_email(db, email)
    if user:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been deactivated.",
            )
        user.google_id = google_id
        if verified and not user.is_email_confirmed:
            user.is_email_confirmed = True
        await db.commit()
        await db.refresh(user)
        access_token, raw_refresh = await issue_token_pair(db, user, request)
        return TokenResponse(access_token=access_token, refresh_token=raw_refresh)

    # Brand new user via Google
    user = User(
        email=email,
        display_name=name or None,
        auth_provider=AuthProvider.GOOGLE,
        google_id=google_id,
        is_email_confirmed=verified,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token, raw_refresh = await issue_token_pair(db, user, request)
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


def get_google_auth_url() -> str:
    """Build the Google OAuth consent URL."""
    _ensure_google_configured()

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def _google_redirect_uri() -> str:
    return settings.google_redirect_uri.rstrip("/")


def _ensure_google_configured() -> None:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth is not configured.",
        )
