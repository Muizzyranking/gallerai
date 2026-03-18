import logging
from pathlib import Path

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)


def warmup() -> None:
    """
    Preload the face recognition model and perform a dummy inference to ensure
    that the model is loaded into memory and ready for use.
    This can help reduce latency for the first real inference request.
    """
    import numpy as np
    from deepface import DeepFace

    logger.info(
        f"Warming up DeepFace — model={settings.face_model_name} detector={settings.face_detector_backend}"
    )
    try:
        # Build the model by running a dummy embedding on a blank image
        dummy = np.zeros((224, 224, 3), dtype=np.uint8)
        DeepFace.represent(
            img_path=dummy,
            model_name=settings.face_model_name,
            detector_backend=settings.face_detector_backend,
            enforce_detection=False,
        )
        logger.info("DeepFace warmup complete")
    except Exception as e:
        logger.warning(f"DeepFace warmup failed — will load on first task: {e}")


def detect_faces(image_path: str | Path):
    """
    Detect faces in the given image and return a list of face data.
    Each face data dict contains the bounding box and the embedding vector.

    Filters out the following:
        - faces with detection confidence below settings.face_detection_confidence
        - faces whose bounding box is smaller than settings.face_min_size
    """
    from deepface import DeepFace

    try:
        results = DeepFace.represent(
            img_path=str(image_path),
            model_name=settings.face_model_name,
            detector_backend=settings.face_detector_backend,
            enforce_detection=False,
            align=True,
        )
    except Exception as e:
        logger.error(f"Deepface.represent failed for {image_path}: {e}")
        return []

    valid_faces = []
    for i, result in enumerate(results):
        facial_area = result.get("facial_area", {})  # type: ignore
        confidence = result.get("confidence", 0.0)  # type: ignore
        embedding = result.get("embedding", [])  # type: ignore

        if confidence < settings.face_detection_confidence:
            logger.debug(
                f"Skipping face {i} — confidence {confidence:.3f} below threshold {settings.face_detection_confidence}"
            )
            continue

        w = facial_area.get("w", 0)
        h = facial_area.get("h", 0)

        if w < settings.face_min_size or h < settings.face_min_size:
            logger.debug(
                f"Skipping face {i} — size {w}x{h} below minimum {settings.face_min_size}px"
            )
            continue

        valid_faces.append(
            {
                "embedding": embedding,
                "bounding_box": {
                    "x": facial_area.get("x", 0),
                    "y": facial_area.get("y", 0),
                    "width": w,
                    "height": h,
                },
                "confidence": confidence,
                "face_index": i,
            }
        )

    valid_faces.sort(key=lambda f: f["confidence"], reverse=True)
    logger.debug(
        f"Detected {len(valid_faces)} valid faces in {image_path} (total found: {len(results)})"
    )
    return valid_faces


def extract_single_embedding(image_path: str | Path) -> list[float] | None:
    """
    Extract a single face embedding from an image
    Intended for user face scan

    If multiple faces are detected, uses the largest one

    Returns:
        512-dim embedding as a list of floats, or None if no valid face found
    """
    faces = detect_faces(image_path)
    if not faces:
        logger.info(f"No valid face detected in {image_path}")
        return None

    if len(faces) == 1:
        return faces[0]["embedding"]

    largest = max(
        faces, key=lambda f: f["bounding_box"]["width"] * f["bounding_box"]["height"]
    )
    logger.debug(
        f"Multiple faces detected — using largest face (index={largest['face_index']})"
    )
    return largest["embedding"]


def cosine_similarity_batch(
    query: list[float], matrix: list[list[float]]
) -> np.ndarray:
    """
    Compute cosine similarity between a query embedding and a matrix of embeddings.
    Uses NumPy batched matrix multiplication — efficient for up to ~50k embeddings.

    Returns:
        1D numpy array of similarity scores in range [-1, 1].
        Higher is more similar. Typical match threshold is 0.6.
    """
    q = np.array(query, dtype=np.float32)
    m = np.array(matrix, dtype=np.float32)

    q_norm = q / (np.linalg.norm(q) + 1e-10)
    m_norms = np.linalg.norm(m, axis=1, keepdims=True) + 1e-10
    m_normalized = m / m_norms

    return m_normalized @ q_norm


def find_matching_embeddings(
    query: list[float], candidates: list[dict], threshold: float | None = None
) -> list[dict]:
    """
    Find all candidate face embeddings that match the query above the threshold.

    Returns:
        List of matched candidates sorted by score descending.
        Each item includes the original document plus a "score" field.
    """
    if not candidates:
        return []

    threshold = threshold or settings.face_similarity_threshold
    embeddings = [c["embedding"] for c in candidates]
    scores = cosine_similarity_batch(query, embeddings)

    matches = []
    for candidate, score in zip(candidates, scores, strict=True):
        if float(score) >= threshold:
            matches.append({**candidate, "score": float(score)})

    matches.sort(key=lambda x: x["score"], reverse=True)
    logger.debug(
        f"Found {len(matches)} matches above threshold {threshold} from {len(candidates)} candidates"
    )
    return matches
