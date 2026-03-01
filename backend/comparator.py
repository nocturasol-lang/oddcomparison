"""
Merge bookmaker odds with laystars exchange data and detect changes.

Matching key: (normalized_game_name, market, selection)
  - exact first, then fuzzy (>=80) on game portion with exact market+selection.
Staleness:  adaptive — pre-match 15s, in-play 8s (configurable via env).
  In-play detected via market type (HALF, LIVE indicators).
Skip:       rows where back_odds == 0 or lay_odds == 0 after merge are dropped.
Value rule: diff = back - lay; is_value = diff > 0  (positive diff → RED row).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from rapidfuzz import fuzz

from models import OddsEntry, ScraperResult, OddsDelta

log = logging.getLogger(__name__)

FUZZY_THRESHOLD = 80

# Adaptive staleness: pre-match vs in-play (seconds). Configurable via env.
def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


STALENESS_PREMATCH_SEC = _float_env("STALENESS_PREMATCH_SEC", 15.0)
STALENESS_INPLAY_SEC = _float_env("STALENESS_INPLAY_SEC", 8.0)

# Market / game name substrings that indicate in-play (tighter staleness).
INPLAY_INDICATORS = ("HALF", "LIVE", "IN_PLAY", "INPLAY", "FIRST_HALF", "HALF_TIME")


def _norm_game(entry: OddsEntry) -> str:
    return entry.game_name.strip().lower()


def _norm_sel(entry: OddsEntry) -> str:
    return entry.selection.strip().lower()


def _is_inplay(entry: OddsEntry) -> bool:
    """True if market or game name suggests in-play (use tighter staleness)."""
    market_upper = (entry.market or "").upper()
    game_upper = (entry.game_name or "").upper()
    for indicator in INPLAY_INDICATORS:
        if indicator in market_upper or indicator in game_upper:
            return True
    return False


def _match_key(entry: OddsEntry) -> tuple[str, str, str]:
    """(normalized_game, market, normalized_selection) — exact composite key."""
    return (_norm_game(entry), entry.market, _norm_sel(entry))


class OddsComparator:
    """Merge bookmaker + laystars results and compute value / changes."""

    def merge(
        self,
        bookmaker_results: list[ScraperResult],
        laystars_result: ScraperResult,
    ) -> list[OddsEntry]:
        """Merge bookmaker entries with laystars lay data.

        1. Build lay-side index keyed by (game, market, selection).
        2. For each bookmaker row (back_odds > 0):
           a. Exact key lookup, else fuzzy on game (>= 80) with exact market+selection.
           b. Skip if no lay match or lay_odds == 0.
           c. Skip if staleness exceeds threshold (pre-match 15s, in-play 8s; configurable via env).
           d. Compute diff / is_value, emit merged row.
        """
        lay_exact: dict[tuple[str, str, str], OddsEntry] = {}
        lay_by_market_sel: dict[tuple[str, str], list[OddsEntry]] = {}

        for e in laystars_result.entries:
            key = _match_key(e)
            lay_exact[key] = e
            ms = (e.market, _norm_sel(e))
            lay_by_market_sel.setdefault(ms, []).append(e)

        merged: list[OddsEntry] = []

        for result in bookmaker_results:
            if not result.entries:
                continue
            for entry in result.entries:
                if entry.back_odds <= 0:
                    continue

                lay = lay_exact.get(_match_key(entry))

                if lay is None:
                    ms = (entry.market, _norm_sel(entry))
                    candidates = lay_by_market_sel.get(ms, [])
                    if candidates:
                        book_game = _norm_game(entry)
                        best_ratio = 0.0
                        best_lay: OddsEntry | None = None
                        for cand in candidates:
                            ratio = fuzz.ratio(book_game, _norm_game(cand))
                            if ratio >= FUZZY_THRESHOLD and ratio > best_ratio:
                                best_ratio = ratio
                                best_lay = cand
                        lay = best_lay

                if lay is None or lay.lay_odds <= 0:
                    continue

                inplay = _is_inplay(entry) or _is_inplay(lay)
                staleness_sec = STALENESS_INPLAY_SEC if inplay else STALENESS_PREMATCH_SEC
                age = abs((entry.updated_at - lay.updated_at).total_seconds())
                if age > staleness_sec:
                    log.debug(
                        "Staleness skip: game_id=%s age=%.1fs threshold=%.1fs in_play=%s",
                        entry.game_id, age, staleness_sec, inplay,
                    )
                    continue

                diff = entry.back_odds - lay.lay_odds

                merged.append(
                    entry.model_copy(
                        update={
                            "lay_odds": lay.lay_odds,
                            "lay_available": lay.lay_available,
                            "ls1": lay.ls1,
                            "ls2": lay.ls2,
                            "ls3": lay.ls3,
                            "diff": diff,
                            "is_value": diff > 0,
                        }
                    )
                )

        return merged

    def get_changes(
        self,
        old: list[OddsEntry],
        new: list[OddsEntry],
    ) -> OddsDelta:
        """Compare old vs new by game_id.

        changed = entries where back_odds, lay_odds, diff, or is_value changed.
        removed = game_ids in old but not in new.
        Only returns a non-empty delta when something actually moved.
        """
        new_by_id: dict[str, OddsEntry] = {e.game_id: e for e in new}
        old_by_id: dict[str, OddsEntry] = {e.game_id: e for e in old}

        changed: list[OddsEntry] = []
        for gid, new_entry in new_by_id.items():
            old_entry = old_by_id.get(gid)
            if old_entry is None:
                changed.append(new_entry)
                continue
            if (
                old_entry.back_odds != new_entry.back_odds
                or old_entry.lay_odds != new_entry.lay_odds
                or old_entry.diff != new_entry.diff
                or old_entry.is_value != new_entry.is_value
            ):
                changed.append(new_entry)

        removed = [gid for gid in old_by_id if gid not in new_by_id]

        return OddsDelta(
            type="delta",
            changed=changed,
            removed=removed,
            timestamp=datetime.now(timezone.utc),
        )

    def normalize_for_display(self, entries: list[OddsEntry]) -> list[OddsEntry]:
        """Sort by is_value DESC then game_time ASC. Round all floats to 2dp."""
        sorted_entries = sorted(
            entries,
            key=lambda e: (not e.is_value, e.game_time or ""),
        )

        def round_entry(e: OddsEntry) -> OddsEntry:
            return e.model_copy(
                update={
                    "back_odds": round(e.back_odds, 2),
                    "lay_odds": round(e.lay_odds, 2),
                    "lay_available": round(e.lay_available, 2),
                    "ls1": round(e.ls1, 2),
                    "ls2": round(e.ls2, 2),
                    "ls3": round(e.ls3, 2),
                    "diff": round(e.diff, 2),
                }
            )

        return [round_entry(e) for e in sorted_entries]
