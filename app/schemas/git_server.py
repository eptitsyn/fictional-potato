import uuid
from datetime import datetime

from pydantic import BaseModel


class GitServerCreate(BaseModel):
    name: str
    base_url: str
    access_token: str
    is_active: bool = True


class GitServerUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    access_token: str | None = None
    is_active: bool | None = None


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
