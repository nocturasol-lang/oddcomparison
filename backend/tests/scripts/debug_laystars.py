#!/usr/bin/env python3.12
"""Diagnostic script for live Laystars scraping."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any


def _print_entry(entry: Any, idx: int) -> None:
    print(
        f"  [{idx}] {entry.game_name} | {entry.market} | {entry.selection} | "
        f"lay={entry.lay_odds} avail={entry.lay_available}"
    )


async def _run() -> int:
    try:
        from scraper.laystars import LaystarsScraper
    except Exception as exc:
        print(f"[ERROR] Could not import LaystarsScraper: {exc}")
        return 2

    cookies = os.environ.get("LAYSTARS_COOKIES", "").strip()
    if not cookies:
        try:
            from config import LAYSTARS_COOKIES as configured_cookies

            cookies = (configured_cookies or "").strip()
        except Exception:
            cookies = ""

    if not cookies:
        print("[WARN] No Laystars cookies configured (env or config.py).")
        print("       Set LAYSTARS_COOKIES to run live diagnostics.")
        return 1

    timeout_sec = float(os.environ.get("LAYSTARS_DEBUG_TIMEOUT_SEC", "40"))
    scraper = LaystarsScraper()
    await scraper.set_cookies(cookies)

    print("[INFO] Running Laystars live diagnostic...")
    print(f"[INFO] Cookie length: {len(cookies)} chars")
    print(f"[INFO] Timeout: {timeout_sec:.1f}s")

    try:
        result = await asyncio.wait_for(scraper.fetch(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        print(f"[ERROR] Timed out after {timeout_sec:.1f}s")
        return 1
    except Exception as exc:
        print(f"[ERROR] Scraper crashed: {exc}")
        return 1

    print(
        f"[RESULT] source={result.source} success={result.success} "
        f"entries={len(result.entries)} error={result.error}"
    )
    if result.entries:
        print("[INFO] Sample entries:")
        for idx, entry in enumerate(result.entries[:10], start=1):
            _print_entry(entry, idx)
    else:
        print("[INFO] No entries returned. Possible causes: stale cookies, maintenance, no live events.")

    return 0 if result.success else 1


def main() -> None:
    code = asyncio.run(_run())
    sys.exit(code)


if __name__ == "__main__":
    main()
