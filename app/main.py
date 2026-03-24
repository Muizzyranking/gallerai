from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.core.schemas import (
    ApiErrorResponse,
    BadRequestResponse,
    NotFoundResponse,
    ValidationErrorDetail,
)
from app.db import Collections, close_mongo_client, close_redis, get_mongo_db

setup_logging(env=settings.app_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_mongo_db()
    await db[Collections.FACE_EMBEDDINGS].create_index("event_id")
    await db[Collections.FACE_EMBEDDINGS].create_index("photo_id")
    await db[Collections.FACE_EMBEDDINGS].create_index(
        [("event_id", 1), ("photo_id", 1)]
    )
    yield

    await close_mongo_client()
    await close_redis()


app = FastAPI(
    title=settings.app_name,
    description=settings.description,
    version="0.1.0",
    lifespan=lifespan,
    responses={
        422: {
            "description": "Validation Error",
            "model": ApiErrorResponse[list[ValidationErrorDetail]],
        },
        404: {
            "description": "Not Found",
            "model": NotFoundResponse,
        },
        400: {
            "description": "Bad Request",
            "model": BadRequestResponse,
        },
        500: {
            "description": "Internal Server Error",
            "model": ApiErrorResponse,
        },
    },
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok", "env": settings.debug and "dev" or "production"}
