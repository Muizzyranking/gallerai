from enum import StrEnum


class EventStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AccessMode(StrEnum):
    LINK = "link"
    CODE = "code"
    APPROVED_LIST = "approved_list"
    COMBINED = "combined"


class EventRole(StrEnum):
    ORGANIZER = "organizer"
    ATTENDEE = "attendee"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class InviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class PhotoStatus(StrEnum):
    """
    pending_approval: attendee upload awaiting organizer approval
    rejected: organizer rejected the upload — soft kept for audit
    pending: approved and queued for face processing
    processing: Celery worker currently processing
    processed: face detection complete
    failed: face detection failed after all retries
    """

    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class FlagReason(StrEnum):
    """
    Reason a user flagged a photo in their gallery.
    not_me: face recognition false positive
    dislike: personal preference, not a match error
    removed: user removed without giving a reason
    """

    NOT_ME = "not_me"
    DISLIKE = "dislike"
    REMOVED = "removed"


class MediaStatus(StrEnum):
    """Tracks the face-detection / processing pipeline for an image."""

    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class StorageStatus(StrEnum):
    """Tracks the cloud-promotion pipeline independently of face detection."""

    LOCAL = "local"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    UPLOAD_FAILED = "upload_failed"


class StorageBackend(StrEnum):
    """Which storage backend currently holds the file."""

    LOCAL = "local"
    CLOUDINARY = "cloudinary"


class MediaType(StrEnum):
    """Broad media category — determines which processing pipeline runs."""

    IMAGE = "image"
    VIDEO = "video"


class AuthProvider(StrEnum):
    LOCAL = "local"
    GOOGLE = "google"
