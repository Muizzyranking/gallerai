from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastlimit import FastLimit

from app.api.router import router
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
from app.db import close_redis

setup_logging(env=settings.app_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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


def get_user(request: Request):
    return request.state.user_id if hasattr(request.state, "user_id") else None


limiter = FastLimit(redis_url=settings.redis_url, user_id_func=get_user)
limiter.init_app(app)

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
