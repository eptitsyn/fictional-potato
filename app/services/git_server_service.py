import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import decrypt, encrypt
from app.models.git_server import GitServer
from app.schemas.git_server import GitServerCreate, GitServerUpdate
from app.services.event_log_service import log_event
from app.services.gitlab_service import GitLabClient


async def list_git_servers(db: AsyncSession) -> list[GitServer]:
    result = await db.execute(select(GitServer).order_by(GitServer.name))
    return list(result.scalars().all())


async def get_git_server(db: AsyncSession, server_id: uuid.UUID) -> GitServer:
    result = await db.execute(
        select(GitServer)
        .where(GitServer.id == server_id)
        .options(selectinload(GitServer.repositories))
    )
    server = result.scalar_one_or_none()
    if not server:
        raise NotFoundError(f"Git server {server_id} not found")
    return server


async def create_git_server(db: AsyncSession, data: GitServerCreate) -> GitServer:
    existing = await db.execute(select(GitServer).where(GitServer.name == data.name))
    if existing.first():
        raise ConflictError(f"Git server named '{data.name}' already exists")

    server = GitServer(
        name=data.name,
        base_url=data.base_url,
        access_token_encrypted=encrypt(data.access_token),
        is_active=data.is_active,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    await log_event(
        db, "git_server.created", f"Git server '{server.name}' created",
        details={"server_id": str(server.id), "name": server.name, "base_url": server.base_url},
    )
    return server


async def update_git_server(
    db: AsyncSession, server_id: uuid.UUID, data: GitServerUpdate
) -> GitServer:
    server = await get_git_server(db, server_id)
    update_data = data.model_dump(exclude_none=True)

    new_name = update_data.get("name")
    if new_name and new_name != server.name:
        existing = await db.execute(select(GitServer).where(GitServer.name == new_name))
        if existing.first():
            raise ConflictError(f"Git server named '{new_name}' already exists")

    token_rotated = "access_token" in update_data
    if token_rotated:
        server.access_token_encrypted = encrypt(update_data.pop("access_token"))

    for field, value in update_data.items():
        setattr(server, field, value)

    await db.commit()
    await db.refresh(server)

    if token_rotated:
        await log_event(
            db, "git_server.token_rotated", f"Access token rotated for '{server.name}'",
            details={"server_id": str(server.id), "name": server.name},
        )
    else:
        await log_event(
            db, "git_server.updated", f"Git server '{server.name}' updated",
            details={
                "server_id": str(server.id),
                "changed_fields": list(update_data.keys()),
            },
        )
    return server


async def delete_git_server(db: AsyncSession, server_id: uuid.UUID) -> None:
    server = await get_git_server(db, server_id)
    if server.repositories:
        raise ConflictError("Delete or reassign repositories before deleting this git server")

    name = server.name
    await db.delete(server)
    await db.commit()
    await log_event(
        db, "git_server.deleted", f"Git server '{name}' deleted",
        level="warning",
        details={"server_id": str(server_id), "name": name},
    )


async def list_projects_for_git_server(
    db: AsyncSession, server_id: uuid.UUID
) -> list[dict[str, int | str | None]]:
    server = await get_git_server(db, server_id)
    if not server.is_active:
        raise ConflictError("Git server is inactive")

    client = GitLabClient(server.base_url, get_decrypted_access_token(server))
    try:
        projects = await client.list_projects()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch projects from git server: {exc}",
        ) from exc

    normalized: list[dict[str, int | str | None]] = []
    seen_ids: set[int] = set()
    for project in projects:
        project_id = project.get("id")
        if not isinstance(project_id, int) or project_id in seen_ids:
            continue
        seen_ids.add(project_id)
        path_with_namespace = project.get("path_with_namespace") or project.get("name")
        if not isinstance(path_with_namespace, str) or not path_with_namespace.strip():
            continue
        normalized.append(
            {
                "id": project_id,
                "name": str(project.get("name") or path_with_namespace),
                "path_with_namespace": path_with_namespace,
                "web_url": project.get("web_url"),
            }
        )

    return normalized


def get_decrypted_access_token(server: GitServer) -> str:
    return decrypt(server.access_token_encrypted)
