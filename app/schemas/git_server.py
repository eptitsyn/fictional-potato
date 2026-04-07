import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.core.network import validate_http_base_url


class GitServerCreate(BaseModel):
    name: str
    base_url: str
    access_token: str
    is_active: bool = True

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return validate_http_base_url(value)


class GitServerUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    access_token: str | None = None
    is_active: bool | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_http_base_url(value)


class GitServerRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    base_url: str
    is_active: bool
    created_at: datetime


class GitServerProjectRead(BaseModel):
    id: int
    name: str
    path_with_namespace: str
    web_url: str | None = None
