import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.enums import FlagReason
from app.db import get_redis
from app.models.gallery import UserEventGallery
from app.models.user import User
from app.schemas.gallery import GalleryPhotoResponse, GalleryResponse
from app.schemas.photo import PhotoSchema

logger = logging.getLogger(__name__)

SCAN_TOKEN_PREFIX = "scan:"


async def store_anonymous_result(event_id: str, matches: dict[str, float]) -> str:
    """
    Store anonymous scan result in Redis with TTL.
    Returns a short-lived token that can be used to retrieve the results.
    """
    token = secrets.token_urlsafe(32)
    key = f"{SCAN_TOKEN_PREFIX}{token}"
    payload = json.dumps({"event_id": event_id, "matches": matches})

    redis = await get_redis()
    await redis.setex(key, settings.anonymous_scan_ttl_seconds, payload)
    logger.debug(f"Stored anonymous scan result in Redis with key {key}")
    return token


async def get_anonymous_result_key(token: str, event_id: str) -> dict[str, float]:
    """
    Returns annonymous scan results from redis by token.
    Validates the token belongs to the given event
    """
    redis = await get_redis()
    raw = await redis.get(f"{SCAN_TOKEN_PREFIX}{token}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token expired or not found",
        )

    data = json.loads(raw)
    if data["event_id"] != event_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to event",
        )
    return data["matches"]


async def delete_anonymous_result_key(token: str) -> None:
    """
    Deletes an anonymous scan result from redis by token.
    """
    redis = await get_redis()
    await redis.delete(f"{SCAN_TOKEN_PREFIX}{token}")
    logger.debug(f"Deleted anonymous scan result from Redis with key {token}")


def upsert_gallery_entries(
    user: User, event_id: str, matches: dict[str, float], db: Session
) -> int:
    """
    Save or update matched photos into a user gallery for an event.
    if entry exists, update score if new score is higher.
    Returns the number of new entries excluding updates.
    """
    new_count = 0
    for photo_id, score in matches.items():
        existing = (
            db.query(UserEventGallery)
            .filter(
                UserEventGallery.user_id == user.id,
                UserEventGallery.event_id == event_id,
                UserEventGallery.photo_id == photo_id,
            )
            .first()
        )
        if existing:
            if score > (existing.match_score or 0.0):
                existing.match_score = score
                db.flush()
        else:
            entry = UserEventGallery(
                user_id=user.id,
                event_id=event_id,
                photo_id=photo_id,
                match_score=score,
            )
            db.add(entry)
            new_count += 1

    db.commit()
    logger.info(
        f"Gallery updated for user {user.id} and event {event_id}: {new_count} new entries, total matches {len(matches)}"
    )
    return new_count


async def claim_anonymous_gallery(
    token: str, event_id: str, user: User, db: Session
) -> int:
    """
    Merge anonymous gallery results into the user's gallery and delete the anonymous token.
    Consumes the token, so it can only be used once.
    """
    matches = await get_anonymous_result_key(token, event_id)
    new_count = upsert_gallery_entries(user, event_id, matches, db)
    await delete_anonymous_result_key(token)
    logger.info(
        f"Gallery claimed for user {user.id} and event {event_id}: {new_count} new entries merged"
    )
    return new_count


def get_user_gallery(
    user: User,
    event_id: str,
    db: Session,
    page: int = 1,
    page_size: int = 50,
    include_flagged: bool = False,
) -> GalleryResponse:
    """
    Returns a paginated gallery for a user in an event
    """

    query = (
        db.query(UserEventGallery)
        .options(joinedload(UserEventGallery.photo))
        .filter(
            UserEventGallery.user_id == user.id, UserEventGallery.event_id == event_id
        )
        .order_by(UserEventGallery.match_score.desc())
    )
    if not include_flagged:
        query = query.filter(UserEventGallery.is_flagged == False)  # noqa: E712

    total = query.count()
    entries = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        GalleryPhotoResponse(
            id=entry.id,
            photo=PhotoSchema.model_validate(entry.photo),
            match_score=entry.match_score,
            is_flagged=entry.is_flagged,
            # flag_reason=entry.flagged_at,
            flagged_at=entry.flagged_at,
            created_at=entry.created_at,
        )
        for entry in entries
    ]

    return GalleryResponse(
        event_id=event_id,
        total=total,
        page=page,
        page_size=page_size,
        photos=items,
    )


def flag_gallery_entry(
    user: User,
    event_id: str,
    photo_id: str,
    reason: FlagReason,
    db: Session,
) -> UserEventGallery:
    """
    Soft-flag a gallery entry with a reason.
    The entry stays in the database — it's just hidden from normal gallery view.
    """
    entry = (
        db.query(UserEventGallery)
        .filter(
            UserEventGallery.user_id == user.id,
            UserEventGallery.event_id == event_id,
            UserEventGallery.photo_id == photo_id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gallery entry not found",
        )
    entry.is_flagged = True
    # entry.flag_reason = reason
    entry.flagged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return entry


def unflag_gallery_entry(
    user: User,
    event_id: str,
    photo_id: str,
    db: Session,
) -> UserEventGallery:
    """Restore a flagged gallery entry to visible state."""
    entry = (
        db.query(UserEventGallery)
        .filter(
            UserEventGallery.user_id == user.id,
            UserEventGallery.event_id == event_id,
            UserEventGallery.photo_id == photo_id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gallery entry not found",
        )
    entry.is_flagged = False
    # entry.flag_reason = None
    entry.flagged_at = None
    db.commit()
    db.refresh(entry)
    return entry
