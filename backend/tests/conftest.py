#!/usr/bin/env python3.12
"""Shared pytest fixtures for ODDSHAWK backend tests."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure backend root is on path when running tests.
_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: async test")
    config.addinivalue_line("markers", "live: test requires external services")


@pytest.fixture
def sample_updated_at() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def make_odds_entry(sample_updated_at):
    """Factory for OddsEntry with sensible defaults."""
    from models import OddsEntry

    def _make(**overrides) -> OddsEntry:
        payload = {
            "game_id": "dortmund_vs_bayern_MATCH_ODDS_home",
            "game_time": "01-03 20:00",
            "game_name": "Dortmund v Bayern Munich",
            "market": "MATCH_ODDS",
            "selection": "Home",
            "bookmaker": "novibet",
            "back_odds": 2.10,
            "lay_odds": 2.08,
            "lay_available": 100.0,
            "ls1": 2.08,
            "ls2": 2.10,
            "ls3": 2.12,
            "diff": 0.02,
            "is_value": True,
            "updated_at": sample_updated_at,
        }
        payload.update(overrides)
        return OddsEntry(**payload)

    return _make


@pytest.fixture
def sample_odds_entry(make_odds_entry):
    return make_odds_entry(bookmaker="novibet")


@pytest.fixture
def sample_lay_entry(make_odds_entry):
    return make_odds_entry(
        bookmaker="laystars",
        back_odds=0.0,
        lay_odds=2.08,
        diff=0.0,
        is_value=False,
    )


@pytest.fixture
def sample_scraper_result(sample_odds_entry):
    from models import ScraperResult

    return ScraperResult(
        source="novibet",
        entries=[sample_odds_entry],
        scraped_at=datetime.now(timezone.utc),
        success=True,
        error=None,
    )


@pytest.fixture
def sample_laystars_result(sample_lay_entry):
    from models import ScraperResult

    return ScraperResult(
        source="laystars",
        entries=[sample_lay_entry],
        scraped_at=datetime.now(timezone.utc),
        success=True,
        error=None,
    )


@pytest.fixture
def mock_config_env(monkeypatch: pytest.MonkeyPatch):
    """Set stable env values used by comparator/orchestrator logic."""
    monkeypatch.setenv("STALENESS_PREMATCH_SEC", "15")
    monkeypatch.setenv("STALENESS_INPLAY_SEC", "8")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "100")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", "60")
    yield
    for key in (
        "STALENESS_PREMATCH_SEC",
        "STALENESS_INPLAY_SEC",
        "RATE_LIMIT_REQUESTS",
        "RATE_LIMIT_WINDOW_SEC",
    ):
        os.environ.pop(key, None)
