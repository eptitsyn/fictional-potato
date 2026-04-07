import uuid

from fastapi import APIRouter

from app.dependencies import DB, ReviewerUser
from app.schemas.review import (
    ReviewCommentRead,
    ReviewJobDetailRead,
    ReviewJobRead,
    ReviewTriggerRequest,
)
from app.services import review_service as svc

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=list[ReviewJobRead])
async def list_reviews(db: DB, _: ReviewerUser):
    return await svc.list_jobs(db)


@router.post("/trigger", response_model=ReviewJobRead, status_code=202)
async def trigger_review(data: ReviewTriggerRequest, db: DB, current_user: ReviewerUser):
    return await svc.trigger_review(db, data, current_user)


@router.get("/{job_id}", response_model=ReviewJobDetailRead)
async def get_review(job_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.get_job(db, job_id)


@router.get("/{job_id}/comments", response_model=list[ReviewCommentRead])
async def get_comments(job_id: uuid.UUID, db: DB, _: ReviewerUser):
    job = await svc.get_job(db, job_id)
    return job.comments


@router.post("/{job_id}/retry", response_model=ReviewJobRead, status_code=202)
async def retry_review(job_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.retry_job(db, job_id)


@router.post("/{job_id}/restart", response_model=ReviewJobRead, status_code=202)
async def restart_review(job_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.restart_job(db, job_id)


@router.delete("/{job_id}", status_code=204)
async def delete_review(job_id: uuid.UUID, db: DB, _: ReviewerUser):
    await svc.delete_job(db, job_id)
