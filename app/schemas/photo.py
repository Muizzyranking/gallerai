from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.enums import PhotoStatus


class PhotoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    filename: str | None
    file_size: int | None
    width: int | None
    height: int | None
    face_count: int
    status: PhotoStatus
    is_private: bool
    uploaded_at: datetime
    processed_at: datetime | None


class PhotoBulkUploadResponse(BaseModel):
    accepted: int
    photo_ids: list[str]


class ProcessingStatusResponse(BaseModel):
    event_id: str
    total: int
    pending: int
    processing: int
    processed: int
    failed: int


class PhotoUpdateRequest(BaseModel):
    is_private: bool | None = None
