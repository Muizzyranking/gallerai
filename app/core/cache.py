import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.utils import to_seconds
from app.db import get_redis

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self, namespace: str, ttl: int | None = None) -> None:
        self.namespace: str = namespace
        self._ttl: int | None = ttl

        self._cached_version: int | None = None
        self._version_expiry: float = 0
        self._version_refresh_interval: int = 10

    @property
    def ttl(self) -> int:
        """Effective TTL — instance value or settings fallback."""
        if self._ttl is not None:
            return self._ttl
        return settings.default_cache_ttl

    async def _get_version(self) -> int:
        """Fetch version from Redis but cache it locally for 10 seconds."""
        now = time.monotonic()
        if self._cached_version is not None and now < self._version_expiry:
            return self._cached_version

        try:
            redis = await get_redis()
            v = await redis.get(f"{self.namespace}:__version__")
            self._cached_version = int(v) if v else 1
            self._version_expiry = now + self._version_refresh_interval
            return self._cached_version
        except Exception as e:
            logger.error(f"Failed to fetch cache version for {self.namespace}: {e}")
            return self._cached_version or 1

    async def _key(self, key: str) -> str:
        """Build a versioned, namespaced Redis key."""
        version = await self._get_version()
        return f"{self.namespace}:v{version}:{key}"

    def _serialize(self, data: Any) -> str:
        try:
            return json.dumps(data, default=str)
        except Exception as e:
            logger.error(f"Failed to serialize cache data for {self.namespace}: {e}")
            raise

    def _deserialize(self, raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Failed to deserialize cache data for {self.namespace}: {e}")
            raise

    async def get(self, key: str) -> Any | None:
        """
        Return cached value for key, or None if not found or expired.
        Cache misses are silent — never raises.
        """
        try:
            redis = await get_redis()
            raw = await redis.get(await self._key(key))
            return self._deserialize(raw) if raw else None
        except Exception as e:
            logger.warning(
                f"Cache get failed — namespace={self.namespace} key={key}: {e}"
            )
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in cache."""
        effective_ttl = ttl if ttl is not None else self.ttl
        try:
            redis = await get_redis()
            await redis.setex(
                await self._key(key), effective_ttl, self._serialize(value)
            )
            logger.debug(
                f"Cache set — namespace={self.namespace} key={key} ttl={effective_ttl}s"
            )
        except Exception as e:
            logger.warning(
                f"Cache set failed — namespace={self.namespace} key={key}: {e}"
            )

    async def invalidate(self, key: str) -> None:
        """Delete a single cache entry by key."""
        try:
            redis = await get_redis()
            await redis.delete(await self._key(key))
            logger.debug(f"Cache invalidated — namespace={self.namespace} key={key}")
        except Exception as e:
            logger.warning(
                f"Cache invalidate failed — namespace={self.namespace} key={key}: {e}"
            )

    async def invalidate_namespace(self) -> None:
        """
        Invalidate all entries in this namespace by bumping the version counter.

        All existing keys become unreachable instantly.
        Old keys will expire naturally via their TTLs.
        """
        self._cached_version = None
        self._version_expiry = 0

        try:
            redis = await get_redis()
            version_key = f"{self.namespace}:__version__"
            new_version = await redis.incr(version_key)
            logger.debug(
                f"Cache namespace invalidated — namespace={self.namespace} new_version={new_version}"
            )
        except Exception as e:
            logger.warning(
                f"Cache invalidate_namespace failed — namespace={self.namespace}: {e}"
            )

    async def invalidate_pattern(self, pattern: str) -> None:
        """
        Delete all cache entries in this namespace whose key matches a glob pattern.
        """
        try:
            redis = await get_redis()
            version = await self._get_version()
            full_pattern = f"{self.namespace}:v{version}:{pattern}"

            cursor = 0
            deleted = 0
            while True:
                cursor, keys = await redis.scan(cursor, match=full_pattern, count=100)
                if keys:
                    await redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            logger.debug(
                f"Cache pattern invalidated — namespace={self.namespace} "
                f"pattern={pattern} deleted={deleted}"
            )
        except Exception as e:
            logger.warning(
                f"Cache invalidate_pattern failed — namespace={self.namespace} pattern={pattern}: {e}"
            )

    async def get_or_set(
        self,
        key: str,
        fetch_func: Callable[[], Awaitable[Any]],
        ttl: int | None = None,
        lock_timeout: int = 20,
    ) -> Any:
        """
        Fetch from cache, or use a distributed Redis lock to fetch from source.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached

        redis = await get_redis()
        lock_name = f"lock:{self.namespace}:{key}"

        lock = redis.lock(lock_name, timeout=lock_timeout, sleep=0.1)

        try:
            async with lock:
                cached = await self.get(key)
                if cached is not None:
                    return cached

                data = await fetch_func()
                await self.set(key, data, ttl)
                return data
        except Exception as e:
            logger.error(f"Distributed lock error for {key}: {e}")
            return await fetch_func()


event_cache = Cache(namespace="events", ttl=to_seconds(minutes=5))
event_list_cache = Cache(namespace="event_list", ttl=to_seconds(minutes=2))
gallery_cache = Cache(namespace="gallery", ttl=to_seconds(minutes=1))
