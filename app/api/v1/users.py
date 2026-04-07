import uuid

from fastapi import APIRouter

from app.dependencies import AdminUser, CurrentUser, DB
from app.core.exceptions import ForbiddenError
from app.schemas.user import UserCreate, UserPasswordChange, UserRead, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
async def list_users(db: DB, _: AdminUser):
    return await user_service.list_users(db)


@router.post("", response_model=UserRead, status_code=201)
async def create_user(data: UserCreate, db: DB, _: AdminUser):
    return await user_service.create_user(db, data)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, db: DB, current_user: CurrentUser):
    if current_user.role != "admin" and current_user.id != user_id:
        raise ForbiddenError()
    return await user_service.get_user(db, user_id)


@router.put("/{user_id}", response_model=UserRead)
async def update_user(user_id: uuid.UUID, data: UserUpdate, db: DB, _: AdminUser):
    return await user_service.update_user(db, user_id, data)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, db: DB, current_user: AdminUser):
    await user_service.delete_user(db, user_id, current_user)


@router.put("/{user_id}/password", status_code=204)
async def change_password(
    user_id: uuid.UUID,
    data: UserPasswordChange,
    db: DB,
    current_user: CurrentUser,
):
    if current_user.role != "admin" and current_user.id != user_id:
        raise ForbiddenError()
    await user_service.change_password(db, user_id, data, current_user)
