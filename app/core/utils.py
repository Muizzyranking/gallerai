import hashlib
import uuid

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.media import Media
from app.models.user import User

MAX_KEY_ATTEMPTS = 5


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


async def compute_file_hash(file: UploadFile) -> str:
    """Compute SHA-256 hash without consuming file."""
    content = await file.read()
    await file.seek(0)
    return hashlib.sha256(content).hexdigest()


def generate_unique_media_key(db: Session) -> str:
    """
    Generate a storage_key that does not already exist in the media table.
    Collisions are vanishingly rare (UUID4 hex) but we guard against them
    explicitly so the service layer owns this guarantee, not the DB error.
    """
    for _ in range(1, MAX_KEY_ATTEMPTS + 1):
        candidate = uuid.uuid4().hex
        exists = db.query(Media.id).filter(Media.storage_key == candidate).first()
        if not exists:
            return candidate
    raise RuntimeError(
        f"Could not generate a unique storage_key after {MAX_KEY_ATTEMPTS} attempts."
    )
