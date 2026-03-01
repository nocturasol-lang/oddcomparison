"""Automatic cookie rotation for Laystars"""

import asyncio
import logging
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

log = logging.getLogger(__name__)


class LaystarsCookieRotator:
    """Automatically refresh Laystars cookies before expiry"""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.current_cookies = None
        self.last_refresh = None
        self.refresh_interval = timedelta(hours=6)  # Cookies usually last 24h, refresh every 6h

    async def get_cookies(self) -> str:
        """Get valid cookies, refreshing if needed"""
        if not self.current_cookies or self._should_refresh():
            await self._refresh_cookies()
        return self.current_cookies

    def _should_refresh(self) -> bool:
        if not self.last_refresh:
            return True
        return datetime.now() - self.last_refresh > self.refresh_interval

    async def _refresh_cookies(self):
        """Login to Laystars and get fresh cookies"""
        log.info("Refreshing Laystars cookies...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                # Go to login page
                await page.goto("https://www.laystars888.com/xch/")
                await page.wait_for_load_state("networkidle")

                # Click login button
                await page.click("text=Log In")
                await page.wait_for_selector("input[name='username']")

                # Fill login form
                await page.fill("input[name='username']", self.username)
                await page.fill("input[name='password']", self.password)
                await page.click("button[type='submit']")

                # Wait for login to complete
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)

                # Get cookies
                cookies = await context.cookies()
                cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                self.current_cookies = cookie_string
                self.last_refresh = datetime.now()
                log.info(f"✅ Cookies refreshed, length: {len(cookie_string)}")

            except Exception as e:
                log.error(f"Failed to refresh cookies: {e}")
                raise
            finally:
                await browser.close()
