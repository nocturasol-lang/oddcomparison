"""
FastAPI app: REST + WebSocket odds API, health, CORS.
Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from orchestrator import EVICT_SENTINEL, OddsOrchestrator
from models import OddsEntry, OddsDelta
from logging_config import setup_logging, get_access_logger
from metrics import get_collector

log = logging.getLogger(__name__)

# Global orchestrator (set in lifespan)
orchestrator: OddsOrchestrator | None = None
_start_time: float = 0.0

# API rate limit: max requests per window per IP (override via RATE_LIMIT_* env)
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
RATE_LIMIT_REQUESTS = _int_env("RATE_LIMIT_REQUESTS", 100)
RATE_LIMIT_WINDOW_SEC = _int_env("RATE_LIMIT_WINDOW_SEC", 60)


def _memory_mb() -> float:
    """Current process RSS in MB."""
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except ImportError:
        try:
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)  # Linux KB
        except (ImportError, OSError):
            return 0.0


def _file_descriptor_count() -> int | None:
    """Open file descriptor count for current process; None if unavailable (e.g. Windows)."""
    try:
        import psutil
        p = psutil.Process()
        if hasattr(p, "num_fds"):
            return p.num_fds()
        return None
    except (ImportError, AttributeError):
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory per-IP rate limit: RATE_LIMIT_REQUESTS per RATE_LIMIT_WINDOW_SEC."""

    def __init__(self, app):
        super().__init__(app)
        self._requests = RATE_LIMIT_REQUESTS
        self._window_sec = RATE_LIMIT_WINDOW_SEC
        self._by_ip: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "0.0.0.0"
        now = time.monotonic()
        cutoff = now - self._window_sec
        if ip not in self._by_ip:
            self._by_ip[ip] = []
        self._by_ip[ip] = [t for t in self._by_ip[ip] if t > cutoff]
        if len(self._by_ip[ip]) >= self._requests:
            from starlette.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "rate_limit_exceeded", "retry_after": int(self._window_sec)},
                headers={"Retry-After": str(int(self._window_sec))},
            )
        self._by_ip[ip].append(now)
        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log each API request to oddshawk.access logger."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        client = request.client.host if request.client else "-"
        get_access_logger().info(
            "%s %s %s %.2fms %s",
            request.method,
            request.url.path,
            client,
            elapsed_ms,
            response.status_code,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, _start_time
    setup_logging()
    _start_time = time.monotonic()
    orchestrator = OddsOrchestrator()
    collector = get_collector()
    collector.set_live_getters(
        get_uptime=lambda: time.monotonic() - _start_time,
        get_subscriber_count=lambda: len(orchestrator._subscribers),
    )
    try:
        await orchestrator.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Orchestrator start failed (app still runs): %s", e)
    yield
    try:
        await get_collector().close_redis()
    except Exception:
        pass
    orchestrator._running = False
    for task_name, task in [("_poll_task", orchestrator._poll_task), ("_heartbeat_task", orchestrator._heartbeat_task)]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ODDSHAWK API", lifespan=lifespan)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/odds")
async def get_odds():
    """Return current merged odds snapshot."""
    if orchestrator is None:
        return {"odds": [], "count": 0}
    odds = orchestrator.get_current()
    # Pydantic models serialize in response
    return {"odds": odds, "count": len(odds)}


# WebSocket message rate limit per client (messages per second)
WS_MESSAGES_PER_SECOND = 10


class WebSocketRateLimiter:
    """Allow at most N messages per second per client (sliding window)."""

    __slots__ = ("timestamps", "max_per_sec")

    def __init__(self, max_per_sec: int = WS_MESSAGES_PER_SECOND) -> None:
        self.timestamps: list[float] = []
        self.max_per_sec = max_per_sec

    async def wait_if_needed(self) -> None:
        """If we've already sent max_per_sec in the last 1s, sleep until oldest expires."""
        now = time.monotonic()
        cutoff = now - 1.0
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= self.max_per_sec:
            wait = self.timestamps[0] + 1.0 - now
            if wait > 0:
                await asyncio.sleep(wait)
            now = time.monotonic()
            self.timestamps = [t for t in self.timestamps if t > now - 1.0]
        self.timestamps.append(now)


