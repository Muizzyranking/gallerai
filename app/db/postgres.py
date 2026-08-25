import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import ColumnElement, DateTime, create_engine, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.core.config import settings

sync_engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_engine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,
    echo=False,
)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


def generate_uuid():
    return str(uuid.uuid4())


class BaseModel(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=generate_uuid
    )


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def get_db():
    """
    DB Dependency, yeilds DB
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


T = TypeVar("T")


async def fetch_one[T](
    db: AsyncSession, model: type[T], *where: ColumnElement
) -> T | None:
    result = await db.execute(select(model).where(*where))
    return result.scalar_one_or_none()


async def fetch_all[T](
    db: AsyncSession, model: type[T], *where: ColumnElement
) -> Sequence[T]:
    result = await db.execute(select(model).where(*where))
    return result.scalars().all()
