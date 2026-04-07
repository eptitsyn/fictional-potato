from fastapi import APIRouter, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_token
from app.dependencies import CurrentUser, DB
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import authenticate_user, get_user_from_token, issue_tokens
from app.services.event_log_service import log_event

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (
        forwarded.split(",")[0].strip()
        or request.headers.get("X-Real-IP")
        or (request.client.host if request.client else None)
    )


@router.post("/token", response_model=dict)
async def login(data: LoginRequest, request: Request, response: Response, db: DB):
    ip = _client_ip(request)
    try:
        user = await authenticate_user(db, data.username, data.password)
    except UnauthorizedError:
        await log_event(
            db, "auth.login.failed",
            f"Failed login attempt for '{data.username}'",
            level="warning",
            ip_address=ip,
            details={"attempted_username": data.username},
        )
        await db.commit()
        raise

    tokens = issue_tokens(user)
    await log_event(
        db, "auth.login.success",
        f"User '{user.username}' logged in",
        user_id=user.id,
        username=user.username,
        ip_address=ip,
        details={"role": user.role},
    )
    await db.commit()

    response.set_cookie(
        key="session",
        value=tokens["access_token"],
        httponly=True,
        samesite="lax",
        max_age=3600 * 24 * 7,
    )
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: DB):
    user = await get_user_from_token(db, data.refresh_token)
    tokens = issue_tokens(user)
    return {"access_token": tokens["access_token"], "token_type": "bearer"}


@router.post("/logout")
async def logout(response: Response, current_user: CurrentUser, db: DB):
    await log_event(
        db, "auth.logout",
        f"User '{current_user.username}' logged out",
        user_id=current_user.id,
        username=current_user.username,
    )
    await db.commit()
    response.delete_cookie("session")
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser):
    return current_user
