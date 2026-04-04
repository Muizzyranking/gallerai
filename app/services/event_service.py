import asyncio
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.cache import event_cache, event_list_cache
from app.core.enums import (
    AccessMode,
    EventRole,
    EventStatus,
    InviteStatus,
    MemberStatus,
)
from app.core.security import hash_value, verify_hash
from app.models.event import DEFAULT_EVENT_SETTINGS, Event, EventInvite, EventMember
from app.models.user import User
from app.schemas import MemberAdd
from app.schemas.event import EventCreate, EventResponse, EventUpdate, InviteCreate


async def create_event(payload: EventCreate, owner: User, db: Session) -> Event:
    access_code_hash = None

    if payload.access_config.access_mode in (AccessMode.CODE, AccessMode.COMBINED):
        if not payload.access_config.access_code:
            raise HTTPException(
                status_code=400,
                detail="An access code is required for code and combined access modes",
            )
    if payload.access_config.access_code:
        access_code_hash = hash_value(payload.access_config.access_code)

    event = Event(
        owner_id=owner.id,
        title=payload.title,
        description=payload.description,
        event_date=payload.event_date,
        access_mode=payload.access_config.access_mode,
        access_code_hash=access_code_hash,
        settings={**DEFAULT_EVENT_SETTINGS, **payload.settings.model_dump()},
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
    await event_list_cache.invalidate(f"managed:{owner.id}")
    return event


async def update_event(event: Event, payload: EventUpdate, db: Session) -> Event:
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
    if payload.settings is not None:
        event.settings = {**event.settings, **payload.settings.model_dump()}
    db.commit()
    db.refresh(event)
    await asyncio.gather(
        event_cache.invalidate(f"detail:{event.id}"),
        event_list_cache.invalidate(f"managed:{event.owner_id}"),
    )
    return event


async def delete_event(event: Event, db: Session) -> None:
    event.status = EventStatus.DELETED
    db.commit()
    await asyncio.gather(
        event_cache.invalidate(f"detail:{event.id}"),
        event_list_cache.invalidate(f"managed:{event.owner_id}"),
    )


def verify_event_access_code(
    event: EventResponse, access_code: str | None
) -> Literal[True]:
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


async def grant_attendee_membership(
    event: Event,
    user: User,
    db: Session,
) -> EventMember:
    """
    Add a user as an attendee member of an event.
    If they were previously removed, reactivates their membership.
    Called after successful access verification for logged-in users.
    """
    existing = (
        db.query(EventMember)
        .filter(EventMember.event_id == event.id, EventMember.user_id == user.id)
        .first()
    )
    if existing:
        if existing.status == MemberStatus.ACTIVE:
            return existing
        existing.status = MemberStatus.ACTIVE
        db.commit()
        db.refresh(existing)
        return existing

    member = EventMember(
        event_id=event.id,
        user_id=user.id,
        role=EventRole.ATTENDEE,
        added_by=None,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    await event_list_cache.invalidate(f"attended:{user.id}")
    return member


async def add_co_organizer(
    event: Event, payload: MemberAdd, added_by: User, db: Session
) -> EventMember:
    user_id = payload.user_id
    email = payload.email

    if not email and not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either email or user_id is required",
        )

    user = None

    if email:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email not found",
            )
    else:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    if user.id == event.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already the event owner",
        )

    existing = (
        db.query(EventMember)
        .filter(
            EventMember.event_id == event.id,
            EventMember.user_id == user.id,
        )
        .first()
    )

    if existing:
        if existing.status == MemberStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member",
            )

        existing.status = MemberStatus.ACTIVE
        existing.role = EventRole.ORGANIZER
        db.commit()
        db.refresh(existing)
        return existing

    member = EventMember(
        event_id=event.id,
        user_id=user.id,
        role=EventRole.ORGANIZER,
        added_by=added_by.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    await event_list_cache.invalidate(f"managed:{user_id}")
    return member


async def remove_member(event: Event, user_id: str, db: Session) -> None:
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
    await asyncio.gather(
        event_list_cache.invalidate(f"managed:{event.owner_id}"),
        event_list_cache.invalidate(f"attended:{event.owner_id}"),
    )


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
    """
    Return all events the user owns, co-organizes, or is an active attendee of.
    """
    memberships = (
        db.query(EventMember)
        .filter(
            EventMember.user_id == owner.id,
            EventMember.status == MemberStatus.ACTIVE,
        )
        .all()
    )
    event_ids = [m.event_id for m in memberships]
    if not event_ids:
        return []
    return (
        db.query(Event)
        .filter(Event.id.in_(event_ids), Event.status != EventStatus.DELETED)
        .order_by(Event.created_at.desc())
        .all()
    )


async def get_managed_events(user: User, db: Session):
    async def fetch():
        members = (
            db.query(EventMember)
            .filter(
                EventMember.user_id == user.id,
                EventMember.role == EventRole.ORGANIZER,
                EventMember.status == MemberStatus.ACTIVE,
            )
            .all()
        )
        ids = [m.event_id for m in members]
        if not ids:
            return []
        events = (
            db.query(Event)
            .filter(Event.id.in_(ids), Event.status != EventStatus.DELETED)
            .order_by(Event.created_at.desc())
            .all()
        )
        return [EventResponse.model_validate(e).model_dump() for e in events]

    return await event_list_cache.get_or_set(f"managed:{user.id}", fetch)


async def get_attended_events(user: User, db: Session):
    """
    Return events the user is an attendee of (not organizer).
    Used for the attendee "my events" view.
    """

    async def fetch():
        members = (
            db.query(EventMember)
            .filter(
                EventMember.user_id == user.id,
                EventMember.role == EventRole.ATTENDEE,
                EventMember.status == MemberStatus.ACTIVE,
            )
            .all()
        )
        ids = [m.event_id for m in members]
        if not ids:
            return []
        events = (
            db.query(Event)
            .filter(Event.id.in_(ids), Event.status != EventStatus.DELETED)
            .order_by(Event.created_at.desc())
            .all()
        )
        return [EventResponse.model_validate(e).model_dump() for e in events]

    return await event_list_cache.get_or_set(f"attended:{user.id}", fetch)
