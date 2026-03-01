"""
Orchestrates all scrapers, merges with laystars, and pushes deltas to WebSocket subscribers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .novibet import NovibetScraper
from .laystars import LaystarsScraper

log = logging.getLogger(__name__)


class OddsOrchestrator:
    """Runs bookmaker + laystars scrapers, merges results, broadcasts changes."""

    def __init__(self) -> None:
        self.scrapers: list = [NovibetScraper()]
        self.laystars = LaystarsScraper()

        from comparator import OddsComparator
        self.comparator = OddsComparator()

        self.current_odds: list = []
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self.subscribers: list[asyncio.Queue] = []

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
        log.info("Orchestrator started")

    async def _poll_loop(self) -> None:
        """Poll all sources every ~4s, merge, diff, push delta to subscribers."""
        while self._running:
            cycle_start = time.monotonic()

            bookmaker_tasks = [s.fetch() for s in self.scrapers]
            laystars_task = self.laystars.fetch()

            results: list = []
            gathered = await asyncio.gather(
                *bookmaker_tasks, laystars_task, return_exceptions=True,
            )

            laystars_result = None
            for r in gathered:
                if isinstance(r, Exception):
                    log.warning("Scraper error: %s", r)
                    continue
                if r.source == "laystars":
                    laystars_result = r
                else:
                    results.append(r)

            if laystars_result is None:
                from models import ScraperResult
                laystars_result = ScraperResult(
                    source="laystars", entries=[],
                    scraped_at=datetime.now(timezone.utc),
                    success=False, error="scraper_exception",
                )

            bookmaker_results = [r for r in results if r.success]
            merged = self.comparator.merge(bookmaker_results, laystars_result)
            delta = self.comparator.get_changes(self.current_odds, merged)
            sorted_entries = self.comparator.normalize_for_display(merged)

            self.current_odds = sorted_entries

            if delta.changed or delta.removed:
                for q in self.subscribers:
                    try:
                        q.put_nowait(delta)
                    except asyncio.QueueFull:
                        log.debug("Subscriber queue full, drop delta")

            elapsed = time.monotonic() - cycle_start
            value_count = sum(1 for e in self.current_odds if e.is_value)
            log.info(
                "Cycle: %.0fms | entries=%d | value=%d | laystars=%d",
                elapsed * 1000, len(self.current_odds), value_count,
                len(laystars_result.entries),
            )

            sleep_for = max(0, 4.0 - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def get_current(self) -> list:
        return list(self.current_odds)
