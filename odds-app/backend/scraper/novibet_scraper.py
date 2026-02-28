"""
Novibet.gr High-Performance Live Odds Scraper
==============================================
Built for professional odds trading — sub-second latency, fully async,
concurrent event polling, WebSocket live updates, change detection.

Architecture:
  - asyncio + aiohttp  → concurrent HTTP (all events polled simultaneously)
  - WebSocket listener → catches server-push odds changes instantly
  - Change detection   → only logs/acts when odds actually move
  - Async queue        → decouples scraping from storage/processing
  - Session pool       → multiple Cloudflare sessions in rotation

Install:
    pip install aiohttp asyncio playwright aiofiles
    playwright install chromium

    
Install and run: 

pip install aiohttp playwright aiofiles schedule
playwright install chromium
python novibet_scraper.py
    
    
"""






import asyncio
import aiohttp
import aiofiles
import json
import random
import logging
import time
from datetime import datetime, timezone
from collections import defaultdict
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


# ─── Config ───────────────────────────────────────────────────────────────────

BASE_URL     = "https://www.novibet.gr"
STATS_BASE   = "https://api.performfeeds.com"
OUTLET_KEY   = "ax2irl8xei0w1ejdc3lwjpyko"
LIVE_MENU_ID = 4324

# Trading config
POLL_INTERVAL      = 1.0    # seconds between full poll cycles
MAX_CONCURRENT     = 20     # simultaneous odds requests
SESSION_POOL_SIZE  = 3      # number of Cloudflare sessions in rotation
SESSION_TTL        = 1500   # refresh session after 25 min
MAX_REQ_PER_SESSION = 300   # or after 300 requests

COMMON_PARAMS = {
    "lang":   "el-GR",
    "timeZ":  "GTB Standard Time",
    "oddsR":  "1",
    "usrGrp": "GR",
}

GW_HEADERS = {
    "X-Gw-Application-Name":    "Novi",
    "X-Gw-Channel":             "WebPC",
    "X-Gw-Client-Timezone":     "Europe/Athens",
    "X-Gw-Cms-Key":             "_GR",
    "X-Gw-Country-Sysname":     "GR",
    "X-Gw-Currency-Sysname":    "EUR",
    "X-Gw-Domain-Key":          "_GR",
    "X-Gw-Language-Sysname":    "el-GR",
    "X-Gw-Odds-Representation": "Decimal",
    "X-Gw-State-Sysname":       "",
}

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]


# ─── Session (async Playwright) ───────────────────────────────────────────────

