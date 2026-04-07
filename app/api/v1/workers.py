"""
Worker status endpoint — queries Celery via its inspect API.
Returns real-time data about active workers, running tasks, and queue depth.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter

from app.dependencies import ReviewerUser
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/workers", tags=["workers"])


def _inspect(timeout: float = 3.0) -> dict:
    """Run synchronous Celery inspect calls and return aggregated status."""
    insp = celery_app.control.inspect(timeout=timeout)

    # All calls hit every registered worker
    ping = insp.ping() or {}
    active = insp.active() or {}
    reserved = insp.reserved() or {}
    stats = insp.stats() or {}

    workers = []
    for worker_name, pong in ping.items():
        worker_active = active.get(worker_name, [])
        worker_reserved = reserved.get(worker_name, [])
        worker_stats = stats.get(worker_name, {})

        pool = worker_stats.get("pool", {})
        broker = worker_stats.get("broker", {})

        workers.append({
            "name": worker_name,
            "status": "online",
            "active_tasks": _format_tasks(worker_active),
            "reserved_tasks": _format_tasks(worker_reserved),
            "concurrency": pool.get("max-concurrency", pool.get("processes", "?")),
            "processes": pool.get("processes", []),
            "broker_transport": broker.get("transport", "redis"),
            "broker_connected": True,
            "total_tasks": worker_stats.get("total", {}),
            "prefetch_count": worker_stats.get("prefetch_count", 0),
        })

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "workers_online": len(workers),
        "workers": workers,
    }


def _format_tasks(raw: list[dict]) -> list[dict]:
    return [
        {
            "id": t.get("id", ""),
            "name": t.get("name", "").split(".")[-1],  # short name
            "args": t.get("args", []),
            "kwargs": t.get("kwargs", {}),
            "time_start": t.get("time_start"),
            "acknowledged": t.get("acknowledged", False),
            "worker_pid": t.get("worker_pid"),
        }
        for t in raw
    ]


@router.get("/status")
async def worker_status(_: ReviewerUser) -> dict:
    """Return live Celery worker status (ping + active + reserved + stats)."""
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _inspect)
    return data


@router.get("/queues")
async def queue_lengths(_: ReviewerUser) -> dict:
    """Return approximate task counts per queue via Redis LLEN."""
    try:
        import redis as _redis

        from app.workers.celery_app import REDIS_URL

        r = _redis.from_url(REDIS_URL, decode_responses=True)
        queues = {}
        for q in ("reviews", "celery"):  # add more queue names here if needed
            length = r.llen(q)
            queues[q] = {"pending": length}
        return {"queues": queues, "timestamp": datetime.now(UTC).isoformat()}
    except Exception as e:
        return {"queues": {}, "error": str(e), "timestamp": datetime.now(UTC).isoformat()}
