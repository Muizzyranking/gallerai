import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import PhotoStatus
from app.models.face_embedding import FaceEmbedding
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _copy_embeddings(db: Session, source_photo_id: str, target_photo_id: str) -> int:
    """Copy embeddings from duplicate photo."""
    source_embs = (
        db.query(FaceEmbedding).filter(FaceEmbedding.photo_id == source_photo_id).all()
    )

    for emb in source_embs:
        new_emb = FaceEmbedding(
            event_id=emb.event_id,
            photo_id=target_photo_id,
            embedding=emb.embedding,
            bounding_box=emb.bounding_box,
            detection_confidence=emb.detection_confidence,
            face_index=emb.face_index,
            model_version=emb.model_version,
            created_at=datetime.now(timezone.utc),
        )
        db.add(new_emb)

    return len(source_embs)


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

        if photo.status == PhotoStatus.PROCESSED:
            logger.info(f"Photo {photo_id} already processed")
            return {"status": "already_processed"}

        if not photo.file_hash:
            logger.error(f"Photo {photo_id} missing file_hash")
            mark_photo_as_failed(photo, "Missing file_hash", db)
            db.commit()
            return {"status": "failed", "reason": "missing_hash"}

        mark_photo_as_processing(photo, db)
        logger.info(f"Started processing photo ID {photo_id}")

        duplicate = (
            db.query(Photo)
            .filter(
                Photo.event_id == photo.event_id,
                Photo.file_hash == photo.file_hash,
                Photo.id != photo.id,
                Photo.status == PhotoStatus.PROCESSED,
            )
            .first()
        )

        if duplicate:
            logger.info(f"Photo {photo_id} is duplicate of {duplicate.id}")
            face_count = _copy_embeddings(db, duplicate.id, photo.id)
            mark_photo_as_processed(photo, face_count=face_count, db=db)
            db.commit()
            return {"status": "duplicate", "original_id": duplicate.id}

        image_path = storage.load(photo.storage_key)
        faces = detect_faces(image_path)
        logger.info(f"Photo {photo_id} — detected {len(faces)} valid faces")

        if faces:
            embeddings = [
                FaceEmbedding(
                    event_id=photo.event_id,
                    photo_id=photo.id,
                    embedding=face["embedding"],
                    bounding_box=face["bounding_box"],
                    detection_confidence=face["confidence"],
                    face_index=face["face_index"],
                    model_version=settings.face_model_name,
                    created_at=datetime.now(timezone.utc),
                )
                for face in faces
            ]
            db.bulk_save_objects(embeddings)

        mark_photo_as_processed(photo, len(faces), db)
        logger.info(
            f"Finished processing photo ID {photo_id} with {len(faces)} faces detected."
        )
        return {"status": "processed", "photo_id": photo_id, "face_count": len(faces)}
    except Exception as e:
        db.rollback()
        logger.exception(f"Error processing photo ID {photo_id}: {e}")
        try:
            photo = db.query(Photo).filter(Photo.id == photo_id).first()
            if photo:
                mark_photo_as_failed(photo, str(e), db=db)
                db.commit()
        except Exception:
            logger.exception(
                f"Failed to mark photo - {photo_id} as failed in the database."
            )
        raise self.retry(exc=e) from e
    finally:
        db.close()


@celery_app.task(name="photo_tasks.warmup_models")
def warmup_models_task() -> dict:
    """
    Pre-load DeepFace model weights into memory.
    Called once at worker startup via celery signals.
    """
    from app.services.face_service import warmup

    warmup()
    return {"status": "warmed up"}
