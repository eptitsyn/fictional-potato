import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging_config import configure_logging
from app.models.request_log import RequestLog
from app.services.auth_service import bootstrap_admin

configure_logging()

logger = logging.getLogger(__name__)

_SKIP_LOG_PATHS = {"/health", "/"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        path = request.url.path
        if path in _SKIP_LOG_PATHS or not path.startswith("/api/"):
            return response

        forwarded_for = request.headers.get("X-Forwarded-For", "")
        ip = (
            forwarded_for.split(",")[0].strip()
            or request.headers.get("X-Real-IP")
            or (request.client.host if request.client else None)
        )

        user_id = getattr(request.state, "user_id", None)
        username = getattr(request.state, "username", None)

        try:
            async with AsyncSessionLocal() as db:
                db.add(RequestLog(
                    user_id=user_id,
                    username=username,
                    ip_address=ip,
                    method=request.method,
                    path=path,
                    status_code=response.status_code,
                ))
                await db.commit()
        except Exception:
            logger.exception("Failed to write request log")

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.event_log_service import log_event

    async with AsyncSessionLocal() as db:
        admin_created = await bootstrap_admin(
            db,
            email=settings.FIRST_ADMIN_EMAIL,
            username=settings.FIRST_ADMIN_USERNAME,
            password=settings.FIRST_ADMIN_PASSWORD,
        )
        if admin_created:
            await log_event(
                db, "system.first_admin_created",
                f"First admin '{settings.FIRST_ADMIN_USERNAME}' bootstrapped",
                level="warning",
                details={
                    "username": settings.FIRST_ADMIN_USERNAME,
                    "email": settings.FIRST_ADMIN_EMAIL,
                },
            )
        await log_event(
            db, "system.startup", "Application started",
            details={
                "version": "1.0.0",
                "first_admin_created": admin_created,
            },
        )
    yield


app = FastAPI(
    title="AI Code Reviewer",
    version="1.0.0",
    description=(
        "AI-powered GitLab code reviewer with configurable LLM backends"
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Serve the single-page UI
_ui_dir = Path(__file__).parent / "ui" / "templates"


@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
@app.get("/ui/{rest_of_path:path}", include_in_schema=False)
async def serve_ui():
    return FileResponse(_ui_dir / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
