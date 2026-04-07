import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

pwd_hash = PasswordHash.recommended()


def hash_value(value: str) -> str:
    return pwd_hash.hash(value)


def verify_hash(plain_value: str | None, hashed_value: str | None) -> bool:
    if not plain_value or not hashed_value:
        return False
    return pwd_hash.verify(plain_value, hashed_value)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire, "type": "access"})

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def create_access_token_for_user(user_id: str) -> str:
    return create_access_token(data={"sub": user_id}, expires_delta=None)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        return payload
    except jwt.PyJWTError:
        return {}


def decode_access_token_for_user(token: str) -> str | None:
    payload = decode_access_token(token)
    if payload.get("type") != "access":
        return None
    return payload.get("sub")


def verify_access_token(token: str) -> str | None:
    return decode_access_token_for_user(token)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_hash(token: str, hashed: str) -> bool:
    return hash_token(token) == hashed


def create_password_reset_token() -> str:
    return secrets.token_urlsafe(64)
