from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token_for_user,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.user import TokenResponse, UserCreate


def register_user(payload: UserCreate, db: Session) -> TokenResponse:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    access_token = create_access_token_for_user(user.id)
    return TokenResponse(access_token=access_token)


def login_user(email: str, password: str, db: Session) -> TokenResponse:
    user = db.query(User).filter(User.email == email).first()
    if (
        not user
        or not user.password_hash
        or not verify_password(password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    access_token = create_access_token_for_user(user.id)
    return TokenResponse(access_token=access_token)
