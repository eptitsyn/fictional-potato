import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.user import UserCreate, UserPasswordChange, UserUpdate
from app.services.event_log_service import log_event


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def create_user(db: AsyncSession, data: UserCreate, actor: User | None = None) -> User:
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
    await log_event(
        db, "user.created", f"User '{user.username}' created",
        user_id=actor.id if actor else None,
        username=actor.username if actor else None,
        details={"new_user_id": str(user.id), "new_username": user.username, "role": user.role},
    )
    return user


async def update_user(db: AsyncSession, user_id: uuid.UUID, data: UserUpdate, actor: User | None = None) -> User:
    user = await get_user(db, user_id)
    old_role = user.role
    update_data = data.model_dump(exclude_none=True)
    role_changed = "role" in update_data and update_data["role"] != old_role
    for field, value in update_data.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    if role_changed:
        await log_event(
            db, "user.role_changed", f"Role changed for '{user.username}'",
            level="warning",
            user_id=actor.id if actor else None,
            username=actor.username if actor else None,
            details={"target_user_id": str(user.id), "old_role": old_role, "new_role": user.role},
        )
    else:
        await log_event(
            db, "user.updated", f"User '{user.username}' updated",
            user_id=actor.id if actor else None,
            username=actor.username if actor else None,
            details={"target_user_id": str(user.id), "changed_fields": list(update_data.keys())},
        )
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
    await log_event(
        db, "user.deactivated", f"User '{user.username}' deactivated",
        level="warning",
        user_id=current_user.id,
        username=current_user.username,
        details={"target_user_id": str(user.id), "target_username": user.username},
    )


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
    await log_event(
        db, "auth.password_changed", f"Password changed for '{user.username}'",
        user_id=actor.id,
        username=actor.username,
        details={"target_user_id": str(user.id), "by_admin": is_admin_override},
    )
