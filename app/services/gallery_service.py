import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.cache import Cache
from app.core.config import settings
from app.core.enums import FlagReason
from app.models.gallery import UserEventGallery
from app.models.user import User
from app.schemas.gallery import GalleryPhotoResponse, GalleryResponse
from app.schemas.photo import PhotoSchema

logger = logging.getLogger(__name__)

SCAN_TOKEN_PREFIX = "scan:"

scan_cache = Cache(namespace="scan", ttl=settings.anonymous_scan_ttl_seconds)
gallery_cache = Cache(namespace="gallery", ttl=300)


async def store_anonymous_results(
    event_id: str, matches: dict[str, float], embedding: list[float]
) -> str:
    """
    Store anonymous scan result in Redis with TTL.
    Returns a short-lived token that can be used to retrieve the results.
    """
    token = secrets.token_urlsafe(32)
    payload = {
        "event_id": event_id,
        "matches": matches,
        "embedding": embedding,
    }
    await scan_cache.set(token, payload)
    logger.debug(f"Stored anonymous scan result in Redis with token {token}")
    return token


async def get_anonymous_results(token: str, event_id: str) -> dict[str, float]:
    """
    Returns annonymous scan results from redis by token.
    Validates the token belongs to the given event
    """
    data = await scan_cache.get(token)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token expired or not found",
        )

    if data["event_id"] != event_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to event",
        )
    return data


async def upsert_gallery_entries(
    user: User, event_id: str, matches: dict[str, float], db: Session
) -> int:
    """
    Save or update matched photos into a user gallery for an event.
    if entry exists, update score if new score is higher.
    Returns the number of new entries excluding updates.
    """
    new_count = 0
    for media_id, score in matches.items():
        existing = (
            db.query(UserEventGallery)
            .filter(
                UserEventGallery.user_id == user.id,
                UserEventGallery.event_id == event_id,
                UserEventGallery.photo_id == media_id,
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
                media_id=media_id,
                match_score=score,
            )
            db.add(entry)
            new_count += 1

    db.commit()
    await gallery_cache.invalidate_pattern(f"user:{user.id}:ev:{event_id}:*")
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
    data = await get_anonymous_results(token, event_id)
    matches = data.get("matches")
    anonymous_embedding = data.get("embedding")
    new_count = await upsert_gallery_entries(user, event_id, matches, db)

    if anonymous_embedding:
        user.face_embedding = anonymous_embedding
        user.face_scan_hash = None
        user.face_updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Saved face embedding for user {user.id} from claim")

    await scan_cache.invalidate(token)
    logger.info(
        f"Gallery claimed for user {user.id} and event {event_id}: {new_count} new entries merged"
    )
    return new_count


async def get_user_gallery(
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
    cache_key = (
        f"user:{user.id}:ev:{event_id}:p:{page}:ps:{page_size}:f:{include_flagged}"
    )

    async def fetch_gallery_from_db():
        query = (
            db.query(UserEventGallery)
            .options(joinedload(UserEventGallery.media))
            .filter(
                UserEventGallery.user_id == user.id,
                UserEventGallery.event_id == event_id,
            )
            .order_by(UserEventGallery.match_score.desc())
        )
        if not include_flagged:
            query = query.filter(UserEventGallery.is_flagged == False)  # noqa: E712

        total = query.count()
        entries = query.offset((page - 1) * page_size).limit(page_size).all()

        return GalleryResponse(
            event_id=event_id,
            total=total,
            page=page,
            page_size=page_size,
            photos=[
                GalleryPhotoResponse(
                    id=entry.id,
                    photo=PhotoSchema.model_validate(entry.media),
                    match_score=entry.match_score,
                    is_flagged=entry.is_flagged,
                    flag_reason=entry.flag_reason,
                    flagged_at=entry.flagged_at,
                    created_at=entry.created_at,
                ).model_dump()
                for entry in entries
            ],
        ).model_dump()

    return await gallery_cache.get_or_set(cache_key, fetch_gallery_from_db)


async def flag_gallery_entry(
    user: User,
    event_id: str,
    photo_id: str,
    db: Session,
    reason: FlagReason | None = None,
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
    entry.flag_reason = reason
    entry.flagged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    await gallery_cache.invalidate_pattern(f"user:{user.id}:ev:{event_id}:*")
    return entry


async def unflag_gallery_entry(
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
    entry.flag_reason = None
    entry.flagged_at = None
    db.commit()
    db.refresh(entry)
    await gallery_cache.invalidate_pattern(f"user:{user.id}:ev:{event_id}:*")
    return entry
