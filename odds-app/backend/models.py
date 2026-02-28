from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class OddsEntry(BaseModel):
    game_id: str           # unique key: "{home}_vs_{away}_{market}_{selection}"
    game_time: str         # "28-02 19:30"
    game_name: str         # "Dortmund v Bayern Munich"
    market: str            # "MATCH_ODDS", "FIRST_HALF_GOALS_15", etc
    selection: str         # "FC Bayern Munchen", "Over 1.5 Goals", etc
    bookmaker: str         # "novibet", "stoiximan", "betshop"
    back_odds: float       # bookmaker decimal odds
    lay_odds: float        # lay odds from laystars888
    lay_available: float   # liquidity available at that lay price
    ls1: float             # laystars best lay 1
    ls2: float             # laystars best lay 2
    ls3: float             # laystars best lay 3
    diff: float            # back_odds - lay_odds (negative = value)
    is_value: bool         # True when back_odds >= lay_odds (show RED)
    updated_at: datetime


class ScraperResult(BaseModel):
    source: str                    # "novibet", "laystars", etc
    entries: list[OddsEntry]
    scraped_at: datetime
    success: bool
    error: str | None = None


class OddsDelta(BaseModel):
    type: str = "delta"            # "delta" or "full"
    changed: list[OddsEntry]
    removed: list[str]             # game_ids that disappeared
    timestamp: datetime
