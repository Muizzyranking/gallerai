import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self, namespace: str, ttl: int | None = None) -> None:
        self.namespace: str = namespace
        self._ttl: int | None = ttl

    @property
    def ttl(self) -> int:
        """Effective TTL — instance value or settings fallback."""
        if self._ttl is not None:
            return self._ttl
        from app.core.config import settings

        return settings.default_cache_ttl

    def _key(self, key: str) -> str:
        """Build a namespaced Redis key."""
        return f"{self.namespace}:{key}"

    async def get(self, key: str) -> Any | None:
        """
        Return cached value for key or None if not found or expired.
        Cache misses are silent — never raises.
        """
        from app.db.redis import get_redis

        try:
            redis = await get_redis()
            raw = await redis.get(self._key(key))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(
                f"Cache get failed — namespace={self.namespace} key={key}: {e}"
            )
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        Store a value in cache.
        """
        from app.db.redis import get_redis

        effective_ttl = ttl if ttl is not None else self.ttl
        try:
            redis = await get_redis()
            await redis.setex(self._key(key), effective_ttl, json.dumps(value))
            logger.debug(
                f"Cache set — namespace={self.namespace} key={key} ttl={effective_ttl}s"
            )
        except Exception as e:
            logger.warning(
                f"Cache set failed — namespace={self.namespace} key={key}: {e}"
            )

    async def invalidate(self, key: str) -> None:
        """Delete a single cache entry by key."""
        from app.db.redis import get_redis

        try:
            redis = await get_redis()
            await redis.delete(self._key(key))
            logger.debug(f"Cache invalidated — namespace={self.namespace} key={key}")
        except Exception as e:
            logger.warning(
                f"Cache invalidate failed — namespace={self.namespace} key={key}: {e}"
            )

    async def invalidate_prefix(self, prefix: str) -> None:
        """
        Delete all cache entries whose key starts with the given prefix
        within this namespace.
        """
        from app.db.redis import get_redis

        full_prefix = self._key(prefix)
        try:
            redis = await get_redis()
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match=f"{full_prefix}*", count=100
                )
                if keys:
                    await redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            logger.debug(
                f"Cache prefix invalidated — namespace={self.namespace} "
                f"prefix={prefix} deleted={deleted}"
            )
        except Exception as e:
            logger.warning(
                f"Cache invalidate_prefix failed — namespace={self.namespace} prefix={prefix}: {e}"
            )


event_cache = Cache(namespace="events", ttl=300)  # 5 min — event details
event_list_cache = Cache(namespace="event_list", ttl=120)  # 2 min — user event lists
gallery_cache = Cache(namespace="gallery", ttl=60)  # 1 min — gallery pages
