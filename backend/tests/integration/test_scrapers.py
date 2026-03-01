#!/usr/bin/env python3.12
"""Integration tests for scraper components, with safe live checks."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright.async_api", reason="playwright is required for Novibet integration test")

from scraper.laystars import LaystarsScraper
from scraper.novibet import NovibetScraper


@pytest.mark.asyncio
async def test_scrapers_mocked_smoke() -> None:
    """Smoke-test both scrapers in mocked mode for stable CI/local runs."""
    lay = LaystarsScraper()
    lay._cookies = "session=test"
    lay._get_live_event_ids = AsyncMock(return_value=["evt-1"])  # type: ignore[method-assign]
    lay._fetch_event_odds = AsyncMock(return_value=([], {"MATCH_ODDS"}, {"MATCH_ODDS"}))  # type: ignore[method-assign]
    lay_result = await lay.fetch()
    assert lay_result.source == "laystars"
    assert lay_result.success is False

    novi = NovibetScraper()
    novi._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    novi._fetch_json = AsyncMock(return_value={})  # type: ignore[method-assign]
    novi_result = await novi.fetch()
    assert novi_result.source == "novibet"
    assert novi_result.success is False


@pytest.mark.asyncio
@pytest.mark.live
async def test_laystars_live_safe_connection() -> None:
    """Optional live Laystars connectivity check with hard timeout and graceful skip."""
    if os.environ.get("RUN_LIVE_SCRAPER_TESTS", "0") != "1":
        pytest.skip("Set RUN_LIVE_SCRAPER_TESTS=1 to run live scraper tests.")

    scraper = LaystarsScraper()
    cookies = os.environ.get("LAYSTARS_COOKIES", "").strip()
    if not cookies:
        pytest.skip("LAYSTARS_COOKIES not set; live Laystars test skipped.")

    scraper._cookies = cookies
    try:
        result = await asyncio.wait_for(scraper.fetch(), timeout=30)
    except asyncio.TimeoutError:
        pytest.fail("Laystars live test timed out after 30s")

    print(f"[live] Laystars success={result.success} entries={len(result.entries)} error={result.error}")
    assert result.source == "laystars"
    assert isinstance(result.success, bool)


@pytest.mark.asyncio
@pytest.mark.live
async def test_novibet_live_safe_connection() -> None:
    """Optional live Novibet connectivity check with timeout and cleanup."""
    if os.environ.get("RUN_LIVE_SCRAPER_TESTS", "0") != "1":
        pytest.skip("Set RUN_LIVE_SCRAPER_TESTS=1 to run live scraper tests.")

    scraper = NovibetScraper()
    try:
        result = await asyncio.wait_for(scraper.fetch(), timeout=40)
    except asyncio.TimeoutError:
        pytest.fail("Novibet live test timed out after 40s")
    finally:
        try:
            await scraper.cleanup()
        except Exception:
            pass

    print(f"[live] Novibet success={result.success} entries={len(result.entries)} error={result.error}")
    assert result.source == "novibet"
    assert isinstance(result.success, bool)
