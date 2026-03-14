from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token_for_user
from app.db import get_db
from app.models.event import Event, EventMember
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid JWT. Raises 401 if missing or invalid."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token_for_user(credentials.credentials)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """
    Try to resolve a user from JWT but return None if not authenticated.
    Used for endpoints that support both registered and anonymous access.
    """
    if not credentials:
        return None
    user_id = decode_access_token_for_user(credentials.credentials)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_event_or_404(event_id: str, db: Session = Depends(get_db)) -> Event:
    """Fetch event by ID or raise 404."""
    event = (
        db.query(Event).filter(Event.id == event_id, Event.status != "deleted").first()
    )
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
        )
    return event


def require_event_organizer(
    event: Event = Depends(get_event_or_404),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Ensure the current user is the owner or a co-organizer of the event."""
    if event.owner_id == current_user.id:
        return event
    member = (
        db.query(EventMember)
        .filter(
            EventMember.event_id == event.id,
            EventMember.user_id == current_user.id,
            EventMember.role == "organizer",
            EventMember.status == "active",
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organizer access required"
        )
    return event
