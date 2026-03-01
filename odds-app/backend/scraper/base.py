"""
Base scraper interface. All bookmaker scrapers (novibet, stoiximan, etc.) inherit from this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import unicodedata

from rapidfuzz import fuzz

from models import ScraperResult
from core.market_allowlist import SOCCER_ALLOWED


class BaseScraper(ABC):
    """Abstract base. Subclasses implement fetch() and parse their HTML/API into OddsEntry list."""

    @abstractmethod
    async def fetch(self) -> ScraperResult:
        """Load and parse odds. Return ScraperResult with entries or error."""
        ...

    @staticmethod
    def is_market_allowed(market_std: str) -> bool:
        """Return True only if market_std is in the soccer allowlist."""
        return market_std in SOCCER_ALLOWED

    def normalize_team_name(self, name: str) -> str:
        """Lowercase, remove accents (unicodedata), strip common suffixes, Greek abbreviations."""
        if not name or not name.strip():
            return ""
        s = unicodedata.normalize("NFD", name.strip().lower())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        for suffix in (" fc", " sk", " fk", " cf"):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                break
        greek = {"panathinaikos": "panathinaikos", "olympiakos": "olympiakos", "aek": "aek"}
        return greek.get(s, s)

    def normalize_market(self, market: str) -> str:
        """Map common variants to standard names."""
        if not market or not market.strip():
            return ""
        m = market.strip().lower()
        mapping = {
            "1x2": "MATCH_ODDS",
            "match odds": "MATCH_ODDS",
            "over/under 1.5 first half": "FIRST_HALF_GOALS_15",
            "over/under 2.5": "GOALS_25",
            "double chance": "DOUBLE_CHANCE",
            "draw no bet": "DRAW_NO_BET",
        }
        return mapping.get(m, m)

    def make_game_id(self, home: str, away: str, market: str, selection: str) -> str:
        """Normalize all parts then join with '_'."""
        h = self.normalize_team_name(home)
        a = self.normalize_team_name(away)
        m = self.normalize_market(market)
        sel = selection.strip().lower() if selection else ""
        return f"{h}_vs_{a}_{m}_{sel}"

    def fuzzy_match_game(self, name1: str, name2: str) -> float:
        """Return similarity 0-100 using rapidfuzz.fuzz.ratio."""
        if not name1 or not name2:
            return 0.0
        return float(fuzz.ratio(name1, name2))
