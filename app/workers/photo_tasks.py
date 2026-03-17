import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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
    from app.db.postgres import SessionLocal
    from app.models import Photo
    from app.services.photo_service import (
        mark_photo_as_failed,
        mark_photo_as_processed,
        mark_photo_as_processing,
    )

    db = SessionLocal()
    try:
        photo = db.query(Photo).filter(Photo.id == photo_id).first()
        if not photo:
            logger.error(f"Photo with ID {photo_id} not found.")
            return {"status": "skipped", "reason": "Photo not found"}
        mark_photo_as_processing(photo, db)
        logger.info(f"Started processing photo ID {photo_id}")
        # ======================================================
        # TODO: implement face service calll
        face_count = 0
        # ======================================================

        mark_photo_as_processed(photo, face_count, db)
        logger.info(
            f"Finished processing photo ID {photo_id} with {face_count} faces detected."
        )
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
