"""
Single source of truth for allowed market types per sport.

Scrapers map raw market codes to these canonical names before creating OddsEntry.
Markets that don't map or aren't in the allowlist are dropped (corners, cards, etc.).
"""

from __future__ import annotations

# Standard soccer market names (aligned with base.normalize_market / laystars MARKET_MAP)
SOCCER_ALLOWED: set[str] = {
    "MATCH_ODDS",
    "GOALS_25",
    "GOALS_OVER_UNDER",
    "DOUBLE_CHANCE",
    "FIRST_HALF_RESULT",
    "FIRST_HALF_GOALS_15",
    "FIRST_HALF_OVER_UNDER",
    "BOTH_TEAMS_TO_SCORE",
    "DRAW_NO_BET",
}

# Placeholders for future expansion
TENNIS_ALLOWED: set[str] = set()
BASKETBALL_ALLOWED: set[str] = set()

_SPORT_ALLOWLISTS: dict[str, set[str]] = {
    "soccer": SOCCER_ALLOWED,
    "tennis": TENNIS_ALLOWED,
    "basketball": BASKETBALL_ALLOWED,
}


def is_market_allowed(sport: str, market: str) -> bool:
    """Return True if the market is in the allowlist for the given sport."""
    if not sport or not market:
        return False
    allowlist = _SPORT_ALLOWLISTS.get(sport.strip().lower())
    if allowlist is None:
        return False
    return market.strip() in allowlist
