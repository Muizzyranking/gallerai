from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query


@dataclass
class PaginationParams:
    """Standard pagination parameters injected via FastAPI dependency."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        """Calculate SQL offset from page number."""
        return (self.page - 1) * self.page_size


def get_pagination(
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 50,
) -> PaginationParams:
    """FastAPI dependency that returns pagination parameters."""
    return PaginationParams(page=page, page_size=page_size)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]
