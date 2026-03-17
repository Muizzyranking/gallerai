from typing import TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiResponse[T](BaseModel):
    message: str = "Request processed successfully"
    data: T | None = None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )
