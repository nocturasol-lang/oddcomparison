"""
Central logging setup: rotating files, per-module levels, optional JSON, error.log, access log.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path

# Config
LOG_DIR = Path(os.environ.get("LOG_DIR", "logs")).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG = LOG_DIR / "app.log"
ERROR_LOG = LOG_DIR / "error.log"
ACCESS_LOG = LOG_DIR / "access.log"

ROTATE_BYTES = 10 * 1024 * 1024  # 10 MB
ROTATE_BACKUP_COUNT = 5

# Format: timestamp, level, module, function, message
STANDARD_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s | %(message)s"
STANDARD_DATEFMT = "%Y-%m-%d %H:%M:%S"


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON for production."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "function": record.funcName,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, default=str)


def _make_rotating_handler(
    path: Path,
    level: int = logging.DEBUG,
    use_json: bool = False,
) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=ROTATE_BYTES,
        backupCount=ROTATE_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(STANDARD_FMT, STANDARD_DATEFMT))
    return handler


def setup_logging(
    *,
    use_json: bool | None = None,
    level: int | str = logging.INFO,
) -> None:
    """
    Configure logging: rotating app.log, error.log (warnings+), access.log,
    per-module levels, and optional JSON format.

    use_json: If None, reads LOG_JSON env (1/true/yes → True). Set True for production JSON.
    level: Root logger level (default INFO). Pass string e.g. "DEBUG" or int.
    """
    if use_json is None:
        use_json = os.environ.get("LOG_JSON", "").strip().lower() in ("1", "true", "yes")

    root = logging.getLogger()
    root_level = level if isinstance(level, int) else getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(root_level)

    # Remove existing handlers to avoid duplicates on re-init
    for h in root.handlers[:]:
        root.removeHandler(h)

    # ---- App log (all levels) ----
    app_handler = _make_rotating_handler(APP_LOG, logging.DEBUG, use_json)
    root.addHandler(app_handler)

    # ---- Error log (WARNING and above) ----
    error_handler = _make_rotating_handler(ERROR_LOG, logging.WARNING, use_json)
    root.addHandler(error_handler)

    # ---- Per-module levels ----
    module_levels: dict[str, int] = {
        "scraper": logging.DEBUG,
        "scraper.orchestrator": logging.INFO,
        "scraper.laystars": logging.INFO,
        "scraper.novibet": logging.INFO,
        "cache": logging.INFO,
        "uvicorn": logging.INFO,
        "uvicorn.error": logging.INFO,
        "uvicorn.access": logging.WARNING,  # we use our own access log
    }
    for name, lvl in module_levels.items():
        logging.getLogger(name).setLevel(lvl)

    # ---- Access logger (no propagation to root; its own file) ----
    access_logger = logging.getLogger("oddshawk.access")
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False
    access_handler = _make_rotating_handler(ACCESS_LOG, logging.INFO, use_json)
    access_logger.addHandler(access_handler)


def get_access_logger() -> logging.Logger:
    """Return the dedicated access logger for API request logging."""
    return logging.getLogger("oddshawk.access")
