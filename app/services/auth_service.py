from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User:
    result = await db.execute(
        select(User).where(
            (User.username == username) | (User.email == username),
            User.is_active == True,  # noqa: E712
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        raise UnauthorizedError("Incorrect username or password")
    return user


def issue_tokens(user: User) -> dict:
    payload = {"sub": str(user.id), "role": user.role}
    return {
        "access_token": create_access_token(payload),
        "refresh_token": create_refresh_token(payload),
        "token_type": "bearer",
    }


async def get_user_from_token(db: AsyncSession, token: str) -> User:
    try:
        payload = decode_token(token)
    except Exception:
        raise UnauthorizedError("Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if not user:
        raise UnauthorizedError("User not found or inactive")
    return user


async def bootstrap_admin(db: AsyncSession, email: str, username: str, password: str) -> bool:
    """Create first admin on cold start. Returns True if admin was created."""
    result = await db.execute(select(User))
    if result.first() is not None:
        return False  # users already exist

    admin = User(
        email=email,
        username=username,
        hashed_password=hash_password(password),
        role="admin",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    return True
