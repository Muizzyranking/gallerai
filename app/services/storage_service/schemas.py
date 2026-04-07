from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

type StorageBackendType = Literal["local", "cloudinary"]


class LocalExtras(BaseModel):
    relative_path: str


class CloudinaryExtras(BaseModel):
    public_id: str
    resource_type: Literal["image", "video"] = "image"
    format: str
    version: int
    tags: list[str] = Field(default_factory=list)
    eager_urls: dict[str, str] = Field(default_factory=dict)


type StorageExtras = LocalExtras | CloudinaryExtras


@dataclass
class SaveResult:
    key: str
    backend: StorageBackendType
    extras: dict[str, Any]
    media_type: Literal["image", "video"]


@dataclass
class BatchSaveResult:
    succeeded: list[SaveResult]
    failed: list[tuple[str, Exception]]


@dataclass
class BatchDeleteResult:
    deleted: list[str]
    failed: list[tuple[str, Exception]]


@dataclass
class BatchDeleteByTagResult:
    tag: str
    deleted_count: int
    partial: bool
    failed_cursors: list[str]


@dataclass
class MediaURLs:
    thumbnail: str
    display: str
    download: str
