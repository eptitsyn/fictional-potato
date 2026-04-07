import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.user import UserCreate, UserPasswordChange, UserUpdate


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    existing = await db.execute(
        select(User).where((User.email == data.email) | (User.username == data.username))
    )
    if existing.first():
        raise ConflictError("Email or username already in use")

    user = User(
        email=data.email,
        username=data.username,
        hashed_password=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user_id: uuid.UUID, data: UserUpdate) -> User:
    user = await get_user(db, user_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: uuid.UUID, current_user: User) -> None:
    user = await get_user(db, user_id)
    if user.id == current_user.id:
        raise ForbiddenError("Cannot deactivate your own account")

    # Ensure at least one admin remains
    if user.role == "admin":
        count_result = await db.execute(
            select(func.count()).where(User.role == "admin", User.is_active == True)  # noqa: E712
        )
        if (count_result.scalar() or 0) <= 1:
            raise ForbiddenError("Cannot deactivate the last admin")

    user.is_active = False
    await db.commit()


async def change_password(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: UserPasswordChange,
    actor: User,
) -> None:
    user = await get_user(db, user_id)
    is_self = user.id == actor.id
    is_admin_override = actor.role == "admin" and not is_self

    if is_self or not is_admin_override:
        if not data.current_password:
            raise ForbiddenError("current_password required")
        if not verify_password(data.current_password, user.hashed_password):
            raise ForbiddenError("Incorrect current password")

    user.hashed_password = hash_password(data.new_password)
    await db.commit()
