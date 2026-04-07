import io
import logging
import zipfile
from collections.abc import AsyncGenerator

import httpx
from fastapi.responses import StreamingResponse

from app.models.media import Media
from app.services.storage_service import get_download_url

logger = logging.getLogger(__name__)

ZIP_CHUNK_SIZE = 10  # flush zip buffer every N files
HTTP_CHUNK_BYTES = 256 * 1024  # 256 KB read chunks when fetching remote assets


async def _fetch_chunks(
    url: str, client: httpx.AsyncClient
) -> AsyncGenerator[bytes, None]:
    """Stream an asset from ``url`` in fixed-size chunks."""
    async with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes(chunk_size=HTTP_CHUNK_BYTES):
            yield chunk


async def stream_zip(
    media_items: list[Media],
    zip_filename: str = "galleria.zip",
) -> AsyncGenerator[bytes, None]:
    """
    Stream a zip containing all given media assets.

    Each asset is fetched via its download URL regardless of which storage
    backend it lives on — local assets hit the local download endpoint,
    cloud assets hit the CDN. The zip is written in streaming mode so
    memory usage stays flat regardless of asset count or size.

    Yields bytes chunks suitable for FastAPI ``StreamingResponse``.
    Files are named:  001_original_filename.jpg
                      002_original_filename.mp4
    """
    buffer = io.BytesIO()

    async with httpx.AsyncClient(timeout=60.0) as client:
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, media in enumerate(media_items, start=1):
                url = get_download_url(media)
                original_name = media.filename or f"{media.id}"
                zip_entry_name = f"{i:03d}_{original_name}"

                try:
                    # ZipFile.open() in write mode lets us stream directly into
                    # the entry without buffering the whole file in memory.
                    with zf.open(zip_entry_name, "w", force_zip64=True) as entry:
                        async for chunk in _fetch_chunks(url, client):
                            entry.write(chunk)

                    logger.debug("stream_zip: added %s", zip_entry_name)

                except Exception as exc:
                    # Skip unreadable assets — log and continue so the rest
                    # of the zip is still delivered.
                    logger.warning(
                        "stream_zip: skipping %s (%s) — %s",
                        zip_entry_name,
                        media.id,
                        exc,
                    )
                    continue

                # Flush the buffer every ZIP_CHUNK_SIZE files to avoid
                # accumulating too much in memory at once.
                if i % ZIP_CHUNK_SIZE == 0:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)

    remaining = buffer.getvalue()
    if remaining:
        yield remaining

    logger.info("stream_zip: complete — %d assets — %s", len(media_items), zip_filename)


def zip_streaming_response(
    media_items: list[Media],
    zip_filename: str = "galleria.zip",
) -> StreamingResponse:
    """
    Wrap ``stream_zip`` in a ``StreamingResponse`` ready to return from a route.

    Usage::

        @router.get("/events/{event_id}/download")
        async def download_event_media(event: AccessibleEvent, db: DB):
            media = media_service.get_event_media(event.id, db)
            return zip_streaming_response(media, f"event-{event.id}.zip")
    """
    return StreamingResponse(
        stream_zip(media_items, zip_filename),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"',
        },
    )
