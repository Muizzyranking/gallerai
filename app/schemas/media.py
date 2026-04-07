from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.enums import MediaStatus, MediaType, StorageBackend, StorageStatus


class MediaURLs(BaseModel):
    """Pre-resolved URL variants for a single media asset."""

    thumbnail: str
    display: str
    download: str


class _MediaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    media_type: MediaType
    filename: str | None
    face_count: int
    urls: MediaURLs
    created_at: datetime

    # Video-only
    duration_seconds: float | None = None


class MediaCard(_MediaBase):
    """
    Lean gallery grid card for public event attendees.
    """

    pass


class MediaCardOrganiser(_MediaBase):
    """
    Gallery grid card for the event organiser.
    """

    status: MediaStatus
    storage_status: StorageStatus
    is_private: bool


class _MediaDetailBase(_MediaBase):
    """Common detail fields for both public and organiser views."""

    width: int | None
    height: int | None
    file_size: int | None
    mime_type: str
    processed_at: datetime | None
    uploaded_at: datetime | None


class MediaDetail(_MediaDetailBase):
    """
    Full media detail for public attendees.
    """

    pass


class MediaDetailOrganiser(_MediaDetailBase):
    """
    Full media detail for the event organiser.
    Includes pipeline status, privacy, storage backend, and error message.
    """

    status: MediaStatus
    storage_status: StorageStatus
    storage_backend: StorageBackend
    is_private: bool
    error_message: str | None


class MediaListResponse(BaseModel):
    """Paginated list of lean gallery cards — public view."""

    total: int
    page: int
    page_size: int
    items: list[MediaCard]


class MediaListOrganiserResponse(BaseModel):
    """Paginated list of lean gallery cards — organiser view."""

    total: int
    page: int
    page_size: int
    items: list[MediaCardOrganiser]


class MediaBulkUploadResponse(BaseModel):
    accepted: int
    skipped: int = 0
    media_ids: list[str]


class ProcessingStatusResponse(BaseModel):
    """
    Count breakdown of both pipeline statuses for an event.
    """

    event_id: str
    total: int

    pending: int = 0
    pending_approval: int = 0
    processing: int = 0
    processed: int = 0
    failed: int = 0

    storage_local: int = 0
    storage_uploading: int = 0
    storage_uploaded: int = 0
    storage_upload_failed: int = 0


class MediaUpdateRequest(BaseModel):
    """Payload for PATCH /{media_id} — currently only privacy toggle."""

    is_private: bool | None = None


class BulkDownloadRequest(BaseModel):
    """Request body for select-and-download endpoint."""

    media_ids: list[str]


class AnonymousGalleryDownloadRequest(BaseModel):
    """Request body for anonymous gallery download using scan token."""

    scan_token: str
