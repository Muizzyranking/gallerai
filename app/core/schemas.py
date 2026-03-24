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


class ValidationErrorDetail(BaseModel):
    field: str
    message: str
    type: str


class ApiErrorResponse[T](BaseModel):
    message: str
    data: None = None
    errors: T | None = None


class NotFoundResponse(BaseModel):
    message: str = "Resource not found"


class BadRequestResponse(BaseModel):
    message: str = "Bad request"
