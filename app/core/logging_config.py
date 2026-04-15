"""
Centralized logging configuration.

Sets up:
  - JSON formatter for all stdout output (compatible with log aggregators)
  - EventLogHandler: persists WARNING+ Python log records to the event_logs table

Usage:
    from app.core.logging_config import configure_logging
    configure_logging()          # call once at startup (FastAPI lifespan + Celery worker)
"""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON for stdout aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# EventLog handler — persists WARNING+ records to the DB
# ---------------------------------------------------------------------------

_LEVEL_MAP = {
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}

# Loggers that must never feed back into EventLogHandler (prevent recursion)
_BLOCKED_LOGGERS = {
    "app.services.event_log_service",
    "sqlalchemy",
    "sqlalchemy.engine",
    "asyncio",
}


class EventLogHandler(logging.Handler):
    """
    Persists WARNING / ERROR / CRITICAL Python log records to event_logs.

    Works in both FastAPI (asyncio event loop already running) and
    Celery workers (loop running in a background thread via asyncio.run).

    Thread-safety: emit() is called from arbitrary threads; we schedule
    the coroutine onto the running event loop without blocking the caller.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(level=logging.WARNING)
        self._loop = loop  # can be set explicitly; auto-detected otherwise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

    @staticmethod
    async def _persist(event_type: str, level: str, message: str, details: dict) -> None:
        """Fire-and-forget coroutine — opens its own DB session via log_event(None, ...)."""
        from app.services.event_log_service import log_event
        await log_event(None, event_type, message, level=level, details=details)

    # ------------------------------------------------------------------
    # logging.Handler interface
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        # Never recurse into blocked loggers
        if record.name in _BLOCKED_LOGGERS or record.name.startswith("sqlalchemy"):
            return

        level_str = _LEVEL_MAP.get(record.levelno, "warning")
        event_type = f"python.log.{level_str}"
        message = self.format(record)

        details: dict[str, Any] = {
            "logger": record.name,
            "module": record.module,
            "lineno": record.lineno,
            "func": record.funcName,
        }
        if record.exc_info:
            details["traceback"] = "".join(traceback.format_exception(*record.exc_info))

        loop = self._get_loop()
        if loop is None or loop.is_closed():
            # No event loop available — skip silently rather than crash
            return

        coro = self._persist(event_type, level_str, message, details)

        if loop.is_running():
            # FastAPI / Celery async worker: schedule without blocking
            loop.call_soon_threadsafe(lambda: loop.create_task(coro))
        else:
            # Sync context with a stopped loop — best-effort run
            try:
                loop.run_until_complete(coro)
            except Exception:
                pass  # never raise from a logging handler


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------

def configure_logging(
    log_level: str = "INFO",
    *,
    enable_event_log_handler: bool = True,
    event_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """
    Configure root logger with JSON stdout output and (optionally) DB handler.

    Call once at application startup:
      - FastAPI: inside lifespan()
      - Celery: at module level in celery_app.py or via worker_init signal

    Args:
        log_level: Root log level string (e.g. "INFO", "DEBUG").
        enable_event_log_handler: Whether to attach EventLogHandler.
            Set to False when DB is not yet available (e.g. during migrations).
        event_loop: Explicit event loop for EventLogHandler. If None,
            asyncio.get_event_loop() is used at emit() time.
    """
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_JsonFormatter())

    handlers: list[logging.Handler] = [stdout_handler]

    if enable_event_log_handler:
        handlers.append(EventLogHandler(loop=event_loop))

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=handlers,
        force=True,  # override any existing root-logger config
    )

    # Reduce noise from chatty third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
