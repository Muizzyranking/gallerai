import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.services.face_service import find_matching_embeddings

logger = logging.getLogger(__name__)


async def search_event_for_user(
    embedding: list[float],
    event_id: str,
    db: AsyncIOMotorDatabase,
    threshold: float | None = None,
) -> dict[str, float]:
    """
    Search all the face embeddings in a particular event for mataches with the given embeddings.

    Fetches all embeddings for event from mongodb, runs batched cosine_similarity, deduplicates photos,
    and returns a dict of photo_id to similarity score for all photos that have a match above the threshold.
    """
    threshold = threshold or settings.face_similarity_threshold

    cursor = db["face_embeddings"].find(
        {"event_id": event_id}, {"photo_id": 1, "embedding": 1, "_id": 0}
    )
    candidates = await cursor.to_list(length=None)
    if not candidates:
        logger.debug(f"No embeddings found for event -{event_id}")
        return {}

    logger.debug(f"Found {len(candidates)} candidate embeddings for event {event_id}")

    matches = find_matching_embeddings(embedding, candidates, threshold)

    best_per_photo: dict[str, float] = {}
    for match in matches:
        photo_id = match["photo_id"]
        score = match["score"]
        if (photo_id not in best_per_photo) or (score > best_per_photo[photo_id]):
            best_per_photo[photo_id] = score

    logger.info(
        f"Search complete — event={event_id} "
        f"candidates={len(candidates)} "
        f"matched_photos={len(best_per_photo)}"
    )
    return best_per_photo
