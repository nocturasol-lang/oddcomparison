#!/usr/bin/env python3.12
"""Unit tests for calculator.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from calculator import (
    arbitrage_percentage,
    arbitrage_profit_percent,
    arbitrage_stakes,
    expected_roi_decimal,
    kelly_fraction,
    kelly_stake,
    lay_liability,
    lay_stake_from_liability,
    minimum_guaranteed_profit,
    roi_decimal,
    roi_percent,
    roi_single_bet,
)


def test_kelly_stake_positive_edge() -> None:
    stake = kelly_stake(decimal_odds=2.5, estimated_probability=0.5, bankroll=1000, fraction=0.5)
    assert stake > Decimal("0")


def test_kelly_fraction_no_edge_returns_zero() -> None:
    assert kelly_fraction(decimal_odds=1.5, estimated_probability=0.4) == Decimal("0")


def test_arbitrage_percentage_and_profit() -> None:
    total = arbitrage_percentage(2.2, 2.2)
    assert total < Decimal("1")
    profit = arbitrage_profit_percent(2.2, 2.2)
    assert profit is not None
    assert profit > Decimal("0")


def test_arbitrage_stakes_and_minimum_profit() -> None:
    stakes = arbitrage_stakes(100, 2.2, 2.2)
    assert stakes is not None
    assert len(stakes) == 2
    guaranteed = minimum_guaranteed_profit(100, 2.2, 2.2)
    assert guaranteed is not None
    assert guaranteed > Decimal("0")


def test_arbitrage_stakes_returns_none_without_arb() -> None:
    assert arbitrage_stakes(100, 1.8, 1.8) is None


def test_lay_liability_and_inverse_stake() -> None:
    liability = lay_liability(stake=25, lay_odds=3.0)
    assert liability == Decimal("50.000000")
    stake = lay_stake_from_liability(liability=50, lay_odds=3.0)
    assert stake == Decimal("25.000000")


def test_roi_calculations() -> None:
    assert roi_decimal(10, 100) == Decimal("0.100000")
    assert roi_percent(10, 100) == Decimal("10.000000")
    assert roi_single_bet(stake=10, decimal_odds=2.0, won=False) == Decimal("-1")
    assert expected_roi_decimal(decimal_odds=2.0, estimated_probability=0.6) == Decimal("0.200000")
