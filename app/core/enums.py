import enum


class EventStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AccessMode(str, enum.Enum):
    LINK = "link"
    CODE = "code"
    APPROVED_LIST = "approved_list"
    COMBINED = "combined"


class EventRole(str, enum.Enum):
    ORGANIZER = "organizer"
    ATTENDEE = "attendee"


class MemberStatus(str, enum.Enum):
    ACTIVE = "active"
    REMOVED = "removed"


class InviteStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class PhotoStatus(str, enum.Enum):
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


class FlagReason(str, enum.Enum):
    """
    Reason a user flagged a photo in their gallery.
    not_me: face recognition false positive
    dislike: personal preference, not a match error
    removed: user removed without giving a reason
    """

    NOT_ME = "not_me"
    DISLIKE = "dislike"
    REMOVED = "removed"
