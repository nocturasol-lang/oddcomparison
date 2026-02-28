"""
Orchestrates all scrapers, merges with laystars, and pushes deltas to WebSocket subscribers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .novibet import NovibetScraper
# from .laystars import LaystarsScraper  # disabled for localhost test without Laystars

if TYPE_CHECKING:
    from ..comparator import OddsComparator
    from ..models import OddsEntry, OddsDelta, ScraperResult

log = logging.getLogger(__name__)


def _mock_laystars_result():
    """Mock Laystars result (empty) for localhost test without Laystars scraper."""
    from ..models import ScraperResult
    return ScraperResult(
        source="laystars",
        entries=[],
        scraped_at=datetime.now(timezone.utc),
        success=True,
        error=None,
    )


class OddsOrchestrator:
    """Runs bookmaker + laystars scrapers, merges results, broadcasts changes."""

    def __init__(self) -> None:
        self.scrapers: list = [NovibetScraper()]  # add Stoiximan, BetShop later
        # self.laystars = LaystarsScraper()  # disabled for localhost test
        from ..comparator import OddsComparator
        self.comparator = OddsComparator()

        self.current_odds: list = []  # list[OddsEntry]
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self.subscribers: list[asyncio.Queue] = []

    async def start(self) -> None:
        """Initialize scrapers (e.g. single browser), set _running=True, run _poll_loop in background."""
        for s in self.scrapers:
            if hasattr(s, "initialize"):
                await s.initialize()

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Orchestrator started")

    async def _poll_loop(self) -> None:
        """Poll all sources every ~4s, merge, diff, push delta to subscribers."""
        while self._running:
            cycle_start = time.monotonic()

            # Run bookmaker scrapers only (Laystars disabled for localhost test)
            bookmaker_tasks = [s.fetch() for s in self.scrapers]
            # laystars_task = self.laystars.fetch()

            results: list = []
            gathered = await asyncio.gather(*bookmaker_tasks, return_exceptions=True)

            for r in gathered:
                if isinstance(r, Exception):
                    log.warning(f"Scraper error: {r}")
                    continue
                results.append(r)

            # Mock empty Laystars for localhost test without Laystars
            laystars_result = _mock_laystars_result()

            bookmaker_results = [r for r in results if r.success]
            merged = self.comparator.merge(bookmaker_results, laystars_result)
            delta = self.comparator.get_changes(self.current_odds, merged)
            sorted_entries = self.comparator.normalize_for_display(merged)

            self.current_odds = sorted_entries

            # Push delta to all subscriber queues (non-blocking)
            for q in self.subscribers:
                try:
                    q.put_nowait(delta)
                except asyncio.QueueFull:
                    log.debug("Subscriber queue full, drop delta")

            elapsed = time.monotonic() - cycle_start
            value_count = sum(1 for e in self.current_odds if e.is_value)
            log.info(
                f"Cycle: {elapsed*1000:.0f}ms | "
                f"entries={len(self.current_odds)} | "
                f"value={value_count}"
            )

            sleep_for = max(0, 4.0 - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def subscribe(self) -> asyncio.Queue:
        """Create a queue, add to subscribers, return it (for one WebSocket connection)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove queue from subscribers."""
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def get_current(self) -> list:
        """Return current_odds snapshot (list of OddsEntry)."""
        return list(self.current_odds)
