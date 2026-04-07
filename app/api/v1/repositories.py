import uuid

from fastapi import APIRouter

from app.dependencies import AdminUser, DB, ReviewerUser
from app.schemas.repository import RepositoryCreate, RepositoryRead, RepositoryUpdate
from app.services import repository_service as svc

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=list[RepositoryRead])
async def list_repositories(db: DB, _: ReviewerUser):
    return await svc.list_repositories(db)


@router.post("", response_model=RepositoryRead, status_code=201)
async def create_repository(data: RepositoryCreate, db: DB, _: AdminUser):
    return await svc.create_repository(db, data)


@router.get("/{repo_id}", response_model=RepositoryRead)
async def get_repository(repo_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.get_repository(db, repo_id)


@router.put("/{repo_id}", response_model=RepositoryRead)
async def update_repository(repo_id: uuid.UUID, data: RepositoryUpdate, db: DB, _: AdminUser):
    return await svc.update_repository(db, repo_id, data)


@router.delete("/{repo_id}", status_code=204)
async def delete_repository(repo_id: uuid.UUID, db: DB, _: AdminUser):
    await svc.delete_repository(db, repo_id)
