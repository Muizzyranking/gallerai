import logging
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Adds a unique X-Request-ID header to every response.
    If the client sends an X-Request-ID header, that value is used instead
    of generating a new one.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request, attach request ID to response and log context."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
