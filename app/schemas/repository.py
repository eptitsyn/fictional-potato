import uuid
from datetime import datetime

from pydantic import BaseModel


class RepositoryCreate(BaseModel):
    git_server_id: uuid.UUID
    gitlab_project_id: int
    name: str
    webhook_secret: str  # write-only
    llm_model_id: uuid.UUID | None = None
    review_commits: bool = True
    review_mrs: bool = True


class RepositoryUpdate(BaseModel):
    git_server_id: uuid.UUID | None = None
    gitlab_project_id: int | None = None
    name: str | None = None
    webhook_secret: str | None = None
    llm_model_id: uuid.UUID | None = None
    review_commits: bool | None = None
    review_mrs: bool | None = None
    is_active: bool | None = None


class RepositoryRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    git_server_id: uuid.UUID
    git_server_name: str | None
    git_server_url: str | None
    gitlab_project_id: int
    name: str
    llm_model_id: uuid.UUID | None
    review_commits: bool
    review_mrs: bool
    is_active: bool
    created_at: datetime
    # tokens intentionally excluded
