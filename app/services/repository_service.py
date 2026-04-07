import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import decrypt, encrypt
from app.models.repository import Repository
from app.schemas.repository import RepositoryCreate, RepositoryUpdate


async def list_repositories(db: AsyncSession) -> list[Repository]:
    result = await db.execute(select(Repository).order_by(Repository.name))
    return list(result.scalars().all())


async def get_repository(db: AsyncSession, repo_id: uuid.UUID) -> Repository:
    result = await db.execute(
        select(Repository)
        .where(Repository.id == repo_id)
        .options(selectinload(Repository.llm_model))
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise NotFoundError(f"Repository {repo_id} not found")
    return repo


async def get_repository_by_gitlab_id(
    db: AsyncSession, gitlab_project_id: int
) -> Repository | None:
    result = await db.execute(
        select(Repository)
        .where(Repository.gitlab_project_id == gitlab_project_id, Repository.is_active == True)  # noqa: E712
        .options(selectinload(Repository.llm_model).selectinload(Repository.llm_model.__class__.endpoint))
    )
    return result.scalar_one_or_none()


async def create_repository(db: AsyncSession, data: RepositoryCreate) -> Repository:
    existing = await db.execute(
        select(Repository).where(Repository.gitlab_project_id == data.gitlab_project_id)
    )
    if existing.first():
        raise ConflictError(
            f"Repository with GitLab project ID {data.gitlab_project_id} already exists"
        )

    repo = Repository(
        gitlab_project_id=data.gitlab_project_id,
        name=data.name,
        gitlab_url=data.gitlab_url,
        gitlab_token_encrypted=encrypt(data.gitlab_token),
        webhook_secret_encrypted=encrypt(data.webhook_secret),
        llm_model_id=data.llm_model_id,
        review_commits=data.review_commits,
        review_mrs=data.review_mrs,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


async def update_repository(
    db: AsyncSession, repo_id: uuid.UUID, data: RepositoryUpdate
) -> Repository:
    repo = await get_repository(db, repo_id)
    update_data = data.model_dump(exclude_none=True)

    if "gitlab_token" in update_data:
        repo.gitlab_token_encrypted = encrypt(update_data.pop("gitlab_token"))
    if "webhook_secret" in update_data:
        repo.webhook_secret_encrypted = encrypt(update_data.pop("webhook_secret"))

    for field, value in update_data.items():
        setattr(repo, field, value)
    await db.commit()
    await db.refresh(repo)
    return repo


async def delete_repository(db: AsyncSession, repo_id: uuid.UUID) -> None:
    repo = await get_repository(db, repo_id)
    await db.delete(repo)
    await db.commit()


def get_decrypted_gitlab_token(repo: Repository) -> str:
    return decrypt(repo.gitlab_token_encrypted)


def get_decrypted_webhook_secret(repo: Repository) -> str:
    return decrypt(repo.webhook_secret_encrypted)
