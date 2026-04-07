import uuid

from fastapi import APIRouter

from app.dependencies import AdminUser, DB, ReviewerUser
from app.schemas.llm import (
    LLMEndpointAvailableModelRead,
    LLMEndpointCreate,
    LLMEndpointRead,
    LLMEndpointUpdate,
    LLMModelCreate,
    LLMModelRead,
    LLMModelUpdate,
)
from app.services import llm_endpoint_service as svc

router = APIRouter(prefix="/llm-endpoints", tags=["llm-endpoints"])


@router.get("", response_model=list[LLMEndpointRead])
async def list_endpoints(db: DB, _: ReviewerUser):
    return await svc.list_endpoints(db)


@router.post("", response_model=LLMEndpointRead, status_code=201)
async def create_endpoint(data: LLMEndpointCreate, db: DB, actor: AdminUser):
    return await svc.create_endpoint(db, data, actor)


@router.get("/{endpoint_id}", response_model=LLMEndpointRead)
async def get_endpoint(endpoint_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.get_endpoint(db, endpoint_id)


@router.put("/{endpoint_id}", response_model=LLMEndpointRead)
async def update_endpoint(endpoint_id: uuid.UUID, data: LLMEndpointUpdate, db: DB, _: AdminUser):
    return await svc.update_endpoint(db, endpoint_id, data)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(endpoint_id: uuid.UUID, db: DB, _: AdminUser):
    await svc.delete_endpoint(db, endpoint_id)


@router.post("/{endpoint_id}/test")
async def test_endpoint(endpoint_id: uuid.UUID, db: DB, _: AdminUser):
    return await svc.test_endpoint(db, endpoint_id)


# ── Models nested under endpoint ───────────────────────────────────────────────

@router.get("/{endpoint_id}/models", response_model=list[LLMModelRead])
async def list_models(endpoint_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.list_models_for_endpoint(db, endpoint_id)


@router.get(
    "/{endpoint_id}/available-models",
    response_model=list[LLMEndpointAvailableModelRead],
)
async def list_available_models(endpoint_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.list_available_models_for_endpoint(db, endpoint_id)


@router.post("/{endpoint_id}/models", response_model=LLMModelRead, status_code=201)
async def create_model(endpoint_id: uuid.UUID, data: LLMModelCreate, db: DB, _: AdminUser):
    return await svc.create_model(db, endpoint_id, data)


router_models = APIRouter(prefix="/llm-models", tags=["llm-models"])


@router_models.get("/{model_id}", response_model=LLMModelRead)
async def get_model(model_id: uuid.UUID, db: DB, _: ReviewerUser):
    return await svc.get_model(db, model_id)


@router_models.post("/{model_id}/test")
async def test_model(model_id: uuid.UUID, db: DB, _: AdminUser):
    return await svc.test_model(db, model_id)


@router_models.put("/{model_id}", response_model=LLMModelRead)
async def update_model(model_id: uuid.UUID, data: LLMModelUpdate, db: DB, _: AdminUser):
    return await svc.update_model(db, model_id, data)


@router_models.delete("/{model_id}", status_code=204)
async def delete_model(model_id: uuid.UUID, db: DB, _: AdminUser):
    await svc.delete_model(db, model_id)
