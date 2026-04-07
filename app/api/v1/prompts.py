import uuid

from fastapi import APIRouter

from app.dependencies import AdminUser, CurrentUser, DB, ReviewerUser
from app.schemas.prompt import PromptCreate, PromptRead, PromptUpdate
from app.services import prompt_service as svc

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("", response_model=list[PromptRead])
async def list_prompts(db: DB, current_user: CurrentUser):
    return await svc.list_prompts(db, current_user)


@router.post("", response_model=PromptRead, status_code=201)
async def create_prompt(data: PromptCreate, db: DB, current_user: ReviewerUser):
    return await svc.create_prompt(db, data, current_user)


@router.get("/{prompt_id}", response_model=PromptRead)
async def get_prompt(prompt_id: uuid.UUID, db: DB, current_user: CurrentUser):
    return await svc.get_prompt(db, prompt_id, current_user)


@router.put("/{prompt_id}", response_model=PromptRead)
async def update_prompt(prompt_id: uuid.UUID, data: PromptUpdate, db: DB, current_user: ReviewerUser):
    return await svc.update_prompt(db, prompt_id, data, current_user)


@router.delete("/{prompt_id}", status_code=204)
async def delete_prompt(prompt_id: uuid.UUID, db: DB, current_user: ReviewerUser):
    await svc.delete_prompt(db, prompt_id, current_user)


@router.post("/{prompt_id}/set-default", response_model=PromptRead)
async def set_default(prompt_id: uuid.UUID, db: DB, _: AdminUser):
    return await svc.set_global_default(db, prompt_id)
