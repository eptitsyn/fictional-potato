from sqlalchemy import desc, select

from fastapi import APIRouter, Query

from app.dependencies import DB, AdminUser
from app.models.event_log import EventLog
from app.schemas.event_log import EventLogRead

router = APIRouter(prefix="/event-logs", tags=["event-logs"])


@router.get("", response_model=list[EventLogRead])
async def list_event_logs(
    db: DB,
    _: AdminUser,
    limit: int = Query(default=200, le=500),
    offset: int = 0,
    event_type: str | None = None,
    level: str | None = None,
):
    q = select(EventLog)
    if event_type:
        q = q.where(EventLog.event_type == event_type)
    if level:
        q = q.where(EventLog.level == level)
    q = q.order_by(desc(EventLog.timestamp)).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()
