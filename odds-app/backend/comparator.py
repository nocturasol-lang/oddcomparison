"""
Merge bookmaker odds with laystars exchange data and detect changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from rapidfuzz import fuzz

from models import OddsEntry, ScraperResult, OddsDelta


def _fuzzy_match_game_id(game_id1: str, game_id2: str, threshold: int = 80) -> bool:
    """True if similarity >= threshold."""
    if not game_id1 or not game_id2:
        return False
    return fuzz.ratio(game_id1, game_id2) >= threshold


class OddsComparator:
    """Merge bookmaker + laystars results and compute value / changes."""

    def merge(
        self,
        bookmaker_results: list[ScraperResult],
        laystars_result: ScraperResult,
    ) -> list[OddsEntry]:
        """
        Merge all bookmaker entries with laystars lay data.
        - Build dict of laystars entries keyed by game_id.
        - For each bookmaker entry: exact game_id match first, else fuzzy (>= 80).
        - Fill lay_odds, ls1, ls2, ls3, lay_available from match.
        - diff = back_odds - lay_odds; is_value = back_odds >= lay_odds.
        - No match -> keep entry with is_value=False.
        """
        lay_by_id: dict[str, OddsEntry] = {}
        for e in laystars_result.entries:
            lay_by_id[e.game_id] = e

        merged: list[OddsEntry] = []

        for result in bookmaker_results:
            if not result.entries:
                continue
            for entry in result.entries:
                gid = entry.game_id
                lay = lay_by_id.get(gid)

                if lay is None:
                    # Fuzzy match: find best lay entry with ratio >= 80
                    for lid, lay_cand in lay_by_id.items():
                        if _fuzzy_match_game_id(gid, lid, 80):
                            lay = lay_cand
                            break

                if lay is not None:
                    merged.append(
                        entry.model_copy(
                            update={
                                "lay_odds": lay.lay_odds,
                                "lay_available": lay.lay_available,
                                "ls1": lay.ls1,
                                "ls2": lay.ls2,
                                "ls3": lay.ls3,
                                "diff": entry.back_odds - lay.lay_odds,
                                "is_value": entry.back_odds >= lay.lay_odds,
                            }
                        )
                    )
                else:
                    merged.append(
                        entry.model_copy(
                            update={
                                "diff": entry.back_odds,  # no lay -> diff = back
                                "is_value": False,
                            }
                        )
                    )

        return merged

    def get_changes(
        self,
        old: list[OddsEntry],
        new: list[OddsEntry],
    ) -> OddsDelta:
        """
        Compare old vs new by game_id.
        changed = entries where back_odds, lay_odds, or is_value changed.
        removed = game_ids in old but not in new.
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
        """
        Sort by is_value DESC (value rows first), then game_time ASC.
        Round all floats to 2 decimal places.
        """
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
