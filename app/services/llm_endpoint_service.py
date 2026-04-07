import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.network import ensure_outbound_http_policy, resolve_endpoint_base_url
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import decrypt, encrypt
from app.models.llm import LLMEndpoint, LLMModel
from app.models.user import User
from app.schemas.llm import LLMEndpointCreate, LLMEndpointUpdate, LLMModelCreate, LLMModelUpdate


def _build_endpoint_headers(endpoint: LLMEndpoint) -> dict[str, str]:
    api_key = decrypt(endpoint.api_key_encrypted) if endpoint.api_key_encrypted else None
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _extract_available_model_names(payload: object) -> list[str]:
    raw_models = payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            raw_models = payload["data"]
        elif isinstance(payload.get("models"), list):
            raw_models = payload["models"]
        else:
            raw_models = []

    if not isinstance(raw_models, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        model_name: str | None = None
        if isinstance(item, str):
            model_name = item
        elif isinstance(item, dict):
            candidate = item.get("id") or item.get("model_name") or item.get("name")
            if isinstance(candidate, str):
                model_name = candidate

        if not model_name:
            continue

        normalized = model_name.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            names.append(normalized)

    return sorted(names, key=str.casefold)


def _extract_chat_completion_preview(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text = item["text"].strip()
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    return None


async def _fetch_endpoint_models_payload(endpoint: LLMEndpoint) -> object:
    try:
        resolved_base_url = resolve_endpoint_base_url(endpoint.base_url)
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(
                f"{resolved_base_url.rstrip('/')}/models",
                headers=_build_endpoint_headers(endpoint),
            )
            response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or exc.response.reason_phrase
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Endpoint returned {exc.response.status_code} while listing models"
                + (f": {detail}" if detail else "")
            ),
        ) from exc
    except httpx.HTTPError as exc:
        hint = ""
        if resolved_base_url != endpoint.base_url:
            hint = f" Tried {resolved_base_url} from inside Docker."
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch models from endpoint: {exc}.{hint}".strip(),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Endpoint returned invalid JSON for /models",
        ) from exc


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
    await ensure_outbound_http_policy(db)
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
    await ensure_outbound_http_policy(db)
    return ep


async def delete_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> None:
    ep = await get_endpoint(db, endpoint_id)
    await db.delete(ep)
    await db.commit()
    await ensure_outbound_http_policy(db)


async def test_endpoint(db: AsyncSession, endpoint_id: uuid.UUID) -> dict:
    ep = await get_endpoint(db, endpoint_id)
    await ensure_outbound_http_policy(db)

    try:
        payload = await _fetch_endpoint_models_payload(ep)
        return {
            "status": "ok",
            "http_status": status.HTTP_200_OK,
            "models_found": len(_extract_available_model_names(payload)),
        }
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


async def list_available_models_for_endpoint(
    db: AsyncSession, endpoint_id: uuid.UUID
) -> list[dict[str, str]]:
    endpoint = await get_endpoint(db, endpoint_id)
    await ensure_outbound_http_policy(db)
    payload = await _fetch_endpoint_models_payload(endpoint)
    return [{"model_name": model_name} for model_name in _extract_available_model_names(payload)]


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


async def test_model(db: AsyncSession, model_id: uuid.UUID) -> dict:
    model = await get_model(db, model_id)
    await ensure_outbound_http_policy(db)
    resolved_base_url = resolve_endpoint_base_url(model.endpoint.base_url)
    headers = {"Content-Type": "application/json", **_build_endpoint_headers(model.endpoint)}
    payload = {
        "model": model.model_name,
        "messages": [{"role": "user", "content": "Ответь ровно: ОК."}],
        "temperature": 0,
        "max_tokens": 8,
    }

    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{resolved_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        response_payload = response.json()
        preview = _extract_chat_completion_preview(response_payload)
        return {
            "status": "ok",
            "http_status": response.status_code,
            "model_name": model.model_name,
            "response_preview": preview,
        }
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or exc.response.reason_phrase
        return {
            "status": "error",
            "http_status": exc.response.status_code,
            "detail": (
                f"Model test failed with {exc.response.status_code}"
                + (f": {detail}" if detail else "")
            ),
        }
    except httpx.HTTPError as exc:
        hint = ""
        if resolved_base_url != model.endpoint.base_url:
            hint = f" Tried {resolved_base_url} from inside Docker."
        return {
            "status": "error",
            "detail": f"Failed to reach model endpoint: {exc}.{hint}".strip(),
        }
    except ValueError:
        return {
            "status": "error",
            "detail": "Model endpoint returned invalid JSON for /chat/completions",
        }


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

    if "model_name" in update_data and update_data["model_name"] != model.model_name:
        existing = await db.execute(
            select(LLMModel).where(
                LLMModel.endpoint_id == model.endpoint_id,
                LLMModel.model_name == update_data["model_name"],
                LLMModel.id != model_id,
            )
        )
        if existing.first():
            raise ConflictError(
                f"Model '{update_data['model_name']}' already exists for this endpoint"
            )

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
