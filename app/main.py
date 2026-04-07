import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.v1.router import api_router
from app.config import settings
from app.core.database import AsyncSessionLocal
from app.core.network import ensure_outbound_http_policy
from app.services.auth_service import bootstrap_admin

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bootstrap first admin on cold start
    async with AsyncSessionLocal() as db:
        await bootstrap_admin(
            db,
            email=settings.FIRST_ADMIN_EMAIL,
            username=settings.FIRST_ADMIN_USERNAME,
            password=settings.FIRST_ADMIN_PASSWORD,
        )
        try:
            await ensure_outbound_http_policy(db)
        except Exception:
            logger.exception("Failed to initialize outbound HTTP policy")
    yield


app = FastAPI(
    title="AI Code Reviewer",
    version="1.0.0",
    description="AI-powered GitLab code reviewer with configurable LLM backends",
    lifespan=lifespan,
)

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
