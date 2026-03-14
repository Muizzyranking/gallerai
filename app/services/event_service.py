from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.enums import (
    AccessMode,
    EventRole,
    EventStatus,
    InviteStatus,
    MemberStatus,
)
from app.core.security import hash_value, verify_hash
from app.models.event import Event, EventInvite, EventMember
from app.models.user import User
from app.schemas.event import EventCreate, EventUpdate, InviteCreate


def create_event(payload: EventCreate, owner: User, db: Session) -> Event:
    access_code_hash = None
    if payload.access_config.access_code:
        access_code_hash = hash_value(payload.access_config.access_code)

    event = Event(
        owner_id=owner.id,
        title=payload.title,
        description=payload.description,
        event_date=payload.event_date,
        access_mode=payload.access_config.access_mode,
        access_code_hash=access_code_hash,
    )
    db.add(event)
    db.flush()

    member = EventMember(
        event_id=event.id,
        user_id=owner.id,
        role=EventRole.ORGANIZER,
        added_by=owner.id,
    )

    db.add(member)
    db.commit()
    db.refresh(event)
    return event


def update_event(event: Event, payload: EventUpdate, db: Session) -> Event:
    if payload.title is not None:
        event.title = payload.title
    if payload.description is not None:
        event.description = payload.description
    if payload.event_date is not None:
        event.event_date = payload.event_date
    if payload.status is not None:
        if payload.status == EventStatus.DELETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use the delete endpoint to delete an event",
            )
        event.status = payload.status
    db.commit()
    db.refresh(event)
    return event


def delete_event(event: Event, db: Session) -> None:
    event.status = EventStatus.DELETED
    db.commit()


def verify_event_access_code(event: Event, access_code: str | None) -> Literal[True]:
    """
    Verifies the access code an event.
    Checks if the events allows code and the validity of the code.
    """
    if event.access_mode == AccessMode.LINK:
        return True

    if event.access_mode not in (AccessMode.CODE, AccessMode.COMBINED):
        raise HTTPException(
            status_code=400, detail="This event does not use access codes"
        )
    if not access_code:
        raise HTTPException(status_code=403, detail="Access code required")

    if not verify_hash(access_code, event.access_code_hash):
        raise HTTPException(status_code=403, detail="Invalid access code")

    return True


def add_co_organizer(
    event: Event, user_id: str, added_by: User, db: Session
) -> EventMember:
    if user_id == event.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already the event owner",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    existing = (
        db.query(EventMember)
        .filter(EventMember.event_id == event.id, EventMember.user_id == user_id)
        .first()
    )
    if existing:
        if existing.status == MemberStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User is already a member"
            )
        existing.status = MemberStatus.ACTIVE
        existing.role = EventRole.ORGANIZER
        db.commit()
        db.refresh(existing)
        return existing

    member = EventMember(
        event_id=event.id,
        user_id=user_id,
        role=EventRole.ORGANIZER,
        added_by=added_by.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def remove_member(event: Event, user_id: str, db: Session) -> None:
    if user_id == event.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the event owner",
        )

    member = (
        db.query(EventMember)
        .filter(
            EventMember.event_id == event.id,
            EventMember.user_id == user_id,
            EventMember.status == MemberStatus.ACTIVE,
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    member.status = MemberStatus.REMOVED
    db.commit()


def add_invites(event: Event, payload: InviteCreate, db: Session) -> list[EventInvite]:
    import secrets

    invites = []
    for email in payload.emails:
        existing = (
            db.query(EventInvite)
            .filter(EventInvite.event_id == event.id, EventInvite.email == email)
            .first()
        )
        if existing:
            if existing.status == InviteStatus.REVOKED:
                existing.status = InviteStatus.PENDING
                existing.invite_token = secrets.token_urlsafe(32)
                db.flush()
                invites.append(existing)
            continue

        invite = EventInvite(
            event_id=event.id,
            email=email,
            invite_token=secrets.token_urlsafe(32),
        )
        db.add(invite)
        db.flush()
        invites.append(invite)

    db.commit()
    return invites


def revoke_invite(event: Event, email: str, db: Session) -> None:
    invite = (
        db.query(EventInvite)
        .filter(EventInvite.event_id == event.id, EventInvite.email == email)
        .first()
    )
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    invite.status = InviteStatus.REVOKED
    db.commit()


def get_event_list(owner: User, db: Session) -> list[Event]:
    owned = (
        db.query(Event)
        .filter(Event.owner_id == owner.id, Event.status != EventStatus.DELETED)
        .all()
    )
    owned_ids = [e.id for e in owned]
    co_organized_ids = (
        db.query(EventMember.event_id)
        .filter(
            EventMember.user_id == owner.id,
            EventMember.role == EventRole.ORGANIZER,
            EventMember.status == MemberStatus.ACTIVE,
            EventMember.event_id.notin_(owned_ids),
        )
        .all()
    )
    co_organized_ids = [r[0] for r in co_organized_ids]
    co_organized = (
        db.query(Event)
        .filter(Event.id.in_(co_organized_ids), Event.status != EventStatus.DELETED)
        .all()
        if co_organized_ids
        else []
    )
    return owned + co_organized
