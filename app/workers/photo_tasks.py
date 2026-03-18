import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_mongo_collection():
    """
    I am using pymongo sync since it is a bg task.
    """
    from pymongo import MongoClient

    from app.core.config import settings

    client = MongoClient(settings.mongo_url)
    return client[settings.mongo_db]["face_embeddings"]


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="photo_tasks.process_photo",
)
def process_photo_task(self, photo_id: str):
    """
    Process a single photo through the face detection pipeline.
        - Mark the photo as "processing" in the database.
        - Load image from storage
        - Run DeepFace face detection and ArcFace embedding extraction.
        - store each face embedding doc in Mongo
        - Mark photo as "processed" with face count
    """
    from app.core.config import settings
    from app.db.postgres import SessionLocal
    from app.models import Photo
    from app.services.face_service import detect_faces
    from app.services.photo_service import (
        mark_photo_as_failed,
        mark_photo_as_processed,
        mark_photo_as_processing,
    )
    from app.services.storage_service import storage

    db = SessionLocal()
    try:
        photo = db.query(Photo).filter(Photo.id == photo_id).first()
        if not photo:
            logger.error(f"Photo with ID {photo_id} not found.")
            return {"status": "skipped", "reason": "Photo not found"}
        mark_photo_as_processing(photo, db)
        logger.info(f"Started processing photo ID {photo_id}")

        image_path = storage.load(photo.storage_key)
        faces = detect_faces(image_path)
        logger.info(f"Photo {photo_id} — detected {len(faces)} valid faces")
        if faces:
            collection = _get_mongo_collection()
            documents = [
                {
                    "event_id": photo.event_id,
                    "photo_id": photo.id,
                    "embedding": face["embedding"],
                    "bounding_box": face["bounding_box"],
                    "detection_confidence": face["confidence"],
                    "face_index": face["face_index"],
                    "model_version": settings.face_model_name,
                    "created_at": datetime.now(timezone.utc),
                }
                for face in faces
            ]
            collection.insert_many(documents)
            logger.debug(f"Stored {len(documents)} embeddings for photo {photo_id}")

        mark_photo_as_processed(photo, len(faces), db)
        logger.info(
            f"Finished processing photo ID {photo_id} with {len(faces)} faces detected."
        )
        return {"status": "processed", "photo_id": photo_id, "face_count": len(faces)}
    except Exception as e:
        logger.exception(f"Error processing photo ID {photo_id}: {e}")
        try:
            photo = db.query(Photo).filter(Photo.id == photo_id).first()
            if photo:
                mark_photo_as_failed(photo, str(e), db=db)
        except Exception:
            logger.exception(
                f"Failed to mark photo - {photo_id} as failed in the database."
            )
        raise self.retry(exc=e) from e
    finally:
        db.close()
