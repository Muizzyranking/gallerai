import enum
import hashlib
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.tokens import RefreshToken
from app.models.user import User

pwd_hash = PasswordHash.recommended()

ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
REFRESH_TOKEN_EXPIRE_DAYS: int = 30


class TokenPurpose(enum.StrEnum):
    ACCESS = "access"
    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE = "email_change"
    EMAIL_CONFIRM = "email_confirm"


def hash_value(value: str) -> str:
    return pwd_hash.hash(value)


def verify_hash(plain_value: str | None, hashed_value: str | None) -> bool:
    if not plain_value or not hashed_value:
        return False
    return pwd_hash.verify(plain_value, hashed_value)


def hash_password(value: str) -> str:
    return pwd_hash.hash(value)


def verify_password_hash(plain_value: str | None, hashed_value: str | None) -> bool:
    if not plain_value or not hashed_value:
        return False
    return pwd_hash.verify(plain_value, hashed_value)


def create_token(
    payload: dict[str, Any],
    expires: timedelta,
    purpose: TokenPurpose | str | None = None,
) -> str:
    """Sign a JWT with an expiry. Caller supplies all claims except `iat`/`exp`."""
    now = datetime.now(UTC)
    data: dict[str, Any] = {**payload, "iat": now, "exp": now + expires}
    if purpose is not None:
        data["purpose"] = str(purpose)
    return jwt.encode(data, settings.secret_key, algorithm=settings.algorithm)


def decode_token(
    token: str,
    expected_purpose: TokenPurpose | str | None = None,
) -> dict[str, Any]:
    """
    Decode and validate a signed JWT.
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if expected_purpose is not None and payload.get("purpose") != str(expected_purpose):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token purpose",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def create_access_token_for_user(user: User) -> str:
    return create_token(
        {"sub": str(user.id), "version": user.token_version or 0},
        expires=timedelta(minutes=settings.access_token_expire_minutes),
        purpose=TokenPurpose.ACCESS,
    )


def decode_access_token_for_user(token: str) -> tuple[str | None, int | None]:
    result = decode_token(token, expected_purpose=TokenPurpose.ACCESS)
    return result.get("sub"), result.get("version")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_hash(token: str, hashed: str) -> bool:
    return hash_token(token) == hashed


def create_password_reset_token() -> str:
    return secrets.token_urlsafe(64)


def create_refresh_token(
    db: Session,
    user_id: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> str:
    """
    Generate a raw refresh token, persist its hash, and return the raw value.
    The caller is responsible for setting it as an httpOnly cookie.
    """
    raw = RefreshToken.generate_raw()
    token = RefreshToken(
        user_id=user_id,
        token_hash=RefreshToken.hash(raw),
        expires_at=datetime.now(tz=timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(token)
    db.commit()
    return raw


def verify_refresh_token(db: Session, raw_token: str) -> RefreshToken | None:
    """
    Look up a refresh token by its hash and validate it.
    """
    token_hash = RefreshToken.hash(raw_token)
    token: RefreshToken | None = (
        db.query(RefreshToken).filter_by(token_hash=token_hash).first()
    )
    if token is None or not token.is_valid:
        return None
    return token


def revoke_refresh_token(db: Session, raw_token: str) -> bool:
    token_hash = RefreshToken.hash(raw_token)
    token: RefreshToken | None = (
        db.query(RefreshToken).filter_by(token_hash=token_hash).first()
    )
    if token is None:
        return False
    token.revoked = True
    db.commit()
    return True


def revoke_all_user_tokens(db: Session, user_id: str) -> int:
    updated = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
        )
        .update({"revoked": True})
    )
    db.commit()
    return updated
