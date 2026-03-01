#!/usr/bin/env python3.12
"""Unit tests for comparator.py."""

from __future__ import annotations

from datetime import timedelta

import pytest

from comparator import OddsComparator, _is_inplay
from models import ScraperResult


def test_merge_detects_value_exact_match(sample_odds_entry, sample_lay_entry) -> None:
    comp = OddsComparator()
    bookmaker = ScraperResult(
        source="novibet",
        entries=[sample_odds_entry.model_copy(update={"back_odds": 2.3})],
        scraped_at=sample_odds_entry.updated_at,
        success=True,
        error=None,
    )
    lay = ScraperResult(
        source="laystars",
        entries=[sample_lay_entry.model_copy(update={"lay_odds": 2.0})],
        scraped_at=sample_lay_entry.updated_at,
        success=True,
        error=None,
    )

    merged = comp.merge([bookmaker], lay)
    assert len(merged) == 1
    assert merged[0].diff == pytest.approx(0.3)
    assert merged[0].is_value is True


def test_merge_skips_stale_rows(monkeypatch, sample_odds_entry, sample_lay_entry) -> None:
    monkeypatch.setattr("comparator.STALENESS_PREMATCH_SEC", 1.0)
    comp = OddsComparator()

    stale_book = sample_odds_entry.model_copy(
        update={"updated_at": sample_odds_entry.updated_at + timedelta(seconds=5)}
    )
    bookmaker = ScraperResult(
        source="novibet",
        entries=[stale_book],
        scraped_at=stale_book.updated_at,
        success=True,
        error=None,
    )
    lay = ScraperResult(
        source="laystars",
        entries=[sample_lay_entry],
        scraped_at=sample_lay_entry.updated_at,
        success=True,
        error=None,
    )
    assert comp.merge([bookmaker], lay) == []


def test_merge_uses_fuzzy_matching(sample_odds_entry, sample_lay_entry) -> None:
    comp = OddsComparator()

    bookmaker_entry = sample_odds_entry.model_copy(
        update={"game_name": "Dortmund vs Bayern", "selection": "HOME"}
    )
    lay_entry = sample_lay_entry.model_copy(
        update={"game_name": "Dortmund v Bayern Munich", "selection": "home"}
    )
    bookmaker = ScraperResult(
        source="novibet",
        entries=[bookmaker_entry],
        scraped_at=bookmaker_entry.updated_at,
        success=True,
        error=None,
    )
    lay = ScraperResult(
        source="laystars",
        entries=[lay_entry],
        scraped_at=lay_entry.updated_at,
        success=True,
        error=None,
    )

    merged = comp.merge([bookmaker], lay)
    assert len(merged) == 1
    assert merged[0].lay_odds == lay_entry.lay_odds


def test_merge_skips_zero_back_or_lay(sample_odds_entry, sample_lay_entry) -> None:
    comp = OddsComparator()
    bookmaker = ScraperResult(
        source="novibet",
        entries=[sample_odds_entry.model_copy(update={"back_odds": 0.0})],
        scraped_at=sample_odds_entry.updated_at,
        success=True,
        error=None,
    )
    lay = ScraperResult(
        source="laystars",
        entries=[sample_lay_entry.model_copy(update={"lay_odds": 0.0})],
        scraped_at=sample_lay_entry.updated_at,
        success=True,
        error=None,
    )
    assert comp.merge([bookmaker], lay) == []


def test_is_inplay_detection(sample_odds_entry) -> None:
    inplay = sample_odds_entry.model_copy(update={"market": "FIRST_HALF_GOALS_15"})
    prematch = sample_odds_entry.model_copy(update={"market": "MATCH_ODDS"})
    assert _is_inplay(inplay) is True
    assert _is_inplay(prematch) is False


def test_get_changes_detects_updates_and_removals(sample_odds_entry) -> None:
    comp = OddsComparator()
    old = [sample_odds_entry]
    new = [sample_odds_entry.model_copy(update={"back_odds": 2.5, "diff": 0.42})]
    delta = comp.get_changes(old, new)
    assert len(delta.changed) == 1
    assert delta.changed[0].back_odds == pytest.approx(2.5)
    assert delta.removed == []

    delta_removed = comp.get_changes(old, [])
    assert delta_removed.changed == []
    assert delta_removed.removed == [sample_odds_entry.game_id]


def test_normalize_for_display_sorts_and_rounds(make_odds_entry) -> None:
    comp = OddsComparator()
    e1 = make_odds_entry(
        game_id="b",
        is_value=False,
        game_time="02-03 20:00",
        back_odds=2.1111,
        lay_odds=2.0099,
    )
    e2 = make_odds_entry(
        game_id="a",
        is_value=True,
        game_time="01-03 20:00",
        back_odds=2.5555,
        lay_odds=2.1111,
    )

    out = comp.normalize_for_display([e1, e2])
    assert [entry.game_id for entry in out] == ["a", "b"]
    assert out[0].back_odds == 2.56
    assert out[0].lay_odds == 2.11
