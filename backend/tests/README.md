# ODDSHAWK Backend Test Suite

This test suite is organized to prioritize the Laystars critical path first, then core comparison/model logic, and finally integration behavior (orchestrator/WebSocket/live checks).

## Structure

- `tests/unit/`
  - `test_comparator.py`
  - `test_models.py`
  - `test_calculator.py`
- `tests/scrapers/`
  - `test_base.py`
  - `test_laystars.py`
  - `test_novibet.py`
- `tests/integration/`
  - `test_orchestrator.py`
  - `test_scrapers.py`
  - `test_websocket.py`
- `tests/scripts/`
  - `debug_laystars.py`
  - `test_redis.py`
  - `run_all_diagnostics.py`

## Prerequisites

- Python `3.12`
- Backend dependencies installed (`pip install -r requirements.txt`)
- Optional for live diagnostics/tests:
  - Valid `LAYSTARS_COOKIES` (either in env var or `config.py`)
  - Redis running and reachable by `REDIS_URL` (default `redis://localhost:6379/0`)

## How To Run Tests

Run from `odds-app/backend`:

- Full suite:
  - `python3.12 -m pytest tests -v`
- Laystars-focused tests first:
  - `python3.12 -m pytest tests/scrapers/test_laystars.py -v`
- Unit tests:
  - `python3.12 -m pytest tests/unit -v`
- Integration tests (mocked-safe by default):
  - `python3.12 -m pytest tests/integration -v`
- Live scraper integration tests:
  - Linux/macOS: `RUN_LIVE_SCRAPER_TESTS=1 python3.12 -m pytest tests/integration/test_scrapers.py -m live -v`
  - PowerShell: `$env:RUN_LIVE_SCRAPER_TESTS="1"; python3.12 -m pytest tests/integration/test_scrapers.py -m live -v`

## Diagnostic Scripts

All scripts are module-runnable:

- Laystars live diagnostic:
  - `python3.12 -m tests.scripts.debug_laystars`
- Redis diagnostic:
  - `python3.12 -m tests.scripts.test_redis`
- Run all diagnostics:
  - `python3.12 -m tests.scripts.run_all_diagnostics`

## Expected Output

- Unit/scraper tests:
  - `PASSED` for deterministic logic and mocked integrations.
- Live scraper tests:
  - May `SKIP` if `RUN_LIVE_SCRAPER_TESTS` or cookies are not configured.
  - If enabled, should complete within timeout and print live status details.
- Diagnostics scripts:
  - Exit code `0` when successful.
  - Clear `[INFO]`, `[PASS]`, `[WARN]`, `[ERROR]` lines with actionable hints.
