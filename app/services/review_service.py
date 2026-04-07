import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.repository import Repository
from app.models.review import ReviewJob
from app.models.user import User
from app.schemas.review import ReviewTriggerRequest
from app.services.git_server_service import get_decrypted_access_token
from app.services.gitlab_service import GitLabClient
from app.workers.tasks import run_review_job

ORPHAN_PENDING_REQUEUE_MIN_AGE = timedelta(seconds=30)


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

    return await _enqueue_job(db, job)


async def retry_job(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    job = await get_job(db, job_id)
    if job.status not in ("failed",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry a job with status '{job.status}'",
        )
    job.status = "pending"
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    await db.commit()

    return await _enqueue_job(db, job)


async def restart_job(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    job = await _get_job_for_mutation(db, job_id)
    _ensure_job_is_stopped(job, action="restart")
    await _delete_gitlab_comments_for_job(job)

    for comment in list(job.comments):
        await db.delete(comment)

    job.status = "pending"
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.llm_model_id = None
    job.prompt_id = None
    await db.commit()
    await db.refresh(job)

    return await _enqueue_job(db, job)


async def delete_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    job = await _get_job_for_mutation(db, job_id)
    _ensure_job_is_stopped(job, action="delete")
    await _delete_gitlab_comments_for_job(job)
    await db.delete(job)
    await db.commit()


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

    return await _enqueue_job(db, job)


async def requeue_pending_job(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    job = await get_job(db, job_id)
    if job.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot requeue a job with status '{job.status}'",
        )
    if job.started_at is not None:
        raise ConflictError(
            "Cannot requeue a pending job that already started. Wait for it to finish."
        )

    age = datetime.now(UTC) - job.created_at
    if age < ORPHAN_PENDING_REQUEUE_MIN_AGE:
        raise ConflictError(
            "Only pending jobs older than 30 seconds can be requeued. "
            "This avoids creating duplicate tasks while Celery is still picking the job up."
        )

    job.error_message = None
    job.completed_at = None
    await db.commit()
    await db.refresh(job)
    return await _enqueue_job(db, job)


async def _get_job_for_mutation(db: AsyncSession, job_id: uuid.UUID) -> ReviewJob:
    result = await db.execute(
        select(ReviewJob)
        .where(ReviewJob.id == job_id)
        .options(
            selectinload(ReviewJob.comments),
            selectinload(ReviewJob.repository).selectinload(Repository.git_server),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Review job {job_id} not found")
    return job


def _ensure_job_is_stopped(job: ReviewJob, *, action: str) -> None:
    if job.status in ("pending", "running"):
        raise ConflictError(
            f"Cannot {action} a job with status '{job.status}'. Wait until it finishes."
        )


async def _enqueue_job(db: AsyncSession, job: ReviewJob) -> ReviewJob:
    try:
        run_review_job.apply_async(args=(str(job.id),), queue="reviews")
    except Exception as exc:
        job.status = "failed"
        job.error_message = f"Failed to enqueue review job: {exc}"[:2000]
        job.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(job)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue review job. Check Redis/Celery connectivity and retry.",
        ) from exc

    return job


async def _delete_gitlab_comments_for_job(job: ReviewJob) -> None:
    note_ids = [comment.gitlab_note_id for comment in job.comments if comment.gitlab_note_id]
    if not note_ids:
        return

    repo = job.repository
    if repo is None or repo.git_server is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review job is missing repository GitLab configuration",
        )

    gitlab = GitLabClient(repo.git_server.base_url, get_decrypted_access_token(repo.git_server))
    note_index = await _load_gitlab_note_index(gitlab, job)
    failures: list[str] = []

    for note_id in note_ids:
        discussion_id = note_index.get(str(note_id))
        if not discussion_id:
            continue

        try:
            if job.trigger_type == "commit":
                if not job.commit_sha:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Commit review is missing commit SHA",
                    )
                await gitlab.delete_commit_discussion_note(
                    repo.gitlab_project_id,
                    job.commit_sha,
                    discussion_id,
                    str(note_id),
                )
            else:
                if job.mr_iid is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Merge request review is missing MR IID",
                    )
                await gitlab.delete_mr_discussion_note(
                    repo.gitlab_project_id,
                    job.mr_iid,
                    discussion_id,
                    str(note_id),
                )
        except HTTPException:
            raise
        except Exception as exc:
            failures.append(f"{note_id}: {exc}")

    if failures:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete some GitLab comments: " + "; ".join(failures[:5]),
        )


async def _load_gitlab_note_index(gitlab: GitLabClient, job: ReviewJob) -> dict[str, str]:
    repo = job.repository
    if repo is None:
        return {}

    if job.trigger_type == "commit":
        if not job.commit_sha:
            return {}
        discussions = await gitlab.list_commit_discussions(repo.gitlab_project_id, job.commit_sha)
    else:
        if job.mr_iid is None:
            return {}
        discussions = await gitlab.list_mr_discussions(repo.gitlab_project_id, job.mr_iid)

    return _build_note_discussion_index(discussions)


def _build_note_discussion_index(discussions: list[dict]) -> dict[str, str]:
    note_index: dict[str, str] = {}

    for discussion in discussions:
        discussion_id = discussion.get("id")
        if discussion_id is None:
            continue

        for note in discussion.get("notes") or []:
            if not isinstance(note, dict):
                continue
            note_id = note.get("id")
            if note_id is None:
                continue
            note_index[str(note_id)] = str(discussion_id)

    return note_index
