"""
Tracks scraper success/response times, arbitrage opportunities, WebSocket subscribers,
memory and uptime. Persists to Redis with 1-hour retention; exposes via /api/metrics.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

KEY_METRICS = "metrics:current"
METRICS_TTL_SEC = 3600  # 1 hour


def _memory_mb() -> float:
    """Current process RSS in MB. Uses psutil if available, else 0."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux KB
        except (ImportError, OSError):
            return 0.0


class MetricsCollector:
    """
    In-memory metrics plus optional Redis persistence (1h TTL).
    """

    def __init__(self) -> None:
        # Per-scraper: successes, failures, last response time (ms)
        self._scrapers: dict[str, dict[str, Any]] = {}
        self._arbitrage_count = 0
        self._subscriber_count = 0
        self._get_uptime: Callable[[], float] | None = None
        self._get_subscriber_count: Callable[[], int] | None = None
        self._redis: Any = None
        self._redis_available = True

    def set_live_getters(
        self,
        *,
        get_uptime: Callable[[], float] | None = None,
        get_subscriber_count: Callable[[], int] | None = None,
    ) -> None:
        """Set callables for live uptime and subscriber count (called when building snapshot)."""
        if get_uptime is not None:
            self._get_uptime = get_uptime
        if get_subscriber_count is not None:
            self._get_subscriber_count = get_subscriber_count

    def record_scraper_run(self, source: str, success: bool, response_time_ms: float) -> None:
        """Record one scraper run (success/failure and duration in ms)."""
        if source not in self._scrapers:
            self._scrapers[source] = {"successes": 0, "failures": 0, "last_response_time_ms": 0.0}
        s = self._scrapers[source]
        if success:
            s["successes"] = s.get("successes", 0) + 1
        else:
            s["failures"] = s.get("failures", 0) + 1
        s["last_response_time_ms"] = round(response_time_ms, 2)

    def record_cycle(
        self,
        scraper_results: list[tuple[str, bool, float]],
        arbitrage_count: int,
        subscriber_count: int,
    ) -> None:
        """
        Record one poll cycle: per-scraper (name, success, response_time_ms),
        current arbitrage (value) count, and WebSocket subscriber count.
        """
        for source, success, response_time_ms in scraper_results:
            self.record_scraper_run(source, success, response_time_ms)
        self._arbitrage_count = arbitrage_count
        self._subscriber_count = subscriber_count

    def get_snapshot(self) -> dict[str, Any]:
        """Build full metrics snapshot (includes live memory and uptime if getters set)."""
        uptime_sec = self._get_uptime() if self._get_uptime else 0.0
        subscriber_count = self._get_subscriber_count() if self._get_subscriber_count else self._subscriber_count

        scrapers_out: dict[str, dict[str, Any]] = {}
        for name, data in self._scrapers.items():
            total = data.get("successes", 0) + data.get("failures", 0)
            rate = (data["successes"] / total * 100) if total else 0.0
            scrapers_out[name] = {
                "successes": data.get("successes", 0),
                "failures": data.get("failures", 0),
                "success_rate_pct": round(rate, 1),
                "last_response_time_ms": data.get("last_response_time_ms", 0),
            }

        return {
            "scrapers": scrapers_out,
            "arbitrage_opportunities": self._arbitrage_count,
            "websocket_subscribers": subscriber_count,
            "memory_mb": round(_memory_mb(), 2),
            "uptime_sec": round(uptime_sec, 2),
            "timestamp": time.time(),
        }

    async def _get_redis(self):
        """Lazy Redis client (shared connection for metrics writes)."""
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis
            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            self._redis = Redis.from_url(url, decode_responses=True)
            await self._redis.ping()
            return self._redis
        except Exception as e:
            log.debug("Metrics: Redis unavailable: %s", e)
            self._redis_available = False
            return None

    async def check_redis_connection(self) -> tuple[bool, str]:
        """Ping Redis; return (True, 'ok') or (False, error_message)."""
        try:
            client = await self._get_redis()
            if client is None:
                return (False, "Redis not configured or connection failed")
            await client.ping()
            return (True, "ok")
        except Exception as e:
            return (False, str(e))

    async def write_to_redis(self, snapshot: dict[str, Any] | None = None) -> None:
        """Store current snapshot in Redis with 1-hour TTL."""
        if not self._redis_available:
            return
        try:
            client = await self._get_redis()
            if client is None:
                return
            data = snapshot or self.get_snapshot()
            payload = json.dumps(data, default=str)
            await client.setex(KEY_METRICS, METRICS_TTL_SEC, payload)
        except Exception as e:
            log.debug("Metrics: Redis write failed: %s", e)
            self._redis_available = False

    async def read_from_redis(self) -> dict[str, Any] | None:
        """Load last stored snapshot from Redis (for fallback or secondary readers)."""
        try:
            client = await self._get_redis()
            if client is None:
                return None
            raw = await client.get(KEY_METRICS)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            log.debug("Metrics: Redis read failed: %s", e)
            return None

    async def close_redis(self) -> None:
        """Close Redis connection if opened."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


# Global collector (set by main / orchestrator)
_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    """Return the global metrics collector (creates one if not set)."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
