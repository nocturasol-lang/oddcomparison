#!/usr/bin/env python3.12
"""Unit tests for scraper.novibet.NovibetScraper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright.async_api", reason="playwright is required for Novibet tests")

from scraper.novibet import NovibetScraper


def _mock_event(*, price_key: str = "price", price_value: float = 2.1, available: bool = True) -> list[dict]:
    return [
        {
            "betViews": [
                {
                    "competitions": [
                        {
                            "caption": "Test League",
                            "events": [
                                {
                                    "betContextId": "ctx1",
                                    "additionalCaptions": {"competitor1": "Team A", "competitor2": "Team B"},
                                    "liveData": {},
                                    "markets": [
                                        {
                                            "betTypeSysname": "MATCH_ODDS",
                                            "betItems": [
                                                {"caption": "Home", "isAvailable": available, price_key: price_value},
                                            ],
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_fetch_with_mocked_playwright_data() -> None:
    scraper = NovibetScraper()
    scraper._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scraper._fetch_json = AsyncMock(return_value=_mock_event())  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is True
    assert len(result.entries) == 1
    assert result.entries[0].market == "MATCH_ODDS"
    assert result.entries[0].selection == "Home"


@pytest.mark.asyncio
async def test_selection_extraction_accepts_decimal_price() -> None:
    scraper = NovibetScraper()
    scraper._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scraper._fetch_json = AsyncMock(return_value=_mock_event(price_key="decimalPrice", price_value=1.95))  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is True
    assert result.entries[0].back_odds == pytest.approx(1.95)


@pytest.mark.asyncio
async def test_market_parsing_skips_unavailable_or_invalid_odds() -> None:
    scraper = NovibetScraper()
    scraper._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scraper._fetch_json = AsyncMock(return_value=_mock_event(price_value=1.0, available=False))  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is True
    assert result.entries == []


@pytest.mark.asyncio
async def test_fetch_returns_error_on_empty_response() -> None:
    scraper = NovibetScraper()
    scraper._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scraper._fetch_json = AsyncMock(return_value={})  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is False
    assert "Empty response" in (result.error or "")


@pytest.mark.asyncio
async def test_fetch_handles_unexpected_exception() -> None:
    scraper = NovibetScraper()
    scraper._ensure_browser = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scraper._fetch_json = AsyncMock(side_effect=RuntimeError("novibet boom"))  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is False
    assert "novibet boom" in (result.error or "")
