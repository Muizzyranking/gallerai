from sqlalchemy.orm import Session

from app.models.user import User


def resolve_user(
    db: Session, user_id: str | None = None, email: str | None = None
) -> User | None:
    """
    Resolves a user from the id or email
    """
    if user_id is None and email is None:
        return None

    user = None

    if email:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        user = db.query(User).filter(User.id == user_id).first()

    return user


def to_seconds(
    days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0
) -> int:
    """Convert days, hours, and minutes to total seconds."""
    return (days * 86400) + (hours * 3600) + (minutes * 60)
