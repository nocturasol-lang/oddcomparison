#!/usr/bin/env python3.12
"""Run all diagnostics scripts and print a summary."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


@dataclass(slots=True)
class StepResult:
    name: str
    returncode: int

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def _run_step(module_name: str) -> StepResult:
    print(f"\n[RUN] python -m {module_name}")
    proc = subprocess.run([sys.executable, "-m", module_name], check=False)
    print(f"[DONE] {module_name} -> exit={proc.returncode}")
    return StepResult(name=module_name, returncode=proc.returncode)


def main() -> None:
    modules = [
        "tests.scripts.debug_laystars",
        "tests.scripts.test_redis",
    ]
    results = [_run_step(mod) for mod in modules]

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n=== Diagnostics Summary ===")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"{status:>4} | {r.name} | exit={r.returncode}")

    print(f"\nTotal: {len(results)} | Passed: {passed} | Failed: {failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
