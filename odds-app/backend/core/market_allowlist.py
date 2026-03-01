"""
Single source of truth for allowed soccer market types.

Every scraper maps its raw market codes to one of these canonical SOCCER_*
names before creating OddsEntry rows.  Markets that don't map are silently
dropped — this is how corners, cards, specials, etc. are excluded.
"""

from __future__ import annotations

SOCCER_ALLOWED: set[str] = {
    "SOCCER_MATCH_RESULT",
    "SOCCER_UNDER_OVER",
    "SOCCER_DOUBLE_CHANCE",
    "SOCCER_FIRST_HALF_RESULT",
    "SOCCER_BOTH_TEAMS_TO_SCORE",
    "SOCCER_FIRST_HALF_UNDER_OVER",
}


def map_laystars_market_code(code: str, name: str) -> str | None:
    """Map a Laystars market_code (array index 9) to a canonical SOCCER_* name.

    ``name`` (array index 10) is available as a fallback hint but the primary
    dispatch is on ``code``.  Returns *None* for any market we don't want
    (corners, cards, specials, etc.).
    """
    c = code.upper().strip()

    if c == "MATCH_ODDS":
        return "SOCCER_MATCH_RESULT"

    if c == "BOTH_TEAMS_TO_SCORE":
        return "SOCCER_BOTH_TEAMS_TO_SCORE"

    if c == "DOUBLE_CHANCE":
        return "SOCCER_DOUBLE_CHANCE"

    is_first_half = "FIRST_HALF" in c or c.startswith("HALF_TIME")

    if c.startswith("OVER_UNDER_"):
        if is_first_half:
            return "SOCCER_FIRST_HALF_UNDER_OVER"
        return "SOCCER_UNDER_OVER"

    if is_first_half:
        if any(kw in c for kw in ("GOALS", "OVER", "UNDER")):
            return "SOCCER_FIRST_HALF_UNDER_OVER"
        return "SOCCER_FIRST_HALF_RESULT"

    return None
