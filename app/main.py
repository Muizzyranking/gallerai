from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db import Collections, close_mongo_client, get_mongo_db


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


app = FastAPI(
    title=settings.app_name,
    description=settings.description,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "env": settings.debug and "dev" or "production"}
