from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_event_or_404,
    require_event_organizer,
)
from app.db.postgres import get_db
from app.models.event import Event
from app.models.user import User
from app.schemas.event import (
    EventAccessVerify,
    EventCreate,
    EventResponse,
    EventUpdate,
    InviteCreate,
    MemberAdd,
)
from app.services import event_service

router = APIRouter()


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return event_service.create_event(payload, current_user, db)


@router.get("", response_model=list[EventResponse])
def list_my_events(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns all events the current user owns or co-organizes."""
    return event_service.get_event_list(current_user, db)


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event: Event = Depends(get_event_or_404)):
    return event


@router.patch("/{event_id}", response_model=EventResponse)
def update_event(
    payload: EventUpdate,
    event: Event = Depends(require_event_organizer),
    db: Session = Depends(get_db),
):
    return event_service.update_event(event, payload, db)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event: Event = Depends(require_event_organizer),
    db: Session = Depends(get_db),
):
    event_service.delete_event(event, db)


@router.post("/{event_id}/access/verify", status_code=status.HTTP_200_OK)
def verify_access(payload: EventAccessVerify, event: Event = Depends(get_event_or_404)):
    """Verify an access code. Returns 200 if valid, 403 if not."""
    event_service.verify_event_access_code(event, payload.access_code)
    return {"message": "Access granted"}


@router.post("/{event_id}/members", status_code=status.HTTP_201_CREATED)
def add_co_organizer(
    payload: MemberAdd,
    event: Event = Depends(require_event_organizer),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = event_service.add_co_organizer(event, payload.user_id, current_user, db)
    return {"message": "Co-organizer added", "member_id": member.id}


@router.delete("/{event_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str,
    event: Event = Depends(require_event_organizer),
    db: Session = Depends(get_db),
):
    event_service.remove_member(event, user_id, db)


@router.post("/{event_id}/invites", status_code=status.HTTP_201_CREATED)
def add_invites(
    payload: InviteCreate,
    event: Event = Depends(require_event_organizer),
    db: Session = Depends(get_db),
):
    invites = event_service.add_invites(event, payload, db)
    return {"message": f"{len(invites)} invite(s) created", "count": len(invites)}


@router.delete("/{event_id}/invites/{email}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    email: str,
    event: Event = Depends(require_event_organizer),
    db: Session = Depends(get_db),
):
    event_service.revoke_invite(event, email, db)
