import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import decrypt, encrypt
from app.models.git_server import GitServer
from app.models.llm import LLMModel
from app.models.repository import Repository
from app.schemas.repository import RepositoryCreate, RepositoryUpdate
from app.services.event_log_service import log_event


async def list_repositories(db: AsyncSession) -> list[Repository]:
    result = await db.execute(
        select(Repository)
        .order_by(Repository.name)
        .options(
            selectinload(Repository.git_server),
            selectinload(Repository.llm_model),
        )
    )
    return list(result.scalars().all())


async def get_repository(db: AsyncSession, repo_id: uuid.UUID) -> Repository:
    result = await db.execute(
        select(Repository)
        .where(Repository.id == repo_id)
        .options(
            selectinload(Repository.git_server),
            selectinload(Repository.llm_model),
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise NotFoundError(f"Repository {repo_id} not found")
    return repo


async def get_repository_by_gitlab_id(
    db: AsyncSession, git_server_id: uuid.UUID, gitlab_project_id: int
) -> Repository | None:
    result = await db.execute(
        select(Repository)
        .where(
            Repository.git_server_id == git_server_id,
            Repository.gitlab_project_id == gitlab_project_id,
            Repository.is_active == True,  # noqa: E712
        )
        .options(
            selectinload(Repository.git_server),
            selectinload(Repository.llm_model).selectinload(LLMModel.endpoint),
        )
    )
    return result.scalar_one_or_none()


async def create_repository(db: AsyncSession, data: RepositoryCreate) -> Repository:
    await _ensure_git_server_exists(db, data.git_server_id)
    existing = await db.execute(
        select(Repository).where(
            Repository.git_server_id == data.git_server_id,
            Repository.gitlab_project_id == data.gitlab_project_id,
        )
    )
    if existing.first():
        raise ConflictError(
            f"Repository with GitLab project ID {data.gitlab_project_id} already exists on this git server"
        )

    repo = Repository(
        git_server_id=data.git_server_id,
        gitlab_project_id=data.gitlab_project_id,
        name=data.name,
        webhook_secret_encrypted=encrypt(data.webhook_secret),
        llm_model_id=data.llm_model_id,
        review_commits=data.review_commits,
        review_mrs=data.review_mrs,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    result = await get_repository(db, repo.id)
    await log_event(
        db, "repository.created", f"Repository '{result.name}' created",
        details={
            "repo_id": str(result.id),
            "name": result.name,
            "git_server_id": str(data.git_server_id),
            "gitlab_project_id": data.gitlab_project_id,
        },
    )
    return result


async def update_repository(
    db: AsyncSession, repo_id: uuid.UUID, data: RepositoryUpdate
) -> Repository:
    repo = await get_repository(db, repo_id)
    update_data = data.model_dump(exclude_none=True)

    webhook_rotated = "webhook_secret" in update_data
    if webhook_rotated:
        repo.webhook_secret_encrypted = encrypt(update_data.pop("webhook_secret"))

    next_git_server_id = update_data.get("git_server_id", repo.git_server_id)
    next_project_id = update_data.get("gitlab_project_id", repo.gitlab_project_id)
    if next_git_server_id != repo.git_server_id:
        await _ensure_git_server_exists(db, next_git_server_id)

    if (
        next_git_server_id != repo.git_server_id
        or next_project_id != repo.gitlab_project_id
    ):
        existing = await db.execute(
            select(Repository).where(
                Repository.id != repo.id,
                Repository.git_server_id == next_git_server_id,
                Repository.gitlab_project_id == next_project_id,
            )
        )
        if existing.first():
            raise ConflictError(
                f"Repository with GitLab project ID {next_project_id} already exists on this git server"
            )

    for field, value in update_data.items():
        setattr(repo, field, value)
    await db.commit()
    await db.refresh(repo)
    result = await get_repository(db, repo.id)

    if webhook_rotated:
        await log_event(
            db, "repository.webhook_secret_rotated",
            f"Webhook secret rotated for '{result.name}'",
            details={"repo_id": str(result.id), "name": result.name},
        )
    else:
        changed = list(update_data.keys())
        event = (
            "repository.deactivated"
            if "is_active" in changed and not repo.is_active
            else "repository.updated"
        )
        level = "warning" if event == "repository.deactivated" else "info"
        await log_event(
            db, event, f"Repository '{result.name}' {event.split('.')[1]}",
            level=level,
            details={"repo_id": str(result.id), "name": result.name, "changed_fields": changed},
        )
    return result


async def delete_repository(db: AsyncSession, repo_id: uuid.UUID) -> None:
    repo = await get_repository(db, repo_id)
    name = repo.name
    await db.delete(repo)
    await db.commit()
    await log_event(
        db, "repository.deleted", f"Repository '{name}' deleted",
        level="warning",
        details={"repo_id": str(repo_id), "name": name},
    )


def get_decrypted_webhook_secret(repo: Repository) -> str:
    return decrypt(repo.webhook_secret_encrypted)


async def _ensure_git_server_exists(db: AsyncSession, server_id: uuid.UUID) -> None:
    existing = await db.execute(select(GitServer.id).where(GitServer.id == server_id))
    if not existing.first():
        raise NotFoundError(f"Git server {server_id} not found")
