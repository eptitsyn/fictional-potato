import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.review import ReviewJob
from app.models.user import User
from app.schemas.review import ReviewTriggerRequest
from app.workers.tasks import run_review_job


async def list_jobs(db: AsyncSession, limit: int = 50) -> list[ReviewJob]:
    result = await db.execute(
        select(ReviewJob).order_by(ReviewJob.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    result = await db.execute(
        select(ReviewJob)
        .where(ReviewJob.id == job_id)
        .options(selectinload(ReviewJob.comments))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Review job {job_id} not found")
    return job


async def trigger_review(
    db: AsyncSession, data: ReviewTriggerRequest, actor: User
) -> ReviewJob:
    job = ReviewJob(
        repository_id=data.repository_id,
        trigger_type=data.trigger_type,
        commit_sha=data.commit_sha,
        mr_iid=data.mr_iid,
        status="pending",
        triggered_by_id=actor.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch to Celery
    run_review_job.delay(str(job.id))
    return job


async def retry_job(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    job = await get_job(db, job_id)
    if job.status not in ("failed",):
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry a job with status '{job.status}'",
        )
    job.status = "pending"
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    await db.commit()

    run_review_job.delay(str(job.id))
    return job


async def create_webhook_job(
    db: AsyncSession,
    repository_id: uuid.UUID,
    trigger_type: str,
    commit_sha: str | None = None,
    mr_iid: int | None = None,
) -> ReviewJob:
    job = ReviewJob(
        repository_id=repository_id,
        trigger_type=trigger_type,
        commit_sha=commit_sha,
        mr_iid=mr_iid,
        status="pending",
        triggered_by_id=None,  # webhook-triggered
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    run_review_job.delay(str(job.id))
    return job
