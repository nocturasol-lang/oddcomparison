"""
Laystars888.com scraper — Betfair exchange clone for Greek market.
Lay-only data: lay_odds, ls1, ls2, ls3, lay_available.

2-step flow (requires session cookies):
  1. GET  list-live-mapped   → live event IDs  (or use manually-set fallback IDs)
  2. POST events/{id}/markets body=[]  → all markets + lay ladders per runner
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import json

import aiohttp

from .base import BaseScraper
from models import OddsEntry, ScraperResult

log = logging.getLogger(__name__)

_DEBUG = os.environ.get("DEBUG_LAYSTARS") == "1"

BASE = "https://www.laystars888.com/exchange-service"

MARKET_MAP: dict[str, str] = {
    "MATCH_ODDS": "MATCH_ODDS",
    "OVER_UNDER_25": "GOALS_OVER_UNDER",
    "DOUBLE_CHANCE": "DOUBLE_CHANCE",
    "HALF_TIME": "FIRST_HALF_RESULT",
    "BOTH_TEAMS_TO_SCORE": "BOTH_TEAMS_TO_SCORE",
    "FIRST_HALF_GOALS_15": "FIRST_HALF_OVER_UNDER",
}

MAX_CONCURRENT = 20


class LaystarsScraper(BaseScraper):
    """Laystars888 exchange scraper. Lay prices only; no back odds."""

    def __init__(self) -> None:
        self._cookies: str = ""
        self.event_ids: list[str] = []  # manual fallback when discovery is down

    async def set_cookies(self, cookie_string: str) -> None:
        self._cookies = cookie_string

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.laystars888.com",
            "Referer": "https://www.laystars888.com/xch/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
        }
        if self._cookies:
            h["Cookie"] = self._cookies
        return h

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def fetch(self) -> ScraperResult:
        now = datetime.now(timezone.utc)

        if not self._cookies:
            return ScraperResult(
                source="laystars", entries=[], scraped_at=now,
                success=False, error="no_cookies_configured",
            )

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, headers=self._headers()) as client:
            # Step 1: discover live event IDs (fall back to manual list)
            try:
                event_ids = await self._get_live_event_ids(client)
            except Exception as exc:
                log.warning("Laystars discovery failed: %s", exc)
                event_ids = []

            if not event_ids and self.event_ids:
                event_ids = list(self.event_ids)
                print(f"Laystars: discovery returned 0, using {len(event_ids)} manual event IDs")

            if not event_ids:
                return ScraperResult(
                    source="laystars", entries=[], scraped_at=now,
                    success=False, error="no_live_events (discovery may be in maintenance)",
                )

            print(f"Laystars: {len(event_ids)} live event IDs")

            # Step 2: fetch all markets per event (POST body=[] returns everything)
            sem = asyncio.Semaphore(MAX_CONCURRENT)
            all_entries: list[OddsEntry] = []
            errors: list[str] = []
            raw_codes_seen: set[str] = set()
            mapped_types_seen: set[str] = set()

            async def process_event(eid: str) -> None:
                async with sem:
                    try:
                        entries, raw_codes, mapped_types = await self._fetch_event_odds(
                            eid, client, now,
                        )
                        all_entries.extend(entries)
                        raw_codes_seen.update(raw_codes)
                        mapped_types_seen.update(mapped_types)
                    except Exception as exc:
                        msg = f"event_{eid}:{exc}"
                        log.warning("Laystars event %s failed: %s", eid, exc)
                        errors.append(msg)

            await asyncio.gather(*(process_event(eid) for eid in event_ids))

            if _DEBUG:
                print(f"[DEBUG_LAYSTARS] raw market codes seen: {sorted(raw_codes_seen)}")
                print(f"[DEBUG_LAYSTARS] mapped canonical types: {sorted(mapped_types_seen)}")

        ok = len(all_entries) > 0
        err_str: str | None = None
        if not ok:
            err_str = f"parsed_zero_entries:{len(event_ids)}_events"
            if errors:
                err_str += " | " + "; ".join(errors[:5])

        return ScraperResult(
            source="laystars", entries=all_entries,
            scraped_at=now, success=ok, error=err_str,
        )

    # ------------------------------------------------------------------
    # Step 1 — live event IDs
    # ------------------------------------------------------------------

    async def _get_live_event_ids(self, client: aiohttp.ClientSession) -> list[str]:
        ts = int(time.time() * 1000)

        endpoints = [
            f"{BASE}/left-menu/eventId?tzo=GMT%2B0200&_={ts}",
            f"{BASE}/inplay?sport=soccer&tzo=GMT%2B0200&_={ts}",
            f"{BASE}/sport/1/inplay?tzo=GMT%2B0200&_={ts}",
            f"{BASE}/left-menu?eventId=&tzo=GMT%2B0200&sport=soccer&_={ts}",
            f"{BASE}/inplay/list?tzo=GMT%2B0200&_={ts}",
            f"{BASE}/soccer/live?tzo=GMT%2B0200&_={ts}",
        ]

        for url in endpoints:
            try:
                async with client.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    print(f"GET {resp.status} ...{url[-60:]}")
                    if resp.status == 200:
                        text = await resp.text()
                        if not text.strip().startswith("<"):
                            print(f"  Response: {text[:300]}")
            except Exception as exc:
                print(f"  Error: {exc}")

        return []

    @staticmethod
    def _extract_event_ids(data) -> list[str]:
        """Try to pull event IDs from various JSON shapes."""
        if isinstance(data, dict):
            for key in ("liveCenters", "eventIds", "events", "ids", "liveEvents"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    return [str(i) for i in val if i]
            # left-menu: look for nested event IDs
            for key in ("soccer", "football", "inplay", "live", "menu"):
                val = data.get(key)
                if isinstance(val, list):
                    ids = []
                    for item in val:
                        if isinstance(item, dict):
                            eid = item.get("eventId") or item.get("id") or item.get("eventid")
                            if eid:
                                ids.append(str(eid))
                    if ids:
                        return ids
        elif isinstance(data, list):
            if data and isinstance(data[0], (str, int)):
                return [str(i) for i in data if i]
            ids = []
            for item in data:
                if isinstance(item, dict):
                    eid = item.get("eventId") or item.get("id") or item.get("eventid")
                    if eid:
                        ids.append(str(eid))
            if ids:
                return ids
        return []

    # ------------------------------------------------------------------
    # Step 2 — lay odds for one event (POST body=[] → all markets)
    # ------------------------------------------------------------------

    async def _fetch_event_odds(
        self,
        event_id: str,
        client: aiohttp.ClientSession,
        now: datetime,
    ) -> tuple[list[OddsEntry], set[str], set[str]]:
        url = (
            f"{BASE}/events/{event_id}/markets"
            "?currencyCode=EUR&groupOrder=popular&init=true&igm=true"
            "&tzo=GMT%2B0200"
        )

        async with client.post(
            url, json=[], timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                log.warning(
                    "Laystars event %s HTTP %s\n  URL: %s\n  Resp: %.500s",
                    event_id, resp.status, url, text,
                )
                return [], set(), set()
            data = await resp.json()

        event_name = data.get("eventName", "")
        home_name = data.get("homeName", "")
        away_name = data.get("awayName", "")
        game_name = event_name or (f"{home_name} v {away_name}" if home_name else "")

        raw_markets = data.get("markets")
        if not isinstance(raw_markets, list):
            return [], set(), set()

        entries: list[OddsEntry] = []
        raw_codes: set[str] = set()
        mapped_types: set[str] = set()

        for mkt in raw_markets:
            if not isinstance(mkt, list) or len(mkt) < 15:
                continue

            market_code = str(mkt[9] or "")
            raw_codes.add(market_code)

            market_std = MARKET_MAP.get(market_code)
            if market_std is None:
                continue
            mapped_types.add(market_std)

            game_time = self._extract_game_time(mkt)

            runners = mkt[14]
            if not isinstance(runners, list):
                continue

            for runner in runners:
                if not isinstance(runner, list) or len(runner) < 4:
                    continue

                sel_name = str(runner[1] or "")
                ls1, ls2, ls3, lay_available = self._parse_lay_ladder(runner)

                if home_name and away_name:
                    game_id = self.make_game_id(home_name, away_name, market_std, sel_name)
                else:
                    game_id = f"{event_id}_{market_code}_{sel_name.strip().lower()}"

                entries.append(OddsEntry(
                    game_id=game_id,
                    game_time=game_time,
                    game_name=game_name,
                    market=market_std,
                    selection=sel_name,
                    bookmaker="laystars",
                    back_odds=0.0,
                    lay_odds=ls1,
                    lay_available=lay_available,
                    ls1=ls1,
                    ls2=ls2,
                    ls3=ls3,
                    diff=0.0,
                    is_value=False,
                    updated_at=now,
                ))

        return entries, raw_codes, mapped_types

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_game_time(mkt: list) -> str:
        for val in mkt:
            if isinstance(val, (int, float)) and 1_600_000_000_000 < val < 2_100_000_000_000:
                dt = datetime.fromtimestamp(val / 1000, tz=timezone.utc)
                return dt.strftime("%d-%m %H:%M")
        return ""

    @staticmethod
    def _parse_lay_ladder(runner: list) -> tuple[float, float, float, float]:
        """Extract ls1, ls2, ls3, lay_available from runner[3][1] (lay ladder)."""
        ls1 = ls2 = ls3 = 0.0
        lay_available = 0.0

        try:
            ladders = runner[3]
            if not isinstance(ladders, list) or len(ladders) < 2:
                return ls1, ls2, ls3, lay_available
            lay_ladder = ladders[1]
            if not isinstance(lay_ladder, list):
                return ls1, ls2, ls3, lay_available
        except (IndexError, TypeError):
            return ls1, ls2, ls3, lay_available

        if len(lay_ladder) >= 1 and isinstance(lay_ladder[0], dict):
            ls1 = float(lay_ladder[0].get("price", 0))
            lay_available = float(lay_ladder[0].get("size", 0))
        if len(lay_ladder) >= 2 and isinstance(lay_ladder[1], dict):
            ls2 = float(lay_ladder[1].get("price", 0))
        if len(lay_ladder) >= 3 and isinstance(lay_ladder[2], dict):
            ls3 = float(lay_ladder[2].get("price", 0))

        return ls1, ls2, ls3, lay_available
