"""
Novibet.gr scraper — single Playwright browser, all API calls via page.evaluate().
Three-step approach:
  1. Get sport categories from main menu
  2. Get live events for SOCCER category
  3. Get odds for each event
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from .base import BaseScraper
from models import OddsEntry, ScraperResult

log = logging.getLogger(__name__)

BASE_URL = "https://www.novibet.gr"
LIVE_MENU_ID = 4324

FETCH_HEADERS = {
    "Accept": "application/json",
    "X-Gw-Application-Name": "Novi",
    "X-Gw-Channel": "WebPC",
    "X-Gw-Country-Sysname": "GR",
    "X-Gw-Language-Sysname": "el-GR",
    "X-Gw-Odds-Representation": "Decimal",
}

COMMON_PARAMS = "lang=el-GR&timeZ=GTB+Standard+Time&oddsR=1&usrGrp=GR"


class NovibetScraper(BaseScraper):

    def __init__(self):
        self._pw_cm = None
        self._playwright = None
        self._browser = None
        self._page = None

    async def initialize(self):
        self._pw_cm = async_playwright()
        self._playwright = await self._pw_cm.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
            locale="el-GR",
            timezone_id="Europe/Athens",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        self._page = await ctx.new_page()

        await self._page.goto(
            "https://www.novibet.gr/stoixima-live",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(4)

        for sel in ["text=Αποδέχομαι", "text=Αποδοχή", "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"]:
            try:
                btn = await self._page.query_selector(sel)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        log.info("NovibetScraper: browser initialized")

    async def _fetch_json(self, url: str):
        """Fetch JSON from inside the trusted Playwright browser."""
        result = await self._page.evaluate(
            """async (args) => {
                try {
                    const r = await fetch(args.url, { headers: args.headers });
                    const text = await r.text();
                    return { status: r.status, text: text };
                } catch(e) {
                    return { status: 0, text: '', error: e.toString() };
                }
            }""",
            {"url": url, "headers": FETCH_HEADERS},
        )
        status = result.get("status", 0)
        text = result.get("text", "")
        if status != 200:
            log.warning("Novibet API %s -> HTTP %s", url, status)
            print(f"HTTP {status} for {url[:80]}")
            return {}
        try:
            return json.loads(text)
        except Exception as e:
            log.warning("Novibet JSON parse error: %s | text[:200]: %s", e, text[:200])
            return {}

    async def fetch(self) -> ScraperResult:
        now = datetime.now(timezone.utc)
        try:
            ts = int(time.time() * 1000)

            # Step 1: Get all live soccer events + basic info
            main_url = f"https://www.novibet.gr/spt/feed/marketviews/location/v2/4324/4390/?lang=el-GR&timeZ=GTB+Standard+Time&oddsR=1&usrGrp=GR&timestamp={ts}"
            data = await self._fetch_json(main_url)

            if not data:
                return ScraperResult(
                    source="novibet", entries=[],
                    scraped_at=now, success=False,
                    error="Empty response from main endpoint"
                )

            # Step 2: Extract all events from betViews > competitions > events (store full raw event)
            events_to_fetch = []
            for view in (data if isinstance(data, list) else [data]):
                for bet_view in view.get("betViews", []):
                    for competition in bet_view.get("competitions", []):
                        comp_name = competition.get("caption", "")
                        for event in competition.get("events", []):
                            caps = event.get("additionalCaptions", {})
                            home = caps.get("competitor1", "")
                            away = caps.get("competitor2", "")
                            event_id = event.get("betContextId")
                            live_data = event.get("liveData", {})
                            if event_id and home and away:
                                events_to_fetch.append({
                                    "event_id": event_id,
                                    "home": home,
                                    "away": away,
                                    "competition": comp_name,
                                    "live_data": live_data,
                                    "raw": event,
                                })

            print(f"Found {len(events_to_fetch)} live soccer events")
            for e in events_to_fetch[:5]:
                h = (e['home'] or "").encode("ascii", errors="replace").decode("ascii")
                a = (e['away'] or "").encode("ascii", errors="replace").decode("ascii")
                print(f"  {h} v {a} (id={e['event_id']})")

            first_event = events_to_fetch[0]["raw"] if events_to_fetch else {}
            print(f"Raw event keys: {list(first_event.keys())}")

            # Step 3: Parse odds directly from each event's raw object (no HTTP calls)
            ALLOWED_MARKETS = {
                "SOCCER_MATCH_RESULT": "MATCH_ODDS",
                "SOCCER_UNDER_OVER": "GOALS_OVER_UNDER",
                "SOCCER_DOUBLE_CHANCE": "DOUBLE_CHANCE",
                "SOCCER_FIRST_HALF_RESULT": "FIRST_HALF_RESULT",
                "SOCCER_BOTH_TEAMS_TO_SCORE": "BOTH_TEAMS_TO_SCORE",
                "SOCCER_FIRST_HALF_UNDER_OVER": "FIRST_HALF_OVER_UNDER",
            }

            def parse_event_odds(event: dict, debug_first: bool = False) -> list[OddsEntry]:
                home = event["home"]
                away = event["away"]
                game_name = f"{home} v {away}"
                game_time = now.strftime("%d-%m %H:%M")
                raw_event = event.get("raw", {})
                markets = raw_event.get("markets", [])

                if debug_first:
                    print(f"Number of markets: {len(markets)}")
                    if markets:
                        preview = json.dumps(markets[0], ensure_ascii=False)[:500]
                        preview = preview.encode("ascii", errors="replace").decode("ascii")
                        print(f"First market: {preview}")

                entries = []
                for market in markets:
                    bet_type = (
                        market.get("betTypeSysname") or
                        market.get("sysname") or
                        market.get("type") or
                        ""
                    )

                    if bet_type not in ALLOWED_MARKETS:
                        continue

                    market_std = ALLOWED_MARKETS[bet_type]

                    selections = market.get("betItems") or []

                    for sel in selections:
                        if not sel.get("isAvailable", False):
                            continue
                        sel_name = (
                            sel.get("caption") or
                            sel.get("name") or
                            sel.get("selectionName") or
                            ""
                        )
                        price = (
                            sel.get("price") or
                            sel.get("decimalPrice") or
                            sel.get("odds") or
                            sel.get("decimalOdds") or
                            0
                        )
                        try:
                            odds_float = float(price)
                        except (TypeError, ValueError):
                            odds_float = 0.0
                        if odds_float < 1.01:
                            continue

                        home_n = self.normalize_team_name(home)
                        away_n = self.normalize_team_name(away)
                        game_id = self.make_game_id(home_n, away_n, market_std, sel_name)

                        entries.append(OddsEntry(
                            game_id=game_id,
                            game_time=game_time,
                            game_name=game_name,
                            market=market_std,
                            selection=sel_name,
                            bookmaker="novibet",
                            back_odds=odds_float,
                            lay_odds=0.0,
                            lay_available=0.0,
                            ls1=0.0, ls2=0.0, ls3=0.0,
                            diff=0.0,
                            is_value=False,
                            updated_at=now,
                        ))
                return entries

            all_entries = []
            for i, e in enumerate(events_to_fetch):
                all_entries.extend(parse_event_odds(e, debug_first=(i == 0)))

            print(f"Total OddsEntry objects: {len(all_entries)}")
            for e in all_entries[:10]:
                gn = (e.game_name or "").encode("ascii", errors="replace").decode("ascii")
                sn = (e.selection or "").encode("ascii", errors="replace").decode("ascii")
                print(f"  {gn} | {e.market} | {sn} | {e.back_odds}")

            return ScraperResult(
                source="novibet",
                entries=all_entries,
                scraped_at=now,
                success=True,
                error=None,
            )

        except Exception as e:
            log.exception("fetch() error: %s", e)
            return ScraperResult(
                source="novibet", entries=[], scraped_at=now,
                success=False, error=str(e)
            )

    async def cleanup(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw_cm is not None:
            await self._pw_cm.__aexit__(None, None, None)
            self._pw_cm = None
            self._playwright = None
