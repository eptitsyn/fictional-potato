from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.models.user import User
from app.services.auth_service import get_user_from_token


async def _resolve_token(
    authorization: str | None = Header(default=None),
    session: str | None = Cookie(default=None),
) -> str:
    """Accept token from Authorization header OR session cookie."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ")
    if session:
        return session
    raise UnauthorizedError()


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(_resolve_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    user = await get_user_from_token(db, token)
    # Expose user identity to the request logging middleware via request.state
    request.state.user_id = user.id
    request.state.username = user.username
    return user


def require_role(*roles: str):
    async def check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise ForbiddenError(
                f"Role '{user.role}' is not permitted. Required: {list(roles)}"
            )
        return user

    return check


# Convenient aliases
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_role("admin"))]
ReviewerUser = Annotated[User, Depends(require_role("admin", "reviewer"))]
DB = Annotated[AsyncSession, Depends(get_db)]