@app.websocket("/ws/odds")
async def ws_odds(websocket: WebSocket):
    """Subscribe to live odds: full state on connect, then deltas."""
    await websocket.accept()

    if orchestrator is None:
        await websocket.send_json({"type": "error", "message": "orchestrator not ready"})
        await websocket.close()
        return

    queue = orchestrator.subscribe()
    if queue is None:
        await websocket.send_json({"type": "error", "message": "subscriber limit reached"})
        await websocket.close()
        return

    ws_limiter = WebSocketRateLimiter(max_per_sec=WS_MESSAGES_PER_SECOND)

    try:
        # Send full state immediately (not rate-limited)
        current = orchestrator.get_current()
        payload = {
            "type": "full",
            "odds": [e.model_dump(mode="json") for e in current],
        }
        await websocket.send_json(payload)

        # Loop: wait for delta from queue, send as JSON (rate-limited to 10/sec)
        while True:
            delta = await queue.get()
            if delta is EVICT_SENTINEL:
                break
            await ws_limiter.wait_if_needed()
            await websocket.send_json(
                {
                    "type": "delta",
                    "changed": [e.model_dump(mode="json") for e in delta.changed],
                    "removed": delta.removed,
                    "timestamp": delta.timestamp.isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        orchestrator.unsubscribe(queue)


@app.get("/api/test")
async def test():
    """Simple test endpoint."""
    return {"status": "ok"}


@app.get("/api/health")
async def health():
    """Health check with odds count and uptime."""
    if orchestrator is None:
        return {"status": "starting", "odds_count": 0, "uptime": 0.0}
    uptime = time.monotonic() - _start_time
    count = len(orchestrator.get_current())
    return {"status": "ok", "odds_count": count, "uptime": round(uptime, 2)}


async def _detailed_health_checks() -> dict:
    """Run Redis ping, scraper status, memory, FDs, WebSocket count; add recovery suggestions."""
    uptime = time.monotonic() - _start_time
    suggestions: list[str] = []
    overall_ok = True

    # Redis
    redis_ok = False
    redis_message = "not checked"
    try:
        collector = get_collector()
        redis_ok, redis_message = await collector.check_redis_connection()
        if not redis_ok:
            overall_ok = False
            suggestions.append("Redis unreachable: verify REDIS_URL and that Redis is running (e.g. redis-cli ping).")
    except Exception as e:
        redis_message = str(e)
        suggestions.append("Redis check failed: ensure redis package is installed and REDIS_URL is set.")

    # Scraper status (from metrics)
    scraper_status: dict = {}
    try:
        snapshot = get_collector().get_snapshot()
        scrapers = snapshot.get("scrapers", {})
        for name, data in scrapers.items():
            successes = data.get("successes", 0)
            failures = data.get("failures", 0)
            total = successes + failures
            last_ok = total > 0 and data.get("success_rate_pct", 0) >= 50
            scraper_status[name] = {
                "successes": successes,
                "failures": failures,
                "success_rate_pct": data.get("success_rate_pct", 0),
                "last_response_time_ms": data.get("last_response_time_ms", 0),
                "last_run_ok": last_ok,
            }
            if total > 0 and data.get("success_rate_pct", 100) < 50:
                overall_ok = False
                if "laystars" in name.lower():
                    suggestions.append("Laystars scraper failing often: check LAYSTARS_COOKIES in config and session validity.")
                else:
                    suggestions.append(f"Scraper '{name}' has low success rate: check network and source availability.")
    except Exception as e:
        log.debug("Scraper status from metrics: %s", e)
        suggestions.append("Could not read scraper metrics; orchestrator may still be starting.")

    # Memory
    memory_mb = _memory_mb()
    if memory_mb > 1024:
        suggestions.append("High memory usage (>1GB): consider restarting the process or checking for leaks.")

    # File descriptors
    fd_count = _file_descriptor_count()

    # WebSocket subscriber count
    ws_count = 0
    if orchestrator is not None:
        ws_count = len(orchestrator._subscribers)

    return {
        "status": "ok" if overall_ok else "degraded",
        "uptime_sec": round(uptime, 2),
        "redis": {"ok": redis_ok, "message": redis_message},
        "scrapers": scraper_status,
        "memory_mb": memory_mb,
        "file_descriptors": fd_count,
        "websocket_connections": ws_count,
        "suggestions": suggestions,
    }


@app.get("/api/health/detailed")
async def health_detailed():
    """Detailed health: Redis ping, scraper status, memory, FDs, WebSocket count, and recovery suggestions."""
    if orchestrator is None:
        return {
            "status": "starting",
            "uptime_sec": round(time.monotonic() - _start_time, 2),
            "redis": {"ok": False, "message": "orchestrator not ready"},
            "scrapers": {},
            "memory_mb": _memory_mb(),
            "file_descriptors": _file_descriptor_count(),
            "websocket_connections": 0,
            "suggestions": ["Orchestrator still starting; wait and retry /api/health/detailed."],
        }
    return await _detailed_health_checks()


@app.get("/api/metrics")
async def metrics():
    """Return metrics: scraper success/response times, arbitrage count, subscribers, memory, uptime. From memory; Redis used for retention."""
    if orchestrator is None:
        return {"error": "orchestrator not ready"}
    collector = get_collector()
    snapshot = collector.get_snapshot()
    return snapshot
