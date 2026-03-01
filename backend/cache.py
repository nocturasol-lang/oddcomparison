"""
Redis cache for odds data with in-memory fallback.
Keys: odds:current (30s TTL), odds:history:{game_id}, metrics:scrapers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from models import OddsEntry

log = logging.getLogger(__name__)

# Key structure
KEY_ODDS_CURRENT = "odds:current"
KEY_ODDS_HISTORY = "odds:history:{}"
KEY_METRICS_SCRAPERS = "metrics:scrapers"

ODDS_TTL_SECONDS = 30
RECONNECT_DELAY_SECONDS = 2
RECONNECT_ATTEMPTS = 5


def _serialize_odds(entries: list[OddsEntry]) -> str:
    """Serialize list of OddsEntry to JSON string."""
    data = [e.model_dump(mode="json") for e in entries]
    return json.dumps(data)


def _deserialize_odds(raw: str | bytes | None) -> list[OddsEntry]:
    """Deserialize JSON string to list[OddsEntry]. Returns [] if invalid or empty."""
    if raw is None or (isinstance(raw, str) and not raw.strip()) or raw == b"":
        return []
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("Cache: invalid JSON for odds: %s", e)
        return []
    if not isinstance(data, list):
        return []
    result: list[OddsEntry] = []
    for item in data:
        try:
            result.append(OddsEntry.model_validate(item))
        except Exception as e:
            log.warning("Cache: skip invalid OddsEntry: %s", e)
    return result


class OddsCache:
    """
    Async Redis cache for odds with reconnection and in-memory fallback.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        ttl_seconds: int = ODDS_TTL_SECONDS,
    ) -> None:
        self._url = url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._ttl = ttl_seconds
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None
        self._use_redis = True
        # In-memory fallback: same key semantics, no TTL for history/metrics
        self._memory: dict[str, tuple[Any, float | None]] = {}  # key -> (value, expiry_ts or None)
        self._memory_ttl = ttl_seconds

    async def connect(self) -> None:
        """Create connection pool and Redis client. Idempotent."""
        if self._pool is not None:
            return
        try:
            self._pool = ConnectionPool.from_url(
                self._url,
                max_connections=10,
                decode_responses=False,
            )
            self._client = Redis(connection_pool=self._pool)
            await self._client.ping()
            log.info("OddsCache: Redis connected at %s", self._url.split("@")[-1] if "@" in self._url else self._url)
        except (RedisConnectionError, RedisError, OSError) as e:
            log.warning("OddsCache: Redis unavailable (%s), using in-memory fallback", e)
            self._use_redis = False
            self._close_redis()

    def _close_redis(self) -> None:
        """Drop Redis client and pool references (async cleanup in close())."""
        self._client = None
        self._pool = None

    async def _ensure_client(self) -> bool:
        """Ensure Redis client is connected; on failure switch to memory. Returns True if Redis is usable."""
        if not self._use_redis:
            return False
        if self._client is None:
            await self.connect()
        if self._client is None or not self._use_redis:
            return False
        for attempt in range(RECONNECT_ATTEMPTS):
            try:
                await self._client.ping()
                return True
            except (RedisConnectionError, RedisError, OSError) as e:
                log.warning("OddsCache: ping failed (attempt %d/%d): %s", attempt + 1, RECONNECT_ATTEMPTS, e)
                if attempt < RECONNECT_ATTEMPTS - 1:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        log.warning("OddsCache: switching to in-memory fallback after reconnect failure")
        self._use_redis = False
        self._close_redis()
        return False

    # ---- Current odds (30s expiry) ----

    async def set_current_odds(self, entries: list[OddsEntry]) -> None:
        """Store current odds with 30-second expiry."""
        payload = _serialize_odds(entries)
        if await self._ensure_client() and self._client is not None:
            try:
                await self._client.setex(KEY_ODDS_CURRENT, self._ttl, payload)
                return
            except (RedisConnectionError, RedisError) as e:
                log.warning("OddsCache: set_current_odds failed: %s", e)
                self._use_redis = False
        # Fallback: in-memory with TTL
        self._memory[KEY_ODDS_CURRENT] = (payload, time.monotonic() + self._memory_ttl)

    async def get_current_odds(self) -> list[OddsEntry]:
        """Retrieve current odds; empty list if missing or expired."""
        if await self._ensure_client() and self._client is not None:
            try:
                raw = await self._client.get(KEY_ODDS_CURRENT)
                return _deserialize_odds(raw)
            except (RedisConnectionError, RedisError) as e:
                log.warning("OddsCache: get_current_odds failed: %s", e)
                self._use_redis = False
        # Fallback: in-memory
        entry = self._memory.get(KEY_ODDS_CURRENT)
        if entry is None:
            return []
        payload, expiry = entry
        if expiry is not None and time.monotonic() > expiry:
            del self._memory[KEY_ODDS_CURRENT]
            return []
        return _deserialize_odds(payload)

    # ---- History per game_id ----

    async def set_odds_history(self, game_id: str, entries: list[OddsEntry]) -> None:
        """Store history for a game (no TTL; caller can manage size)."""
        key = KEY_ODDS_HISTORY.format(game_id)
        payload = _serialize_odds(entries)
        if await self._ensure_client() and self._client is not None:
            try:
                await self._client.set(key, payload)
                return
            except (RedisConnectionError, RedisError) as e:
                log.warning("OddsCache: set_odds_history failed: %s", e)
                self._use_redis = False
        self._memory[key] = (payload, None)

    async def get_odds_history(self, game_id: str) -> list[OddsEntry]:
        """Retrieve history for a game."""
        key = KEY_ODDS_HISTORY.format(game_id)
        if await self._ensure_client() and self._client is not None:
            try:
                raw = await self._client.get(key)
                return _deserialize_odds(raw)
            except (RedisConnectionError, RedisError) as e:
                log.warning("OddsCache: get_odds_history failed: %s", e)
                self._use_redis = False
        entry = self._memory.get(key)
        if entry is None:
            return []
        return _deserialize_odds(entry[0])

    # ---- Scraper metrics ----

    async def set_scraper_metrics(self, metrics: dict[str, Any]) -> None:
        """Store scraper metrics (e.g. last run status, counts). JSON-serializable dict."""
        key = KEY_METRICS_SCRAPERS
        payload = json.dumps(metrics)
        if await self._ensure_client() and self._client is not None:
            try:
                await self._client.set(key, payload)
                return
            except (RedisConnectionError, RedisError) as e:
                log.warning("OddsCache: set_scraper_metrics failed: %s", e)
                self._use_redis = False
        self._memory[key] = (payload, None)

    async def get_scraper_metrics(self) -> dict[str, Any]:
        """Retrieve scraper metrics. Returns {} if missing or invalid."""
        key = KEY_METRICS_SCRAPERS
        if await self._ensure_client() and self._client is not None:
            try:
                raw = await self._client.get(key)
                if raw is None or raw == b"":
                    return {}
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                return json.loads(text)
            except (RedisConnectionError, RedisError, json.JSONDecodeError) as e:
                log.warning("OddsCache: get_scraper_metrics failed: %s", e)
                self._use_redis = False
        entry = self._memory.get(key)
        if entry is None:
            return {}
        try:
            return json.loads(entry[0])
        except json.JSONDecodeError:
            return {}

    async def close(self) -> None:
        """Close Redis connection pool and client. Safe to call if using fallback."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                log.debug("OddsCache: client aclose: %s", e)
        if self._pool is not None and hasattr(self._pool, "aclose"):
            try:
                await self._pool.aclose()
            except Exception as e:
                log.debug("OddsCache: pool aclose: %s", e)
        self._close_redis()
