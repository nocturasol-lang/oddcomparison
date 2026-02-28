"""
Laystars888.com scraper — Betfair exchange clone for Greek market.
Lay-only data: lay_odds, ls1, ls2, ls3, lay_available.
Uses aiohttp for API; Playwright fallback for HTML when no API works.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .base import BaseScraper
from models import OddsEntry, ScraperResult

log = logging.getLogger(__name__)

# Try these API endpoints in order; use whichever returns JSON
API_CANDIDATES = [
    "https://laystars888.com/api/v1/live/football",
    "https://laystars888.com/api/exchange/football/live",
    "https://laystars888.com/live/football/json",
    "https://laystars888.com/api/markets?sport=football&inplay=true",
]

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "el-GR,el;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
}


class LaystarsScraper(BaseScraper):
    """Laystars888 exchange scraper. Lay prices only; no back odds."""

    def __init__(self):
        self.endpoint: str = ""
        self._pw_cm = None
        self._playwright = None
        self._browser = None
        self._page = None

    async def discover_endpoint(self) -> str:
        """Try each URL with aiohttp; return the first that gives JSON. Save to self.endpoint."""
        if self.endpoint:
            return self.endpoint

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as client:
            for url in API_CANDIDATES:
                try:
                    async with client.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            continue
                        text = await resp.text()
                        # Try to parse as JSON (some APIs don't set Content-Type correctly)
                        try:
                            data = json.loads(text)
                            if data is not None:
                                self.endpoint = url
                                print(f"Laystars: endpoint worked: {url}")
                                log.info("Laystars discover_endpoint: %s", url)
                                return url
                        except json.JSONDecodeError:
                            content_type = resp.headers.get("Content-Type", "")
                            if "json" in content_type.lower():
                                self.endpoint = url
                                print(f"Laystars: endpoint worked: {url}")
                                return url
                            continue
                except asyncio.TimeoutError:
                    log.debug("Laystars discover: timeout %s", url)
                except Exception as e:
                    log.debug("Laystars discover: %s -> %s", url, e)

        log.info("Laystars discover_endpoint: no API worked, will use Playwright HTML fallback")
        self.endpoint = ""
        return ""

    async def fetch(self) -> ScraperResult:
        """Fetch live football lay odds from Laystars888. Returns ScraperResult."""
        now = datetime.now(timezone.utc)

        try:
            endpoint = await self.discover_endpoint()

            if endpoint:
                return await self._fetch_api(endpoint, now)
            return await self._fetch_html_playwright(now)
        except Exception as e:
            log.exception("Laystars fetch failed: %s", e)
            return ScraperResult(
                source="laystars",
                entries=[],
                scraped_at=now,
                success=False,
                error=str(e),
            )

    async def _fetch_api(self, url: str, now: datetime) -> ScraperResult:
        """Fetch from discovered JSON API endpoint."""
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as client:
            async with client.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return ScraperResult(
                        source="laystars",
                        entries=[],
                        scraped_at=now,
                        success=False,
                        error=f"HTTP {resp.status}",
                    )
                try:
                    data = await resp.json()
                except Exception as e:
                    return ScraperResult(
                        source="laystars",
                        entries=[],
                        scraped_at=now,
                        success=False,
                        error=str(e),
                    )

        entries = self._parse_api_response(data, now)
        return ScraperResult(
            source="laystars",
            entries=entries,
            scraped_at=now,
            success=True,
            error=None,
        )

    def _parse_api_response(self, data: Any, now: datetime) -> list[OddsEntry]:
        """Convert Betfair-style JSON to OddsEntry list. Extract game_name, market, selection, ls1, ls2, ls3, lay_available."""
        entries: list[OddsEntry] = []

        # Common shapes: { "events": [...] } or { "markets": [...] } or list at root
        events = (
            data.get("events")
            or data.get("eventList")
            or data.get("live")
            or data.get("markets")
            or (data if isinstance(data, list) else [])
        )
        if not isinstance(events, list):
            return entries

        for event in events:
            if not isinstance(event, dict):
                continue
            game_name = event.get("name") or event.get("eventName") or event.get("title") or ""
            if not game_name and isinstance(event.get("event"), dict):
                ev = event["event"]
                game_name = ev.get("name") or ev.get("eventName") or ""
            game_name = str(game_name or "")
            parts = game_name.split(" - ") if game_name else []
            home = parts[0].strip() if parts else ""
            away = parts[-1].strip() if len(parts) > 1 else ""
            home_n = self.normalize_team_name(home)
            away_n = self.normalize_team_name(away)
            game_time = str(event.get("startTime") or event.get("time") or event.get("gameTime") or "")

            markets = event.get("markets") or event.get("marketList") or event.get("runners") or []
            if not isinstance(markets, list):
                markets = [markets] if markets else []

            for market in markets:
                if not isinstance(market, dict):
                    continue
                market_name = market.get("name") or market.get("marketName") or ""
                market_std = self.normalize_market(market_name)

                runners = market.get("runners") or market.get("selections") or market.get("outcomes") or []
                if not isinstance(runners, list):
                    runners = [runners] if runners else []

                for runner in runners:
                    if not isinstance(runner, dict):
                        continue
                    sel_name = runner.get("name") or runner.get("runnerName") or runner.get("selectionName") or ""

                    # Lay ladder: ls1 (best), ls2, ls3, lay_available (liquidity EUR)
                    lay_odds = 0.0
                    lay_available = 0.0
                    ls1, ls2, ls3 = 0.0, 0.0, 0.0

                    ladder = runner.get("ex") or runner.get("lay") or runner.get("ladder") or {}
                    prices = ladder.get("availableToLay") or ladder.get("layPrices") or ladder.get("prices") or []
                    if isinstance(prices, list) and len(prices) >= 1:
                        first = prices[0]
                        if isinstance(first, dict):
                            lay_odds = float(first.get("price") or first.get("odds") or 0)
                            lay_available = float(first.get("size") or first.get("available") or first.get("liquidity") or 0)
                        else:
                            lay_odds = float(first)
                        ls1 = lay_odds
                        if len(prices) >= 2:
                            second = prices[1]
                            if isinstance(second, dict):
                                ls2 = float(second.get("price") or second.get("odds") or 0)
                            else:
                                ls2 = float(second)
                        if len(prices) >= 3:
                            third = prices[2]
                            if isinstance(third, dict):
                                ls3 = float(third.get("price") or third.get("odds") or 0)
                            else:
                                ls3 = float(third)
                    else:
                        lay_odds = float(runner.get("layPrice") or runner.get("odds") or 0)
                        lay_available = float(runner.get("size") or runner.get("available") or 0)
                        ls1 = lay_odds

                    game_id = self.make_game_id(home_n, away_n, market_std, sel_name)

                    entries.append(
                        OddsEntry(
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
                        )
                    )

        return entries

    async def _fetch_html_playwright(self, now: datetime) -> ScraperResult:
        """Fallback: use Playwright to scrape live football page HTML for odds tables."""
        try:
            self._pw_cm = async_playwright()
            self._playwright = await self._pw_cm.__aenter__()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
                locale="el-GR",
            )
            self._page = await ctx.new_page()

            await self._page.goto(
                "https://laystars888.com/live/football",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            html = await self._page.content()
        except Exception as e:
            log.exception("Laystars Playwright HTML fetch failed: %s", e)
            return ScraperResult(
                source="laystars",
                entries=[],
                scraped_at=now,
                success=False,
                error=str(e),
            )
        finally:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._pw_cm is not None:
                await self._pw_cm.__aexit__(None, None, None)
                self._pw_cm = None
                self._playwright = None

        entries: list[OddsEntry] = []
        soup = BeautifulSoup(html, "html.parser")

        # Find tables or divs that look like odds
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                text_parts = [c.get_text(strip=True) for c in cells]
                for i, part in enumerate(text_parts):
                    try:
                        odds_val = float(part.replace(",", "."))
                        if 1.01 <= odds_val <= 100:
                            sel_name = " ".join(text_parts[:i]) or (text_parts[0] if text_parts else "")
                            game_name = ""
                            market_std = "MATCH_ODDS"
                            game_id = f"unknown_vs_unknown_{market_std}_{(sel_name or 'unknown')[:50]}"
                            entries.append(
                                OddsEntry(
                                    game_id=game_id,
                                    game_time="",
                                    game_name=game_name,
                                    market=market_std,
                                    selection=sel_name,
                                    bookmaker="laystars",
                                    back_odds=0.0,
                                    lay_odds=odds_val,
                                    lay_available=0.0,
                                    ls1=odds_val,
                                    ls2=0.0,
                                    ls3=0.0,
                                    diff=0.0,
                                    is_value=False,
                                    updated_at=now,
                                )
                            )
                            break
                    except ValueError:
                        continue

        return ScraperResult(
            source="laystars",
            entries=entries,
            scraped_at=now,
            success=True,
            error=None,
        )
