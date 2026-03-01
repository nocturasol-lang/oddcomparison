#!/usr/bin/env python3.12
"""Unit tests for scraper.laystars.LaystarsScraper."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from scraper.laystars import LaystarsScraper


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"liveEvents": [1, 2, 3]}, ["1", "2", "3"]),
        ([{"eventId": 44}, {"id": 45}], ["44", "45"]),
        ({"soccer": [{"eventId": 99}, {"id": 100}]}, ["99", "100"]),
        ([777, 888], ["777", "888"]),
    ],
)
def test_extract_event_ids_different_json_formats(payload, expected) -> None:
    ids = LaystarsScraper._extract_event_ids(payload)
    assert ids == expected


def test_parse_lay_ladder_full_and_missing() -> None:
    runner_full = [
        None,
        "Home",
        None,
        [
            [{"price": 2.4, "size": 40}],
            [{"price": 2.5, "size": 35}, {"price": 2.52, "size": 20}, {"price": 2.56, "size": 10}],
        ],
    ]
    assert LaystarsScraper._parse_lay_ladder(runner_full) == (2.5, 2.52, 2.56, 35.0)
    assert LaystarsScraper._parse_lay_ladder([None, "Home", None, []]) == (0.0, 0.0, 0.0, 0.0)


def test_extract_game_time_from_timestamp_and_fallback() -> None:
    now = datetime.now(timezone.utc)
    ts_ms = 1_700_000_000_000
    market_with_ts = [ts_ms, None, None]
    parsed = LaystarsScraper._extract_game_time(market_with_ts, now)
    assert parsed.count("-") == 1
    assert parsed.count(":") == 1

    market_without_ts = ["bad", {"startTime": "not-a-date"}]
    fallback = LaystarsScraper._extract_game_time(market_without_ts, now)
    assert fallback == now.strftime("%d-%m %H:%M")


@pytest.mark.asyncio
async def test_fetch_returns_error_without_cookies() -> None:
    scraper = LaystarsScraper()
    result = await scraper.fetch()
    assert result.success is False
    assert result.error == "no_cookies_configured"


@pytest.mark.asyncio
async def test_fetch_uses_manual_event_ids_when_discovery_empty() -> None:
    scraper = LaystarsScraper()
    scraper._cookies = "session=test"
    scraper.event_ids = ["manual-1"]

    fake_entry = []
    mock_fetch_event = AsyncMock(return_value=(fake_entry, {"MATCH_ODDS"}, {"MATCH_ODDS"}))
    scraper._get_live_event_ids = AsyncMock(return_value=[])  # type: ignore[method-assign]
    scraper._fetch_event_odds = mock_fetch_event  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.error is not None
    assert "parsed_zero_entries" in result.error
    mock_fetch_event.assert_awaited()


@pytest.mark.asyncio
async def test_fetch_handles_event_errors_gracefully() -> None:
    scraper = LaystarsScraper()
    scraper._cookies = "session=test"
    scraper._get_live_event_ids = AsyncMock(return_value=["evt-1"])  # type: ignore[method-assign]
    scraper._fetch_event_odds = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    result = await scraper.fetch()
    assert result.success is False
    assert result.error is not None
    assert "parsed_zero_entries" in result.error
