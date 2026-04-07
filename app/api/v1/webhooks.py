"""
GitLab webhook receiver.
URL: POST /api/v1/webhooks/gitlab/{repository_id}

GitLab sends X-Gitlab-Token header for auth validation.
Supported events: Push Hook, Merge Request Hook.
"""
import uuid

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundError
from app.core.security import validate_webhook_secret
from app.services.event_log_service import log_event
from app.services.repository_service import (
    get_decrypted_webhook_secret,
    get_repository,
)
from app.services.review_service import create_webhook_job

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/gitlab/{repository_id}", status_code=202)
async def gitlab_webhook(
    repository_id: uuid.UUID,
    request: Request,
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
):
    async with AsyncSessionLocal() as db:
        try:
            repo = await get_repository(db, repository_id)
        except NotFoundError:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Repository not found"},
            )

        if not repo.is_active:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Repository is inactive"},
            )

        # Validate webhook secret
        stored_secret = get_decrypted_webhook_secret(repo)
        if not x_gitlab_token or not validate_webhook_secret(x_gitlab_token, stored_secret):
            await log_event(
                db, "gitlab.webhook_invalid_token",
                f"Invalid webhook token for repository '{repo.name}'",
                level="warning",
                details={"repository_id": str(repository_id), "repository_name": repo.name},
            )
            await db.commit()
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid webhook token"},
            )

        payload = await request.json()
        event = x_gitlab_event or payload.get("object_kind", "")

        if event in ("push", "Push Hook") and repo.review_commits:
            commits = payload.get("commits", [])
            jobs = []
            for commit in commits[:5]:  # limit to last 5 commits per push
                sha = commit.get("id")
                if sha:
                    job = await create_webhook_job(
                        db,
                        repository_id=repo.id,
                        trigger_type="commit",
                        commit_sha=sha,
                    )
                    jobs.append(str(job.id))
            await log_event(
                db, "gitlab.webhook_received",
                f"Push webhook for '{repo.name}': {len(jobs)} review job(s) queued",
                details={
                    "repository_id": str(repository_id),
                    "repository_name": repo.name,
                    "event": event,
                    "commits": len(commits),
                    "jobs_queued": jobs,
                },
            )
            await db.commit()
            return {"accepted": True, "jobs": jobs}

        elif event in ("merge_request", "Merge Request Hook") and repo.review_mrs:
            obj = payload.get("object_attributes", {})
            action = obj.get("action", "")
            mr_iid = obj.get("iid")

            # Only review on open/update, not close/merge
            if action in ("open", "update", "reopen") and mr_iid:
                job = await create_webhook_job(
                    db,
                    repository_id=repo.id,
                    trigger_type="mr",
                    mr_iid=int(mr_iid),
                )
                await log_event(
                    db, "gitlab.webhook_received",
                    f"MR webhook for '{repo.name}' !{mr_iid} ({action}): review queued",
                    details={
                        "repository_id": str(repository_id),
                        "repository_name": repo.name,
                        "event": event,
                        "action": action,
                        "mr_iid": mr_iid,
                        "job_id": str(job.id),
                    },
                )
                await db.commit()
                return {"accepted": True, "jobs": [str(job.id)]}

        await log_event(
            db, "gitlab.webhook_ignored",
            f"Webhook for '{repo.name}' not handled (event='{event}')",
            level="warning",
            details={
                "repository_id": str(repository_id),
                "repository_name": repo.name,
                "event": event,
            },
        )
        await db.commit()
        return {"accepted": False, "reason": "Event not handled or feature disabled"}
