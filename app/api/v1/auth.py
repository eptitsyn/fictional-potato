from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.dependencies import CurrentUser, DB
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.services.auth_service import authenticate_user, get_user_from_token, issue_tokens
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=dict)
async def login(data: LoginRequest, response: Response, db: DB):
    user = await authenticate_user(db, data.username, data.password)
    tokens = issue_tokens(user)
    # Set refresh token as HttpOnly cookie
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
async def logout(response: Response):
    response.delete_cookie("session")
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser):
    return current_user
