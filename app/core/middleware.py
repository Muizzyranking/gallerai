import logging
import re
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,64}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Adds a unique X-Request-ID header to every response.
    If the client sends an X-Request-ID header, that value is used instead
    of generating a new one.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request, attach request ID to response and log context."""
        client_id = request.headers.get("X-Request-ID", "")
        if client_id and REQUEST_ID_RE.match(client_id):
            request_id = client_id
        else:
            request_id = uuid.uuid4().hex

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
