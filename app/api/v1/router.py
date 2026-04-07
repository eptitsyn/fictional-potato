from fastapi import APIRouter

from app.api.v1 import (
    auth,
    event_logs,
    git_servers,
    llm_endpoints,
    prompts,
    repositories,
    request_logs,
    reviews,
    users,
    webhooks,
    workers,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(llm_endpoints.router)
api_router.include_router(llm_endpoints.router_models)
api_router.include_router(git_servers.router)
api_router.include_router(repositories.router)
api_router.include_router(prompts.router)
api_router.include_router(reviews.router)
api_router.include_router(webhooks.router)
api_router.include_router(workers.router)
api_router.include_router(request_logs.router)
api_router.include_router(event_logs.router)
