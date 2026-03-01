#!/usr/bin/env python3.12
"""Unit tests for scraper.base.BaseScraper helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from models import ScraperResult
from scraper.base import BaseScraper


class DummyScraper(BaseScraper):
    async def fetch(self) -> ScraperResult:  # pragma: no cover - not used in these tests
        return ScraperResult(
            source="dummy",
            entries=[],
            scraped_at=datetime.now(timezone.utc),
            success=True,
            error=None,
        )


def test_normalize_team_name_removes_accents_and_suffixes() -> None:
    s = DummyScraper()
    assert s.normalize_team_name("Málaga FC") == "malaga"
    assert s.normalize_team_name("Panathinaikos") == "panathinaikos"


def test_normalize_market_maps_known_aliases() -> None:
    s = DummyScraper()
    assert s.normalize_market("1x2") == "MATCH_ODDS"
    assert s.normalize_market("over/under 2.5") == "GOALS_25"


def test_make_game_id_uses_normalized_fields() -> None:
    s = DummyScraper()
    game_id = s.make_game_id("Málaga FC", "AEK", "1x2", "Home")
    assert game_id.startswith("malaga_vs_aek_MATCH_ODDS_home")


def test_fuzzy_match_game_handles_empty_values() -> None:
    s = DummyScraper()
    assert s.fuzzy_match_game("", "abc") == 0.0
    assert s.fuzzy_match_game("abc", "abc") == 100.0


def test_market_allowlist() -> None:
    assert BaseScraper.is_market_allowed("MATCH_ODDS", "soccer") is True
    assert BaseScraper.is_market_allowed("CORNERS", "soccer") is False
