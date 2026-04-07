import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from celery import group
from starlette.datastructures import UploadFile as StarletteUpload

from app.core.config import settings
from app.core.enums import MediaStatus, StorageBackend, StorageStatus
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro) -> Any:
    """Run an async coroutine from a synchronous Celery worker thread."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _promote_to_cloud(media, local_storage, cloud_storage) -> Any:
    """
    Read the local scratch file and upload it to cloud using the stable
    storage_key already on the media record.
    """
    local_path = local_storage.load(media.storage_key, media.extras)
    with open(local_path, "rb") as fh:
        content = fh.read()

    upload = StarletteUpload(
        filename=media.filename or media.storage_key,
        headers={"content-type": media.mime_type},
        file=BytesIO(content),
    )

    return await cloud_storage.save(
        upload,
        event_id=media.event_id,
        subfolder="photos",
        key=media.storage_key,
    )


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    name="media_tasks.upload_media",
)
def upload_media_task(self, media_id: str) -> dict[str, Any]:
    """
    Promote a media file from local scratch storage to cloud.

    Idempotent: if storage_status is already UPLOADED, returns immediately.
    On success: updates storage_backend, storage_status, extras, uploaded_at.
    On failure: marks storage_status as UPLOAD_FAILED and schedules retry.
    Does NOT delete the local file — cleanup_local_task handles that.
    """
    from app.db.postgres import SessionLocal
    from app.models.media import Media
    from app.services.storage_service import cloud_storage, local_storage

    db = SessionLocal()
    try:
        media = db.query(Media).filter(Media.id == media_id).first()
        if not media:
            logger.error("upload_media_task: media %s not found", media_id)
            return {"status": "skipped", "reason": "not_found"}

        if media.storage_status == StorageStatus.UPLOADED:
            logger.info("upload_media_task: media %s already uploaded", media_id)
            return {"status": "already_uploaded"}

        media.storage_status = StorageStatus.UPLOADING
        db.commit()

        result = _run_async(_promote_to_cloud(media, local_storage, cloud_storage))

        media.storage_backend = StorageBackend(result.backend)
        media.storage_status = StorageStatus.UPLOADED
        media.extras = result.extras
        media.uploaded_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "upload_media_task: media %s uploaded to %s", media_id, result.backend
        )

        cleanup_local_task.delay(media_id=media_id)  # type: ignore

        return {"status": "uploaded", "media_id": media_id, "backend": result.backend}

    except Exception as exc:
        db.rollback()
        logger.exception("upload_media_task: error on %s: %s", media_id, exc)
        try:
            media = db.query(Media).filter(Media.id == media_id).first()
            if media:
                media.storage_status = StorageStatus.UPLOAD_FAILED
                media.error_message = str(exc)
                db.commit()
        except Exception:
            logger.exception("upload_media_task: could not mark %s as failed", media_id)
        raise self.retry(exc=exc) from exc

    finally:
        db.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="media_tasks.detect_faces",
)
def detect_faces_task(self, media_id: str) -> dict[str, Any]:
    """
    Run face detection and embedding extraction on a locally stored image.

    Idempotent: if status is already PROCESSED, returns immediately.
    Zero faces found is a valid outcome — photo is still marked PROCESSED.
    On failure: marks status as FAILED and schedules retry.
    Does NOT delete the local file — cleanup_local_task handles that.
    """
    from app.db.postgres import SessionLocal
    from app.models.face_embedding import FaceEmbedding
    from app.models.media import Media
    from app.services.face_service import detect_faces
    from app.services.storage_service import local_storage

    db = SessionLocal()
    try:
        media = db.query(Media).filter(Media.id == media_id).first()
        if not media:
            logger.error("detect_faces_task: media %s not found", media_id)
            return {"status": "skipped", "reason": "not_found"}

        if media.status == MediaStatus.PROCESSED:
            logger.info("detect_faces_task: media %s already processed", media_id)
            return {"status": "already_processed"}

        if not media.is_image:
            logger.info(
                "detect_faces_task: media %s is not an image, skipping", media_id
            )
            return {"status": "skipped", "reason": "not_image"}

        media.status = MediaStatus.PROCESSING
        db.commit()

        image_path = local_storage.load(media.storage_key, media.extras)
        faces = detect_faces(image_path)

        logger.info(
            "detect_faces_task: media %s — %d face(s) detected", media_id, len(faces)
        )

        if faces:
            embeddings = [
                FaceEmbedding(
                    event_id=media.event_id,
                    media_id=media.id,
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

        media.status = MediaStatus.PROCESSED
        media.face_count = len(faces)
        media.processed_at = datetime.now(timezone.utc)
        db.commit()

        cleanup_local_task.delay(media_id=media_id)  # type: ignore

        return {"status": "processed", "media_id": media_id, "face_count": len(faces)}

    except Exception as exc:
        db.rollback()
        logger.exception("detect_faces_task: error on %s: %s", media_id, exc)
        try:
            media = db.query(Media).filter(Media.id == media_id).first()
            if media:
                media.status = MediaStatus.FAILED
                media.error_message = str(exc)
                db.commit()
        except Exception:
            logger.exception("detect_faces_task: could not mark %s as failed", media_id)
        raise self.retry(exc=exc) from exc

    finally:
        db.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="media_tasks.cleanup_local",
)
def cleanup_local_task(self, media_id: str) -> dict[str, Any]:
    """
    Delete the local scratch file for a media record — but only when it is
    safe to do so (both upload and processing pipelines are in a terminal state).

    Both upload_media_task and detect_faces_task dispatch this task on
    completion. The first call may find conditions not yet met and return
    early; the second call will find both done and actually delete.

    This task never fails permanently — a missing file is logged and ignored.
    """
    from app.db.postgres import SessionLocal
    from app.models.media import Media
    from app.services.storage_service import local_storage

    db = SessionLocal()
    try:
        media = db.query(Media).filter(Media.id == media_id).first()
        if not media:
            logger.warning("cleanup_local_task: media %s not found", media_id)
            return {"status": "skipped", "reason": "not_found"}

        if not media.is_local_safe_to_delete:
            logger.info(
                "cleanup_local_task: media %s not ready — storage_status=%s status=%s",
                media_id,
                media.storage_status,
                media.status,
            )
            return {
                "status": "deferred",
                "storage_status": media.storage_status,
                "processing_status": media.status,
            }

        if media.storage_backend != StorageBackend.LOCAL:
            try:
                _run_async(local_storage.delete(media.storage_key))
                logger.info(
                    "cleanup_local_task: deleted local scratch file for %s", media_id
                )
            except Exception as exc:
                logger.warning(
                    "cleanup_local_task: could not delete local file for %s: %s",
                    media_id,
                    exc,
                )
        else:
            logger.info(
                "cleanup_local_task: media %s storage_backend is still LOCAL — "
                "upload may not have updated the record yet, skipping deletion",
                media_id,
            )
            return {"status": "deferred", "reason": "backend_still_local"}

        return {"status": "cleaned", "media_id": media_id}

    except Exception as exc:
        logger.exception("cleanup_local_task: error on %s: %s", media_id, exc)
        raise self.retry(exc=exc) from exc

    finally:
        db.close()


def dispatch_image_tasks(media_id: str) -> None:
    """
    Dispatch upload + face detection concurrently for an image.
    Both tasks run independently; cleanup_local_task is triggered by each
    on completion and self-gates on the combined terminal state.
    """
    task_group = group(
        upload_media_task.s(media_id=media_id),  # type: ignore
        detect_faces_task.s(media_id=media_id),  # type: ignore
    )
    task_group.delay()


def dispatch_video_tasks(media_id: str) -> None:
    """
    Dispatch only the upload task for a video (no face pipeline).
    cleanup_local_task is triggered by upload_media_task on completion.
    """
    upload_media_task.delay(media_id=media_id)  # type: ignore


@celery_app.task(name="photo_tasks.warmup_models")
def warmup_models_task() -> dict:
    """
    Pre-load DeepFace model weights into memory.
    Called once at worker startup via celery signals.
    """
    from app.services.face_service import warmup

    warmup()
    return {"status": "warmed up"}
