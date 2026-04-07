import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import decrypt, encrypt
from app.models.llm import LLMEndpoint, LLMModel
from app.models.user import User
from app.schemas.llm import LLMEndpointCreate, LLMEndpointUpdate, LLMModelCreate, LLMModelUpdate


# ── Endpoints ──────────────────────────────────────────────────────────────────


async def list_endpoints(db: AsyncSession) -> list[LLMEndpoint]:
    result = await db.execute(select(LLMEndpoint).order_by(LLMEndpoint.name))
    return list(result.scalars().all())


async def get_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> LLMEndpoint:
    result = await db.execute(
        select(LLMEndpoint)
        .where(LLMEndpoint.id == endpoint_id)
        .options(selectinload(LLMEndpoint.models))
    )
    ep = result.scalar_one_or_none()
    if not ep:
        raise NotFoundError(f"LLM endpoint {endpoint_id} not found")
    return ep


async def create_endpoint(
    db: AsyncSession, data: LLMEndpointCreate, creator: User
) -> LLMEndpoint:
    existing = await db.execute(select(LLMEndpoint).where(LLMEndpoint.name == data.name))
    if existing.first():
        raise ConflictError(f"Endpoint named '{data.name}' already exists")

    ep = LLMEndpoint(
        name=data.name,
        base_url=data.base_url,
        api_key_encrypted=encrypt(data.api_key) if data.api_key else None,
        created_by_id=creator.id,
    )
    db.add(ep)
    await db.commit()
    await db.refresh(ep)
    return ep


async def update_endpoint(
    db: AsyncSession, endpoint_id: uuid.UUID, data: LLMEndpointUpdate
) -> LLMEndpoint:
    ep = await get_endpoint(db, endpoint_id)
    update_data = data.model_dump(exclude_none=True)
    if "api_key" in update_data:
        ep.api_key_encrypted = encrypt(update_data.pop("api_key"))
    for field, value in update_data.items():
        setattr(ep, field, value)
    await db.commit()
    await db.refresh(ep)
    return ep


async def delete_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> None:
    ep = await get_endpoint(db, endpoint_id)
    await db.delete(ep)
    await db.commit()


async def test_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> dict:
    ep = await get_endpoint(db, endpoint_id)
    api_key = decrypt(ep.api_key_encrypted) if ep.api_key_encrypted else None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ep.base_url.rstrip('/')}/models", headers=headers)
        return {"status": "ok", "http_status": r.status_code}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def get_decrypted_api_key(endpoint: LLMEndpoint) -> str | None:
    if endpoint.api_key_encrypted:
        return decrypt(endpoint.api_key_encrypted)
    return None


# ── Models ─────────────────────────────────────────────────────────────────────


async def list_models_for_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> list[LLMModel]:
    await get_endpoint(db, endpoint_id)  # ensure endpoint exists
    result = await db.execute(
        select(LLMModel).where(LLMModel.endpoint_id == endpoint_id).order_by(LLMModel.model_name)
    )
    return list(result.scalars().all())


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> LLMModel:
    result = await db.execute(
        select(LLMModel).where(LLMModel.id == model_id).options(selectinload(LLMModel.endpoint))
    )
    model = result.scalar_one_or_none()
    if not model:
        raise NotFoundError(f"LLM model {model_id} not found")
    return model


async def get_global_default_model(db: AsyncSession) -> LLMModel | None:
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.is_global_default == True, LLMModel.is_active == True)  # noqa: E712
        .options(selectinload(LLMModel.endpoint))
    )
    return result.scalar_one_or_none()


async def create_model(
    db: AsyncSession, endpoint_id: uuid.UUID, data: LLMModelCreate
) -> LLMModel:
    await get_endpoint(db, endpoint_id)
    existing = await db.execute(
        select(LLMModel).where(
            LLMModel.endpoint_id == endpoint_id,
            LLMModel.model_name == data.model_name,
        )
    )
    if existing.first():
        raise ConflictError(
            f"Model '{data.model_name}' already exists for this endpoint"
        )

    if data.is_global_default:
        # Unset other global defaults
        result = await db.execute(
            select(LLMModel).where(LLMModel.is_global_default == True)  # noqa: E712
        )
        for m in result.scalars().all():
            m.is_global_default = False

    model = LLMModel(endpoint_id=endpoint_id, **data.model_dump())
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


async def update_model(
    db: AsyncSession, model_id: uuid.UUID, data: LLMModelUpdate
) -> LLMModel:
    model = await get_model(db, model_id)
    update_data = data.model_dump(exclude_none=True)

    if update_data.get("is_global_default"):
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.is_global_default == True,  # noqa: E712
                LLMModel.id != model_id,
            )
        )
        for m in result.scalars().all():
            m.is_global_default = False

    for field, value in update_data.items():
        setattr(model, field, value)
    await db.commit()
    await db.refresh(model)
    return model


async def delete_model(db: AsyncSession, model_id: uuid.UUID) -> None:
    model = await get_model(db, model_id)
    await db.delete(model)
    await db.commit()
