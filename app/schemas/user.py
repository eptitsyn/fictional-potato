import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    role: Literal["admin", "reviewer", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    role: Literal["admin", "reviewer", "viewer"] | None = None
    is_active: bool | None = None


class UserPasswordChange(BaseModel):
    current_password: str | None = None  # not required for admin overrides
    new_password: str


class UserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
