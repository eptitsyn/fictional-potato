"""
Shared helper for writing domain event logs to the database.

Usage (with an existing session):
    await log_event(db, "auth.login.success", "User admin logged in",
                    username="admin", ip_address="1.2.3.4")

Usage (without a session — opens its own):
    await log_event(None, "llm.call", "LLM plan call started",
                    details={"model": "gpt-4o", "job_id": str(job_id)})
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_log import EventLog

logger = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession | None,
    event_type: str,
    message: str,
    *,
    level: str = "info",
    user_id: uuid.UUID | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write one event log row. Never raises — failures are logged to stderr."""
    entry = EventLog(
        event_type=event_type,
        level=level,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        message=message,
        details=details,
    )
    if db is not None:
        try:
            db.add(entry)
            await db.flush()  # write within caller's transaction
        except Exception:
            logger.exception("log_event flush failed (event_type=%s)", event_type)
        return

    # No session provided — open a short-lived one
    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
    except Exception:
        logger.exception("log_event commit failed (event_type=%s)", event_type)
