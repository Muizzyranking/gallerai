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


def create_access_token(data: dict, expires_delta: timedelta | None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire})

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
    return decode_access_token(token).get("sub")
