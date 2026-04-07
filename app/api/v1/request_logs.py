from sqlalchemy import desc, select

from fastapi import APIRouter

from app.dependencies import DB, AdminUser
from app.models.request_log import RequestLog
from app.schemas.request_log import RequestLogRead

router = APIRouter(prefix="/request-logs", tags=["request-logs"])


@router.get("", response_model=list[RequestLogRead])
async def list_request_logs(
    db: DB,
    _: AdminUser,
    limit: int = 100,
    offset: int = 0,
):
    result = await db.execute(
        select(RequestLog)
        .order_by(desc(RequestLog.timestamp))
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
