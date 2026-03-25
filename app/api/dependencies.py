from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.enums import AccessMode, EventRole, InviteStatus, MemberStatus
from app.core.security import decode_access_token_for_user, verify_hash
from app.db import get_db
from app.models.event import Event, EventInvite, EventMember
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]

Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_user(credentials: Credentials, db: DB) -> User:
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


def get_current_user_optional(credentials: Credentials, db: DB) -> User | None:
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


def get_event_or_404(event_id: str, db: DB) -> Event:
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
    event: Annotated[Event, Depends(get_event_or_404)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: DB,
) -> Event:
    """Ensure the current user is the owner or a co-organizer of the event."""
    if event.owner_id == current_user.id:
        return event
    member = (
        db.query(EventMember)
        .filter(
            EventMember.event_id == event.id,
            EventMember.user_id == current_user.id,
            EventMember.role == EventRole.ORGANIZER,
            EventMember.status == MemberStatus.ACTIVE,
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organizer access required"
        )
    return event


def user_is_invited(db: Session, event_id: str, email: str) -> bool:
    return (
        db.query(EventInvite)
        .filter(
            EventInvite.event_id == event_id,
            EventInvite.email == email,
            EventInvite.status.in_([InviteStatus.PENDING, InviteStatus.ACCEPTED]),
        )
        .first()
        is not None
    )


def valid_access_code(event: Event, access_code: str | None) -> bool:
    if not access_code or not event.access_code_hash:
        return False
    return verify_hash(access_code, event.access_code_hash)


def user_is_active_member(db: Session, event_id: str, user_id: str) -> bool:
    """Return True if the user is an active member of the event."""
    return (
        db.query(EventMember)
        .filter(
            EventMember.event_id == event_id,
            EventMember.user_id == user_id,
            EventMember.status == MemberStatus.ACTIVE,
        )
        .first()
        is not None
    )


def get_event_access(
    event: Annotated[Event, Depends(get_event_or_404)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    access_code: Annotated[str | None, Query(description="Event access code")] = None,
    db: DB = None,
) -> Event:
    """
    Validate attendee-level access to an event.

    For logged-in users:
    - Existing active members always pass
    - New users are verified by access mode then granted membership automatically
      (except link mode which auto-grants without verification)

    For anonymous users:
    - Verified per request via access code or link
    - No membership record created

    Auto-grant rules by access mode:
    - link: auto-grant membership (explicit "add to list" is a UI concern)
    - code: grant after successful code verification
    - approved_list: grant after invite check
    - combined: grant after either invite check or code verification
    """
    from app.services.event_service import grant_attendee_membership

    if current_user and user_is_active_member(db, event.id, current_user.id):
        return event

    # Link mode
    if event.access_mode == AccessMode.LINK:
        if current_user:
            grant_attendee_membership(event, current_user, db)
        return event

    # Code mode
    if event.access_mode == AccessMode.CODE:
        if not valid_access_code(event, access_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid access code",
            )
        if current_user:
            grant_attendee_membership(event, current_user, db)
        return event

    # Approved list mode
    if event.access_mode == AccessMode.APPROVED_LIST:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Login required for this event",
            )
        if not user_is_invited(db, event.id, current_user.email):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not on the approved list",
            )
        grant_attendee_membership(event, current_user, db)
        return event

    # Combined mode
    if event.access_mode == AccessMode.COMBINED:
        if current_user and user_is_invited(db, event.id, current_user.email):
            grant_attendee_membership(event, current_user, db)
            return event
        if not valid_access_code(event, access_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid access code",
            )
        if current_user:
            grant_attendee_membership(event, current_user, db)
        return event

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """
    Require the current user to be an admin.
    Raises 403 if the user does not have is_admin=True.
    Also checks is_active — deactivated admins lose access.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_current_user_optional)]
EventOr404 = Annotated[Event, Depends(get_event_or_404)]
OrganizerEvent = Annotated[Event, Depends(require_event_organizer)]
AccessibleEvent = Annotated[Event, Depends(get_event_access)]
AdminUser = Annotated[User, Depends(require_admin)]
