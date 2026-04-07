import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PromptCreate(BaseModel):
    prompt_type: Literal["system", "commit_review", "mr_review"]
    name: str
    content: str
    is_default: bool = False


class PromptUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    is_active: bool | None = None


class PromptRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID | None
    prompt_type: str
    name: str
    content: str
    is_default: bool
    is_active: bool
    created_at: datetime
