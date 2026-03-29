import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


def search_event_for_user(
    db: Session,
    embedding: list[float],
    event_id: str,
    threshold: float | None = None,
    limit: int = 1000,
) -> dict[str, float]:
    """
    Search faces in event using pgvector cosine similarity.
    Returns: {photo_id: best_similarity_score}
    """
    threshold = threshold or settings.face_similarity_threshold

    stmt = text("""
        SELECT 
            photo_id::text as photo_id,
            MAX(1 - (embedding <=> :query_vec)) as similarity
        FROM face_embeddings
        WHERE event_id = :event_id
          AND 1 - (embedding <=> :query_vec) > :threshold
        GROUP BY photo_id
        ORDER BY similarity DESC
        LIMIT :limit
    """)

    result = db.execute(
        stmt,
        {
            "query_vec": str(embedding),
            "event_id": event_id,
            "threshold": threshold,
            "limit": limit,
        },
    )

    matches = {row.photo_id: float(row.similarity) for row in result}

    logger.info(
        f"Search complete — event={event_id} "
        f"matched_photos={len(matches)} threshold={threshold}"
    )
    return matches
