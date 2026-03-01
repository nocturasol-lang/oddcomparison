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

BASE = "https://www.laystars888.com"  # Not /exchange-service

MARKET_MAP: dict[str, str] = {
    "MATCH_ODDS": "MATCH_ODDS",
    "OVER_UNDER_25": "GOALS_OVER_UNDER",
    "DOUBLE_CHANCE": "DOUBLE_CHANCE",
    "HALF_TIME": "FIRST_HALF_RESULT",
    "BOTH_TEAMS_TO_SCORE": "BOTH_TEAMS_TO_SCORE",
    "FIRST_HALF_GOALS_15": "FIRST_HALF_OVER_UNDER",
}

MAX_CONCURRENT = 20

# Exponential backoff for rate limits (429) and transient errors
BACKOFF_BASE_SEC = 1.0
BACKOFF_MAX_SEC = 60.0
BACKOFF_MAX_RETRIES = 6


class LaystarsScraper(BaseScraper):
    """Laystars888 exchange scraper. Lay prices only; no back odds."""

    def __init__(self) -> None:
        self._cookies: str = ""
        self.event_ids: list[str] = []  # manual fallback when discovery is down
        self._backoff_until: float = 0.0  # monotonic time; wait until this before next request after rate limit

    async def set_cookies(self, cookie_string: str) -> None:
        self._cookies = cookie_string

    async def _maybe_wait_backoff(self) -> None:
        """If we hit a rate limit earlier, sleep until backoff_until."""
        if self._backoff_until > 0:
            wait = self._backoff_until - time.monotonic()
            if wait > 0:
                log.warning("Laystars: rate limit backoff, waiting %.1fs", wait)
                await asyncio.sleep(wait)
            self._backoff_until = 0

    async def _request_with_backoff(
        self,
        client: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> aiohttp.ClientResponse | None:
        """Execute one request with exponential backoff on 429 or transient errors."""
        last_exc: Exception | None = None
        for attempt in range(BACKOFF_MAX_RETRIES):
            await self._maybe_wait_backoff()
            try:
                if method.upper() == "GET":
                    resp = await client.get(url, **kwargs)
                else:
                    resp = await client.post(url, **kwargs)
                if resp.status == 429:
                    backoff = min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_MAX_SEC)
                    self._backoff_until = time.monotonic() + backoff
                    log.warning("Laystars: 429 rate limit, backoff %.1fs (attempt %d)", backoff, attempt + 1)
                    await resp.release()
                    continue
                return resp
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_exc = e
                backoff = min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_MAX_SEC)
                log.warning("Laystars: request failed %s, backoff %.1fs: %s", url[-50:], backoff, e)
                await asyncio.sleep(backoff)
        if last_exc:
            raise last_exc
        return None

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
        """Get live event IDs from Laystars API - Updated with working endpoints"""
        ts = int(time.time() * 1000)
        root_base = BASE.replace("/exchange-service", "")

        # Working endpoints discovered from browser capture
        endpoints = [
            {
                "url": f"{root_base}/member-service/event/list-live-mapped?tzo=GMT%2B0200&_={ts}",
                "type": "live_mapped",
                "description": "Main live events endpoint",
            },
            {
                "url": f"{root_base}/exchange-service/events/highlightV2?tzo=GMT%2B0200&_={ts}",
                "type": "highlight",
                "description": "Highlight events",
            },
            {
                "url": f"{root_base}/exchange-service/events/Left-menu?tzo=GMT%2B0200&_={ts}",
                "type": "left_menu",
                "description": "Left menu events",
            },
        ]

        for endpoint in endpoints:
            try:
                print(f"🔍 Trying: {endpoint['description']}")
                print(f"   URL: {endpoint['url'][-80:]}")

                async with client.get(endpoint["url"], timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        print(f"   HTTP {resp.status}")
                        continue

                    try:
                        data = await resp.json()
                    except Exception:
                        print("   Invalid JSON response")
                        continue

                    # Extract event IDs based on endpoint type
                    event_ids: list[str] = []

                    if endpoint["type"] == "live_mapped":
                        # Response format: list of events with eventId field
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    # Try different possible field names
                                    eid = (
                                        item.get("eventId")
                                        or item.get("id")
                                        or item.get("event_id")
                                        or item.get("fixtureId")
                                    )
                                    if eid:
                                        event_ids.append(str(eid))
                        elif isinstance(data, dict):
                            # Could be wrapped in a container
                            for key in ["events", "data", "results", "liveEvents"]:
                                if key in data and isinstance(data[key], list):
                                    for item in data[key]:
                                        if isinstance(item, dict):
                                            eid = (
                                                item.get("eventId")
                                                or item.get("id")
                                                or item.get("event_id")
                                            )
                                            if eid:
                                                event_ids.append(str(eid))

                    elif endpoint["type"] == "highlight":
                        # Extract from highlight format
                        if isinstance(data, dict):
                            for category in data.get("categories", []):
                                for event in category.get("events", []):
                                    eid = event.get("eventId") or event.get("id")
                                    if eid:
                                        event_ids.append(str(eid))

                    elif endpoint["type"] == "left_menu":
                        # Left menu format
                        if isinstance(data, dict):
                            for sport in data.get("sports", []):
                                for event in sport.get("events", []):
                                    eid = event.get("eventId") or event.get("id")
                                    if eid:
                                        event_ids.append(str(eid))

                    # Remove duplicates and empty strings
                    event_ids = list(set([eid for eid in event_ids if eid and eid.strip()]))

                    if event_ids:
                        print(f"✅ Found {len(event_ids)} live events from {endpoint['type']}")
                        print(f"   First few: {event_ids[:5]}")
                        return event_ids
                    else:
                        print(f"   No event IDs found in {endpoint['type']} response")
                        if _DEBUG:
                            print(f"   Response preview: {str(data)[:500]}")

            except asyncio.TimeoutError:
                print(f"   Timeout on {endpoint['type']}")
                continue
            except Exception as e:
                print(f"   Error on {endpoint['type']}: {e}")
                continue

        # If all API endpoints fail, try scraping the main page
        print("⚠️ All API endpoints failed, trying to scrape main page...")
        html_ids = await self._scrape_event_ids_from_page(client)
        if html_ids:
            return html_ids

        # Last resort: use manual fallback
        if self.event_ids:
            print(f"⚠️ Using {len(self.event_ids)} manual fallback event IDs")
            return self.event_ids

        print("❌ No live events found")
        return []

    async def _scrape_event_ids_from_page(self, client: aiohttp.ClientSession) -> list[str]:
        """Try to scrape live event IDs from the main exchange page HTML."""
        try:
            resp = await self._request_with_backoff(
                client,
                "GET",
                "https://www.laystars888.com/xch/",
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp is not None and resp.status == 200:
                html = await resp.text()
                import re
                matches = re.findall(r"data-event-id=[\"'](\d+)[\"']", html)
                if matches:
                    print(f"✅ Found {len(matches)} events from HTML")
                    return list(set(matches))
            if resp is not None:
                await resp.release()
        except Exception:
            pass
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
            f"{BASE}/exchange-service/events/{event_id}/markets"
            "?currencyCode=EUR&groupOrder=popular&init=true&igm=true"
            "&tzo=GMT%2B0200"
        )

        resp = await self._request_with_backoff(
            client, "POST", url,
            json=[], timeout=aiohttp.ClientTimeout(total=15),
        )
        if resp is None:
            return [], set(), set()
        if resp.status != 200:
            text = await resp.text()
            await resp.release()
            log.warning(
                "Laystars event %s HTTP %s\n  URL: %s\n  Resp: %.500s",
                event_id, resp.status, url, text,
            )
            return [], set(), set()
        try:
            data = await resp.json()
        finally:
            await resp.release()

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

            game_time = self._extract_game_time(mkt, now)

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

    # Timestamp ranges: milliseconds (1.6e12–2.1e12) or seconds (1.6e9–2.1e9)
    _TS_MS_MIN, _TS_MS_MAX = 1_600_000_000_000, 2_100_000_000_000
    _TS_SEC_MIN, _TS_SEC_MAX = 1_600_000_000, 2_100_000_000

    @classmethod
    def _parse_timestamp_to_datetime(cls, val: int | float) -> datetime | None:
        """Convert numeric timestamp (ms or seconds) to UTC datetime, or None if out of range."""
        if not isinstance(val, (int, float)):
            return None
        try:
            if cls._TS_MS_MIN <= val <= cls._TS_MS_MAX:
                return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
            if cls._TS_SEC_MIN <= val <= cls._TS_SEC_MAX:
                return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
        return None

    @classmethod
    def _extract_game_time_dt(cls, mkt: list, fallback_now: datetime) -> datetime:
        """
        Extract game start time from market array as datetime.
        Tries multiple fields: iterate list for ms/s timestamps, then common indices.
        Returns fallback_now if nothing valid is found.
        """
        # 1. Scan all elements for numeric timestamps (ms or seconds)
        for i, val in enumerate(mkt):
            dt = cls._parse_timestamp_to_datetime(val)
            if dt is not None:
                return dt

        # 2. Common API positions: openDate/startTime often at low indices
        for idx in (0, 1, 2, 3, 4, 5, 6, 7, 8):
            if idx < len(mkt):
                dt = cls._parse_timestamp_to_datetime(mkt[idx])
                if dt is not None:
                    return dt

        # 3. If any element is a dict, look for timestamp-like keys
        for val in mkt:
            if isinstance(val, dict):
                for key in ("openDate", "startTime", "marketTime", "time", "date", "timestamp", "start"):
                    v = val.get(key)
                    dt = cls._parse_timestamp_to_datetime(v) if v is not None else None
                    if dt is not None:
                        return dt
                # ISO string fallback
                for key in ("openDate", "startTime", "marketTime", "time"):
                    s = val.get(key)
                    if isinstance(s, str) and s.strip():
                        dt = cls._parse_iso_datetime(s)
                        if dt is not None:
                            return dt

        return fallback_now

    @staticmethod
    def _parse_iso_datetime(s: str) -> datetime | None:
        """Parse ISO-like date string to UTC datetime."""
        s = s.strip()
        if not s:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        try:
            if hasattr(datetime, "fromisoformat"):
                normalized = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalized)
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
        return None

    @classmethod
    def _extract_game_time(cls, mkt: list, fallback_now: datetime) -> str:
        """
        Return game time as display string (dd-mm HH:MM). Uses multiple timestamp
        field detection; falls back to fallback_now so every entry has a valid value.
        """
        dt = cls._extract_game_time_dt(mkt, fallback_now)
        return dt.strftime("%d-%m %H:%M")

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
