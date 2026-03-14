from sqlalchemy.orm import Session

from app.core.enums import EventRole
from app.core.security import hash_value
from app.models.event import Event, EventMember
from app.models.user import User
from app.schemas.event import EventCreate


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
