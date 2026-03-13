from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PhotoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    filename: str | None
    file_size: int | None
    width: int | None
    height: int | None
    face_count: int
    status: str
    is_private: bool
    uploaded_at: datetime
    processed_at: datetime | None


class PhotoBulkUploadResponse(BaseModel):
    """Returned immediately after bulk upload — photos are still processing."""

    accepted: int  # number of photos accepted for processing
    photo_ids: list[str]  # IDs created, can be used to poll status


class ProcessingStatusResponse(BaseModel):
    """Snapshot of processing progress for an event."""

    event_id: str
    total: int
    pending: int
    processing: int
    processed: int
    failed: int


class PhotoUpdateRequest(BaseModel):
    is_private: bool | None = None
