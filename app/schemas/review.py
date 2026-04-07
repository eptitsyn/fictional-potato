import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ReviewTriggerRequest(BaseModel):
    repository_id: uuid.UUID
    trigger_type: Literal["commit", "mr"]
    commit_sha: str | None = None
    mr_iid: int | None = None


class ReviewCommentRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    job_id: uuid.UUID
    file_path: str | None
    line_number: int | None
    line_end: int | None
    comment_body: str
    severity: str
    gitlab_note_id: str | None
    posted_at: datetime | None
    created_at: datetime


class ReviewJobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    repository_id: uuid.UUID
    trigger_type: str
    commit_sha: str | None
    mr_iid: int | None
    status: str
    triggered_by_id: uuid.UUID | None
    llm_model_id: uuid.UUID | None
    prompt_id: uuid.UUID | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ReviewJobDetailRead(ReviewJobRead):
    comments: list[ReviewCommentRead] = []


class WebhookPayload(BaseModel):
    object_kind: str
    project: dict
    commits: list[dict] | None = None
    object_attributes: dict | None = None
