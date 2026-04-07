import uuid

from fastapi import APIRouter

from app.dependencies import AdminUser, DB, ReviewerUser
from app.schemas.git_server import (
    GitServerCreate,
    GitServerProjectRead,
    GitServerRead,
    GitServerUpdate,
)
from app.services import git_server_service as svc

router = APIRouter(prefix="/git-servers", tags=["git-servers"])


@router.get("", response_model=list[GitServerRead])
async def list_git_servers(db: DB, _: ReviewerUser):
    return await svc.list_git_servers(db)


@router.post("", response_model=GitServerRead, status_code=201)
async def create_git_server(data: GitServerCreate, db: DB, _: AdminUser):
    return await svc.create_git_server(db, data)


@router.get("/{server_id}", response_model=GitServerRead)
async def get_git_server(server_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.get_git_server(db, server_id)


@router.get("/{server_id}/projects", response_model=list[GitServerProjectRead])
async def list_git_server_projects(server_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.list_projects_for_git_server(db, server_id)


@router.put("/{server_id}", response_model=GitServerRead)
async def update_git_server(server_id: uuid.UUID, data: GitServerUpdate, db: DB, _: AdminUser):
    return await svc.update_git_server(db, server_id, data)


@router.delete("/{server_id}", status_code=204)
async def delete_git_server(server_id: uuid.UUID, db: DB, _: AdminUser):
    await svc.delete_git_server(db, server_id)
