"""
Celery tasks for running code reviews asynchronously.
Uses the LangGraph multi-step review pipeline (security + quality → consolidation).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from celery import Task

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class ReviewTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Review task %s failed: %s", task_id, exc)


@celery_app.task(bind=True, base=ReviewTask, name="app.workers.tasks.run_review_job")
def run_review_job(self, job_id: str) -> dict:
    """Celery entry point — delegates to async pipeline."""
    return asyncio.run(_execute_review(uuid.UUID(job_id)))


async def _execute_review(job_id: uuid.UUID) -> dict:
    """
    Full review pipeline (LangGraph-powered):
    1. Load job + repository
    2. Fetch diff from GitLab
    3. Resolve effective prompt + model
    4. Run LangGraph multi-step review (plan → security → quality → consolidate)
    5. Persist comments + post to GitLab
    6. Update job status
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import AsyncSessionLocal
    from app.langchain_integration.review_graph import run_review_graph
    from app.models.review import ReviewComment, ReviewJob
    from app.services.gitlab_service import GitLabClient
    from app.services.llm_endpoint_service import get_decrypted_api_key, get_global_default_model
    from app.services.prompt_service import resolve_effective_prompt
    from app.services.repository_service import get_decrypted_gitlab_token

    async with AsyncSessionLocal() as db:
        # Load job with all necessary relations
        result = await db.execute(
            select(ReviewJob)
            .where(ReviewJob.id == job_id)
            .options(
                selectinload(ReviewJob.repository).selectinload(
                    ReviewJob.repository.__class__.llm_model
                ).selectinload(ReviewJob.repository.__class__.llm_model.__class__.endpoint)
            )
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error("ReviewJob %s not found", job_id)
            return {"status": "error", "detail": "Job not found"}

        repo = job.repository
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

        try:
            # Resolve LLM model (repo-pinned → global default)
            if repo.llm_model and repo.llm_model.is_active:
                model = repo.llm_model
            else:
                model = await get_global_default_model(db)
            if not model:
                raise RuntimeError("No active LLM model configured. Add one in Settings → LLM Models.")

            api_key = get_decrypted_api_key(model.endpoint)
            gitlab_token = get_decrypted_gitlab_token(repo)
            gitlab = GitLabClient(repo.gitlab_url, gitlab_token)

            # Fetch diff + metadata
            if job.trigger_type == "commit":
                diff = await gitlab.get_commit_diff(repo.gitlab_project_id, job.commit_sha)
                commit_info = await gitlab.get_commit(repo.gitlab_project_id, job.commit_sha)
                metadata = {
                    "commit_sha": job.commit_sha,
                    "repository_name": repo.name,
                    "diff": diff,
                    "author": commit_info.get("author_name", ""),
                    "message": commit_info.get("title", ""),
                    "mr_title": "", "mr_description": "",
                    "source_branch": "", "target_branch": "",
                }
                review_prompt_type = "commit_review"
            else:
                diff = await gitlab.get_mr_changes(repo.gitlab_project_id, job.mr_iid)
                mr_info = await gitlab.get_mr(repo.gitlab_project_id, job.mr_iid)
                metadata = {
                    "mr_title": mr_info.get("title", ""),
                    "mr_description": mr_info.get("description", "")[:500],
                    "repository_name": repo.name,
                    "source_branch": mr_info.get("source_branch", ""),
                    "target_branch": mr_info.get("target_branch", ""),
                    "diff": diff,
                    "commit_sha": "", "author": "", "message": "",
                }
                review_prompt_type = "mr_review"

            # Resolve prompts (user-specific → global default)
            triggered_by_id = job.triggered_by_id
            system_prompt = await resolve_effective_prompt(db, triggered_by_id, "system")
            review_prompt = await resolve_effective_prompt(db, triggered_by_id, review_prompt_type)
            if not review_prompt:
                raise RuntimeError(
                    f"No active prompt for type '{review_prompt_type}'. "
                    "Seed prompts may not have been applied — run migrations."
                )

            # Update job refs
            job.llm_model_id = model.id
            job.prompt_id = review_prompt.id
            await db.commit()

            # ── LangGraph multi-step review pipeline ──────────────────────────
            comments_data = await run_review_graph(
                diff=diff,
                metadata=metadata,
                system_prompt=system_prompt,
                review_prompt=review_prompt,
                model=model,
                api_key=api_key,
            )

            # Persist + post each comment
            now = datetime.now(UTC)
            for c in comments_data:
                severity = c.get("severity", "info")
                if severity not in ("info", "warning", "error"):
                    severity = "info"

                comment = ReviewComment(
                    job_id=job.id,
                    file_path=c.get("file_path"),
                    line_number=c.get("line_number"),
                    comment_body=c.get("comment_body", ""),
                    severity=severity,
                )
                db.add(comment)
                await db.flush()  # get comment.id

                # Post to GitLab
                try:
                    body = _format_gitlab_comment(c)
                    if job.trigger_type == "commit":
                        resp = await gitlab.post_commit_comment(
                            repo.gitlab_project_id,
                            job.commit_sha,
                            body,
                            path=c.get("file_path"),
                            line=c.get("line_number"),
                        )
                    else:
                        resp = await gitlab.post_mr_note(
                            repo.gitlab_project_id, job.mr_iid, body
                        )
                    comment.gitlab_note_id = str(resp.get("id", ""))
                    comment.posted_at = now
                except Exception as post_err:
                    logger.warning("Failed to post comment to GitLab: %s", post_err)

            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "Review job %s completed — %d comments posted",
                job_id,
                len(comments_data),
            )
            return {"status": "completed", "comments": len(comments_data)}

        except Exception as exc:
            logger.exception("Review job %s failed", job_id)
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.completed_at = datetime.now(UTC)
            await db.commit()
            return {"status": "failed", "detail": str(exc)}


def _format_gitlab_comment(c: dict) -> str:
    severity = c.get("severity", "info")
    icon = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(severity, "ℹ️")
    body = c.get("comment_body", "")
    return (
        f"{icon} **AI Review [{severity.upper()}]**\n\n"
        f"{body}\n\n"
        f"---\n*Generated by [AI Code Reviewer](https://github.com) using LangGraph*"
    )
