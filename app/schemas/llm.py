import uuid
from datetime import datetime

from pydantic import BaseModel, HttpUrl


class LLMEndpointCreate(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None  # write-only, never returned


class LLMEndpointUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class LLMEndpointRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    base_url: str
    is_active: bool
    created_at: datetime
    # api_key intentionally excluded


class LLMModelCreate(BaseModel):
    model_name: str
    display_name: str | None = None
    max_context_tokens: int = 8192
    temperature: float = 0.2
    is_active: bool = True
    is_global_default: bool = False


class LLMModelUpdate(BaseModel):
    display_name: str | None = None
    max_context_tokens: int | None = None
    temperature: float | None = None
    is_active: bool | None = None
    is_global_default: bool | None = None


class LLMModelRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    endpoint_id: uuid.UUID
    model_name: str
    display_name: str | None
    max_context_tokens: int
    temperature: float
    is_active: bool
    is_global_default: bool
    created_at: datetime
