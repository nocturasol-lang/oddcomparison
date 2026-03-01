"""
Orchestrates all scrapers, merges with laystars, and pushes deltas to WebSocket subscribers.
Auto-recovery: circuit breaker per scraper, restart on half-open, webhook alerts, graceful degradation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from scraper.novibet import NovibetScraper
from scraper.laystars import LaystarsScraper

log = logging.getLogger(__name__)

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Circuit breaker: open after this many consecutive failures
CIRCUIT_FAILURE_THRESHOLD = _int_env("CIRCUIT_FAILURE_THRESHOLD", 3)
# Keep circuit open for this long before trying again (half-open)
CIRCUIT_OPEN_SEC = _float_env("CIRCUIT_OPEN_SEC", 60.0)
# Webhook URL for recovery/failure alerts (optional)
RECOVERY_WEBHOOK_URL = os.environ.get("RECOVERY_WEBHOOK_URL", "").strip()
RECOVERY_WEBHOOK_TIMEOUT_SEC = 10


async def _run_scraper_with_timing(name: str, coro) -> tuple[str, Any, bool, float]:
    """Run a scraper coroutine and return (name, result, success, response_time_ms)."""
    start = time.monotonic()
    try:
        r = await coro
        elapsed_ms = (time.monotonic() - start) * 1000
        success = getattr(r, "success", False) if r is not None else False
        return (name, r, success, elapsed_ms)
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        return (name, None, False, elapsed_ms)

# Sentinel: when put into a subscriber queue, consumer should disconnect (stale eviction).
EVICT_SENTINEL: Any = None

HEARTBEAT_INTERVAL_SEC = 60
STALE_CONSUMER_SEC = 30
MAX_SUBSCRIBERS = 1000

# Circuit states
CIRCUIT_CLOSED = "closed"
CIRCUIT_OPEN = "open"
CIRCUIT_HALF_OPEN = "half_open"


async def _send_recovery_webhook(event: str, scraper_name: str, message: str, recovered: bool = True) -> None:
    """POST alert to RECOVERY_WEBHOOK_URL. Fire-and-forget; log and ignore errors."""
    if not RECOVERY_WEBHOOK_URL:
        return
    payload = {
        "event": event,
        "scraper": scraper_name,
        "message": message,
        "recovered": recovered,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RECOVERY_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=RECOVERY_WEBHOOK_TIMEOUT_SEC),
            ) as resp:
                if resp.status >= 400:
                    log.warning("Recovery webhook returned %s for %s", resp.status, event)
    except Exception as e:
        log.warning("Recovery webhook failed: %s", e)


class SubscriberWrapper:
    """Queue wrapper that tracks last_sent (orchestrator) and last_consumed (consumer)."""

    __slots__ = ("_queue", "last_sent", "last_consumed")

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.last_sent: float = 0.0
        self.last_consumed: float = 0.0

    def put_nowait(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
            self.last_sent = time.monotonic()
        except asyncio.QueueFull:
            raise

    async def get(self) -> Any:
        result = await self._queue.get()
        self.last_consumed = time.monotonic()
        return result


class OddsOrchestrator:
    """Runs bookmaker + laystars scrapers, merges results, broadcasts changes. Circuit breaker per scraper."""

    def __init__(self) -> None:
        self.scrapers: list = [NovibetScraper()]
        self.laystars = LaystarsScraper()

        from comparator import OddsComparator
        self.comparator = OddsComparator()

        self.current_odds: list = []
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._subscribers: list[SubscriberWrapper] = []
        self._circuit: dict[str, dict[str, Any]] = {}

    def _circuit_state(self, name: str) -> dict[str, Any]:
        if name not in self._circuit:
            self._circuit[name] = {
                "consecutive_failures": 0,
                "state": CIRCUIT_CLOSED,
                "opened_at": 0.0,
                "last_success_at": 0.0,
            }
        return self._circuit[name]

    def _should_run_scraper(self, name: str) -> tuple[bool, bool]:
        """Return (should_run, should_restart)."""
        state = self._circuit_state(name)
        s = state["state"]
        now = time.monotonic()
        if s == CIRCUIT_CLOSED:
            return (True, False)
        if s == CIRCUIT_HALF_OPEN:
            return (True, False)
        if now - state["opened_at"] < CIRCUIT_OPEN_SEC:
            return (False, False)
        state["state"] = CIRCUIT_HALF_OPEN
        log.info("Circuit half-open for %s, will attempt restart and fetch", name)
        return (True, True)

    async def _restart_scraper(self, scraper: Any, name: str) -> None:
        if hasattr(scraper, "cleanup"):
            try:
                await scraper.cleanup()
            except Exception as e:
                log.warning("Scraper %s cleanup failed: %s", name, e)
        if hasattr(scraper, "initialize"):
            try:
                await scraper.initialize()
                log.info("Scraper %s re-initialized for recovery", name)
            except Exception as e:
                log.warning("Scraper %s initialize failed: %s", name, e)

    def _record_success(self, name: str) -> None:
        state = self._circuit_state(name)
        was_half_open = state["state"] == CIRCUIT_HALF_OPEN
        state["consecutive_failures"] = 0
        state["state"] = CIRCUIT_CLOSED
        state["last_success_at"] = time.monotonic()
        if was_half_open and RECOVERY_WEBHOOK_URL:
            asyncio.create_task(_send_recovery_webhook(
                "scraper_recovery", name, f"Scraper {name} recovered after circuit was open.", recovered=True,
            ))

    def _record_failure(self, name: str) -> None:
        state = self._circuit_state(name)
        state["consecutive_failures"] = state["consecutive_failures"] + 1
        if state["consecutive_failures"] >= CIRCUIT_FAILURE_THRESHOLD:
            state["state"] = CIRCUIT_OPEN
            state["opened_at"] = time.monotonic()
            log.warning("Circuit open for %s after %d consecutive failures", name, state["consecutive_failures"])
            if RECOVERY_WEBHOOK_URL:
                asyncio.create_task(_send_recovery_webhook(
                    "scraper_circuit_open", name,
                    f"Scraper {name} circuit opened after {state['consecutive_failures']} failures.",
                    recovered=False,
                ))
        elif state["state"] == CIRCUIT_HALF_OPEN:
            state["state"] = CIRCUIT_OPEN
            state["opened_at"] = time.monotonic()
            log.warning("Scraper %s failed in half-open, circuit open again", name)

    async def start(self) -> None:
        """Initialize scrapers, wire cookies, start poll loop."""
        for s in self.scrapers:
            if hasattr(s, "initialize"):
                await s.initialize()

        try:
            from config import LAYSTARS_COOKIES
            if LAYSTARS_COOKIES:
                await self.laystars.set_cookies(LAYSTARS_COOKIES)
                log.info("Laystars cookies loaded (%d chars)", len(LAYSTARS_COOKIES))
            else:
                log.warning("LAYSTARS_COOKIES is empty — Laystars scraper will return no data")
        except ImportError:
            log.warning("config.py not found — Laystars scraper will return no data")

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        log.info("Orchestrator started")

    async def _poll_loop(self) -> None:
        """Poll all sources every ~4s; circuit breaker skips failed scrapers; merge, diff, push delta."""
        from models import ScraperResult

        scrapers_list = list(self.scrapers) + [self.laystars]
        scraper_names = [getattr(s, "source", type(s).__name__) for s in self.scrapers] + ["laystars"]

        while self._running:
            cycle_start = time.monotonic()

            run_info: list[tuple[str, Any, bool, bool]] = []
            for name, scraper in zip(scraper_names, scrapers_list):
                should_run, should_restart = self._should_run_scraper(name)
                run_info.append((name, scraper, should_run, should_restart))

            for name, scraper, should_run, should_restart in run_info:
                if should_run and should_restart:
                    await self._restart_scraper(scraper, name)

            tasks: list = []
            task_names: list[str] = []
            for name, scraper, should_run, _ in run_info:
                if should_run:
                    tasks.append(_run_scraper_with_timing(name, scraper.fetch()))
                    task_names.append(name)

            gathered = await asyncio.gather(*tasks) if tasks else []
            g_idx = 0
            results_ordered: list[tuple[str, Any, bool, float]] = []
            skipped_names = {name for name, _, should_run, _ in run_info if not should_run}
            for name, scraper, should_run, _ in run_info:
                if should_run:
                    results_ordered.append(gathered[g_idx])
                    g_idx += 1
                else:
                    log.debug("Scraper %s skipped (circuit open), graceful degradation", name)
                    results_ordered.append((name, None, False, 0.0))

            results = []
            laystars_result = None
            scraper_metrics: list[tuple[str, bool, float]] = []
            for name, r, success, response_time_ms in results_ordered:
                scraper_metrics.append((name, success, response_time_ms))
                if name not in skipped_names:
                    if success:
                        self._record_success(name)
                    else:
                        self._record_failure(name)
                if r is None:
                    if name not in skipped_names:
                        log.warning("Scraper %s failed (exception or no result)", name)
                    continue
                if r.source == "laystars":
                    laystars_result = r
                else:
                    results.append(r)

            if laystars_result is None:
                laystars_result = ScraperResult(
                    source="laystars", entries=[],
                    scraped_at=datetime.now(timezone.utc),
                    success=False, error="scraper_exception",
                )

            for r in results:
                if not r.success:
                    log.warning("Scraper %s success=False: %s (entries=%d)", r.source, r.error, len(r.entries))

            bookmaker_results = [r for r in results if len(r.entries) > 0]
            merged = self.comparator.merge(bookmaker_results, laystars_result)
            delta = self.comparator.get_changes(self.current_odds, merged)
            sorted_entries = self.comparator.normalize_for_display(merged)

            self.current_odds = sorted_entries

            if delta.changed or delta.removed:
                for w in self._subscribers:
                    try:
                        w.put_nowait(delta)
                    except asyncio.QueueFull:
                        log.debug("Subscriber queue full, drop delta")

            elapsed = time.monotonic() - cycle_start
            value_count = sum(1 for e in self.current_odds if e.is_value)
            try:
                from metrics import get_collector
                collector = get_collector()
                collector.record_cycle(scraper_metrics, value_count, len(self._subscribers))
                await collector.write_to_redis()
            except Exception as m_err:
                log.debug("Metrics record failed: %s", m_err)

            log.info(
                "Cycle: %.0fms | entries=%d | value=%d | laystars=%d",
                elapsed * 1000, len(self.current_odds), value_count,
                len(laystars_result.entries),
            )

            sleep_for = max(0, 4.0 - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def _heartbeat_loop(self) -> None:
        """Every 60s remove subscribers that haven't consumed in 30s; log count changes."""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
            if not self._running:
                break
            now = time.monotonic()
            removed = 0
            for w in list(self._subscribers):
                if w.last_consumed > 0 and (now - w.last_consumed) > STALE_CONSUMER_SEC:
                    try:
                        w.put_nowait(EVICT_SENTINEL)
                    except asyncio.QueueFull:
                        pass
                    self._subscribers.remove(w)
                    removed += 1
            if removed:
                log.info(
                    "Subscriber heartbeat: removed %d stale (no consume in %ds), count=%d",
                    removed, STALE_CONSUMER_SEC, len(self._subscribers),
                )

    def subscribe(self) -> SubscriberWrapper | None:
        """Subscribe to odds deltas. Returns None if at max subscriber limit."""
        if len(self._subscribers) >= MAX_SUBSCRIBERS:
            log.warning("Subscriber limit reached (%d), rejecting new connection", MAX_SUBSCRIBERS)
            return None
        w = SubscriberWrapper()
        self._subscribers.append(w)
        log.info("Subscriber connected, count=%d", len(self._subscribers))
        return w

    def unsubscribe(self, subscriber: SubscriberWrapper) -> None:
        """Remove a subscriber (e.g. on WebSocket disconnect)."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
            log.info("Subscriber disconnected, count=%d", len(self._subscribers))

    def get_current(self) -> list:
        return list(self.current_odds)
