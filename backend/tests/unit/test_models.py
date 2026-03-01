#!/usr/bin/env python3.12
"""Unit tests for Pydantic models in models.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from models import OddsDelta, OddsEntry, ScraperResult


def test_odds_entry_validation_success() -> None:
    entry = OddsEntry(
        game_id="team_a_vs_team_b_MATCH_ODDS_home",
        game_time="01-03 20:00",
        game_name="Team A v Team B",
        market="MATCH_ODDS",
        selection="Home",
        bookmaker="novibet",
        back_odds=2.2,
        lay_odds=2.1,
        lay_available=120.0,
        ls1=2.1,
        ls2=2.12,
        ls3=2.14,
        diff=0.1,
        is_value=True,
        updated_at=datetime.now(timezone.utc),
    )
    assert entry.market == "MATCH_ODDS"
    assert entry.is_value is True


def test_odds_entry_validation_fails_with_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        OddsEntry(
            game_id="bad",
            game_time="01-03 20:00",
            game_name="Team A v Team B",
            market="MATCH_ODDS",
            selection="Home",
            bookmaker="novibet",
            back_odds=2.2,
            lay_odds=2.1,
            lay_available=120.0,
            ls1=2.1,
            ls2=2.12,
            ls3=2.14,
            diff=0.1,
            is_value=True,
            # updated_at intentionally omitted
        )


def test_scraper_result_serialization_round_trip(sample_odds_entry) -> None:
    result = ScraperResult(
        source="novibet",
        entries=[sample_odds_entry],
        scraped_at=datetime.now(timezone.utc),
        success=True,
        error=None,
    )
    as_json = result.model_dump_json()
    restored = ScraperResult.model_validate_json(as_json)
    assert restored.source == "novibet"
    assert len(restored.entries) == 1
    assert restored.entries[0].game_id == sample_odds_entry.game_id


def test_odds_delta_defaults_and_deserialization(sample_odds_entry) -> None:
    payload = {
        "changed": [sample_odds_entry.model_dump(mode="json")],
        "removed": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    delta = OddsDelta.model_validate(payload)
    assert delta.type == "delta"
    assert len(delta.changed) == 1
    assert delta.changed[0].bookmaker == "novibet"