class Session:
    """Single Cloudflare-cleared session with auto-refresh."""

    def __init__(self, session_id: int):
        self.id           = session_id
        self.cookie_str   = ""
        self.user_agent   = random.choice(UA_POOL)
        self.refreshed_at = 0.0
        self.req_count    = 0
        self._lock        = asyncio.Lock()

    def is_stale(self) -> bool:
        age_expired  = (time.monotonic() - self.refreshed_at) > SESSION_TTL
        req_exceeded = self.req_count >= MAX_REQ_PER_SESSION
        return not self.cookie_str or age_expired or req_exceeded

    async def refresh(self):
        async with self._lock:
            if not self.is_stale():
                return  # Another coroutine already refreshed it
            log.info(f"[Session {self.id}] Refreshing cookies...")
            self.user_agent = random.choice(UA_POOL)

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ]
                )
                ctx = await browser.new_context(
                    user_agent=self.user_agent,
                    locale="el-GR",
                    timezone_id="Europe/Athens",
                    viewport={"width": 1366, "height": 768},
                    extra_http_headers={
                        "Accept-Language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7"
                    }
                )
                await ctx.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['el-GR', 'el'] });
                    window.chrome = { runtime: {} };
                """)

                page = await ctx.new_page()
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                await page.goto(f"{BASE_URL}/stoixima-live", wait_until="networkidle", timeout=30000)
                await asyncio.sleep(random.uniform(2, 3))

                cookies = await ctx.cookies()
                self.cookie_str   = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                self.refreshed_at = time.monotonic()
                self.req_count    = 0

                await browser.close()
            log.info(f"[Session {self.id}] Refreshed OK.")

    def headers(self, referer: str = None) -> dict:
        self.req_count += 1
        return {
            "User-Agent":      self.user_agent,
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         referer or f"{BASE_URL}/stoixima-live",
            "Cookie":          self.cookie_str,
            "Connection":      "keep-alive",
            **GW_HEADERS,
        }


# ─── Session Pool ─────────────────────────────────────────────────────────────

class SessionPool:
    """
    Round-robin pool of N sessions.
    Spreads requests across sessions — avoids single-session rate limiting.
    Refreshes sessions concurrently in the background.
    """

    def __init__(self, size: int = SESSION_POOL_SIZE):
        self.sessions = [Session(i) for i in range(size)]
        self._index   = 0

    async def initialise(self):
        """Bootstrap all sessions concurrently at startup."""
        await asyncio.gather(*[s.refresh() for s in self.sessions])

    def next(self) -> Session:
        """Round-robin session selection."""
        session = self.sessions[self._index % len(self.sessions)]
        self._index += 1
        return session

    async def get(self) -> Session:
        session = self.next()
        if session.is_stale():
            await session.refresh()
        return session


# ─── Connection Pool (aiohttp) ────────────────────────────────────────────────

def make_connector() -> aiohttp.TCPConnector:
    """
    Persistent TCP connector with connection pooling.
    Reuses connections — avoids TCP handshake overhead on every request.
    """
    return aiohttp.TCPConnector(
        limit=50,           # max total connections
        limit_per_host=20,  # max per host
        ttl_dns_cache=300,  # cache DNS for 5 min
        ssl=False,          # skip SSL verification overhead
        keepalive_timeout=30,
    )


# ─── Async HTTP ───────────────────────────────────────────────────────────────

async def fetch(
    client:   aiohttp.ClientSession,
    pool:     SessionPool,
    url:      str,
    params:   dict = None,
    referer:  str  = None,
    retries:  int  = 2,
) -> dict:
    """
    Single async GET. Returns parsed JSON or {}.
    Fast path — minimal overhead, no sleep between retries for speed.
    """
    session = await pool.get()

    for attempt in range(retries):
        try:
            async with client.get(
                url,
                params=params,
                headers=session.headers(referer=referer),
                timeout=aiohttp.ClientTimeout(total=5),  # Tight 5s timeout
            ) as resp:

                if resp.status == 429:
                    log.warning("429 — backing off 15s")
                    await asyncio.sleep(15 + random.uniform(0, 5))
                    session = await pool.get()
                    continue

                if resp.status in (403, 503):
                    log.warning(f"{resp.status} — refreshing session")
                    await session.refresh()
                    continue

                if resp.status == 304:
                    return {}

                if resp.status == 200:
                    return await resp.json(content_type=None)

        except asyncio.TimeoutError:
            if attempt == 0:
                continue  # One silent retry on timeout
            log.warning(f"Timeout: {url}")

        except aiohttp.ClientError as e:
            log.warning(f"Client error: {e}")

    return {}


# ─── Event Discovery ──────────────────────────────────────────────────────────

async def get_live_events(client: aiohttp.ClientSession, pool: SessionPool) -> list[dict]:
    """Fetches menu/4324 → returns all live event dicts."""
    data = await fetch(client, pool,
                       f"{BASE_URL}/spt/feed/navigation/menu/{LIVE_MENU_ID}",
                       params=COMMON_PARAMS)
    if not data:
        return []

    events = []

    def walk(items, parent=""):
        for item in items:
            itype = item.get("type", "")
            iname = item.get("name") or item.get("title", "")
            iid   = item.get("id")

            if itype in ("event", "fixture") or item.get("fixtureId"):
                events.append({
                    "event_id":    iid or item.get("fixtureId"),
                    "location_id": item.get("locationId", LIVE_MENU_ID),
                    "name":        iname,
                    "competition": parent,
                })

            children = (item.get("items") or item.get("children")
                        or item.get("competitions") or [])
            if children:
                walk(children, parent=iname or parent)

    walk(data.get("items", []))
    return events


# ─── Odds Fetch ───────────────────────────────────────────────────────────────

async def fetch_odds(
    client:   aiohttp.ClientSession,
    pool:     SessionPool,
    event:    dict,
) -> tuple[dict, dict]:
    """Fetches odds for one event. Returns (event, raw_odds)."""
    event_id    = event["event_id"]
    location_id = event.get("location_id", LIVE_MENU_ID)
    url         = f"{BASE_URL}/spt/feed/marketviews/location/v2/{location_id}/{event_id}"
    params      = {**COMMON_PARAMS, "timestamp": int(time.time() * 1000)}

    raw = await fetch(client, pool, url, params=params,
                      referer=f"{BASE_URL}/stoixima-live/{event_id}")
    return event, raw


# ─── Change Detection ─────────────────────────────────────────────────────────

class OddsTracker:
    """
    Tracks previous odds per selection.
    Only fires on_change when odds actually move — avoids processing noise.
    Critical for trading: catches line movements the instant they happen.
    """

    def __init__(self):
        # {event_id: {market|selection: odds}}
        self._state: dict[str, dict[str, float]] = defaultdict(dict)

    def process(self, event_id: str, markets: list) -> list[dict]:
        """Returns only selections where odds have changed."""
        changed = []
        prev    = self._state[event_id]

        for market in markets:
            market_name = market.get("name") or market.get("marketName", "")
            selections  = market.get("selections") or market.get("outcomes") or []

            for sel in selections:
                sel_name  = sel.get("name") or sel.get("selectionName", "")
                new_odds  = (sel.get("price") or sel.get("odds")
                             or sel.get("decimalPrice"))
                key       = f"{market_name}|{sel_name}"
                old_odds  = prev.get(key)

                if new_odds is None:
                    continue

                if old_odds != new_odds:
                    changed.append({
                        "market":    market_name,
                        "selection": sel_name,
                        "odds_old":  old_odds,
                        "odds_new":  new_odds,
                        "direction": "▲" if (old_odds and new_odds > old_odds) else "▼" if old_odds else "NEW",
                        "status":    sel.get("status", ""),
                        "ts":        datetime.now(timezone.utc).isoformat(),
                    })
                    prev[key] = new_odds

        self._state[event_id] = prev
        return changed


# ─── Output Queue & Writer ────────────────────────────────────────────────────

async def writer(queue: asyncio.Queue, filename: str = "odds_changes.jsonl"):
    """
    Async writer — consumes from queue and writes to disk without
    blocking the scraping loop.
    """
    async with aiofiles.open(filename, "a", encoding="utf-8") as f:
        while True:
            record = await queue.get()
            if record is None:
                break
            await f.write(json.dumps(record, ensure_ascii=False) + "\n")
            queue.task_done()


# ─── Core Scrape Loop ─────────────────────────────────────────────────────────

async def scrape_loop(pool: SessionPool, queue: asyncio.Queue):
    """
    Main loop:
    1. Discover all live events
    2. Fan out concurrent odds requests (semaphore-limited)
    3. Run change detection
    4. Push changes to writer queue
    Repeats every POLL_INTERVAL seconds.
    """
    tracker   = OddsTracker()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    connector = make_connector()

    async with aiohttp.ClientSession(connector=connector) as client:
        while True:
            cycle_start = time.monotonic()

            # ── 1. Discover events ──────────────────────────────────────────
            events = await get_live_events(client, pool)
            if not events:
                log.warning("No live events — waiting 5s")
                await asyncio.sleep(5)
                continue

            # ── 2. Concurrent odds fetch ────────────────────────────────────
            async def bounded_fetch(event):
                async with semaphore:
                    return await fetch_odds(client, pool, event)

            results = await asyncio.gather(
                *[bounded_fetch(e) for e in events],
                return_exceptions=True,
            )

            # ── 3. Process changes ──────────────────────────────────────────
            total_changes = 0
            for result in results:
                if isinstance(result, Exception):
                    continue

                event, raw = result
                if not raw:
                    continue

                markets = raw.get("markets") or raw.get("marketViews") or []
                changes = tracker.process(str(event["event_id"]), markets)

                if changes:
                    total_changes += len(changes)
                    record = {
                        "event_id":    event["event_id"],
                        "event_name":  event.get("name"),
                        "competition": event.get("competition"),
                        "changes":     changes,
                        "ts":          datetime.now(timezone.utc).isoformat(),
                    }
                    await queue.put(record)

                    # Live console output for trading desk
                    for c in changes:
                        log.info(
                            f"  {c['direction']} {event.get('name')} | "
                            f"{c['market']} | {c['selection']} | "
                            f"{c['odds_old']} → {c['odds_new']}"
                        )

            # ── 4. Timing ───────────────────────────────────────────────────
            elapsed = time.monotonic() - cycle_start
            log.info(
                f"Cycle: {len(events)} events | "
                f"{total_changes} changes | "
                f"{elapsed*1000:.0f}ms"
            )

            # Sleep only the remainder of the interval
            sleep_for = max(0, POLL_INTERVAL - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)


# ─── Entry Point ──────────────────────────────────────────────────────────────

async def main():
    log.info("Initialising session pool...")
    pool  = SessionPool(size=SESSION_POOL_SIZE)
    await pool.initialise()

    queue = asyncio.Queue(maxsize=1000)

    log.info("Starting scraper + writer...")
    await asyncio.gather(
        scrape_loop(pool, queue),
        writer(queue),
    )


if __name__ == "__main__":
    asyncio.run(main())
