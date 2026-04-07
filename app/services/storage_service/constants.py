from typing import Any

ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)
ALLOWED_VIDEO_TYPES: frozenset[str] = frozenset(
    {"video/mp4", "video/quicktime", "video/webm"}
)
ALLOWED_CONTENT_TYPES: frozenset[str] = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

MAX_IMAGE_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_VIDEO_BYTES: int = 200 * 1024 * 1024  # 200 MB
STREAM_CHUNK_SIZE: int = 1024 * 256  # 256 KB

CLOUDINARY_PRESETS: dict[str, list[dict[str, Any]]] = {
    "thumbnail": [
        {"width": 400, "crop": "fill", "gravity": "auto"},
        {"fetch_format": "auto", "quality": "auto"},
    ],
    "display": [
        {"width": 1920, "crop": "limit"},
        {"fetch_format": "auto", "quality": "auto:good"},
    ],
    "download": [
        {"flags": "attachment"},
    ],
}
