import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.prompt import Prompt
from app.models.user import User
from app.schemas.prompt import PromptCreate, PromptUpdate
from app.services.event_log_service import log_event

PromptType = Literal["system", "commit_review", "mr_review"]


async def resolve_effective_prompt(
    db: AsyncSession, user_id: uuid.UUID | None, prompt_type: PromptType
) -> Prompt | None:
    """
    Cascade: user-specific → global default → None
    """
    if user_id:
        result = await db.execute(
            select(Prompt).where(
                Prompt.user_id == user_id,
                Prompt.prompt_type == prompt_type,
                Prompt.is_active == True,  # noqa: E712
            ).order_by(Prompt.created_at.desc())
        )
        prompt = result.scalar_one_or_none()
        if prompt:
            return prompt

    # Fall back to global default
    result = await db.execute(
        select(Prompt).where(
            Prompt.user_id == None,  # noqa: E711
            Prompt.prompt_type == prompt_type,
            Prompt.is_default,
            Prompt.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def list_prompts(
    db: AsyncSession, current_user: User
) -> list[Prompt]:
    if current_user.role == "admin":
        result = await db.execute(select(Prompt).order_by(Prompt.created_at.desc()))
    else:
        result = await db.execute(
            select(Prompt).where(
                (Prompt.user_id == current_user.id) | (Prompt.user_id == None)  # noqa: E711
            ).order_by(Prompt.created_at.desc())
        )
    return list(result.scalars().all())


async def get_prompt(db: AsyncSession, prompt_id: uuid.UUID, actor: User) -> Prompt:
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise NotFoundError(f"Prompt {prompt_id} not found")
    if actor.role != "admin" and prompt.user_id != actor.id and prompt.user_id is not None:
        raise ForbiddenError("Access denied to this prompt")
    return prompt


async def create_prompt(
    db: AsyncSession, data: PromptCreate, owner: User
) -> Prompt:
    user_id = None if owner.role == "admin" and data.is_default else owner.id

    if data.is_default and user_id is None:
        # Unset other global defaults of same type
        result = await db.execute(
            select(Prompt).where(
                Prompt.user_id == None,  # noqa: E711
                Prompt.prompt_type == data.prompt_type,
                Prompt.is_default == True,
            )
        )
        for p in result.scalars().all():
            p.is_default = False

    prompt = Prompt(
        user_id=user_id,
        prompt_type=data.prompt_type,
        name=data.name,
        content=data.content,
        is_default=data.is_default if user_id is None else False,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)
    await log_event(
        db, "prompt.created", f"Prompt '{prompt.name}' created",
        user_id=owner.id,
        username=owner.username,
        details={
            "prompt_id": str(prompt.id),
            "name": prompt.name,
            "prompt_type": prompt.prompt_type,
            "is_global": user_id is None,
        },
    )
    return prompt


async def update_prompt(
    db: AsyncSession, prompt_id: uuid.UUID, data: PromptUpdate, actor: User
) -> Prompt:
    prompt = await get_prompt(db, prompt_id, actor)

    if prompt.user_id is not None and prompt.user_id != actor.id and actor.role != "admin":
        raise ForbiddenError("Cannot update another user's prompt")

    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(prompt, field, value)
    await db.commit()
    await db.refresh(prompt)
    await log_event(
        db, "prompt.updated", f"Prompt '{prompt.name}' updated",
        user_id=actor.id,
        username=actor.username,
        details={
            "prompt_id": str(prompt.id),
            "name": prompt.name,
            "changed_fields": list(update_data.keys()),
        },
    )
    return prompt


async def delete_prompt(db: AsyncSession, prompt_id: uuid.UUID, actor: User) -> None:
    prompt = await get_prompt(db, prompt_id, actor)
    if prompt.user_id is None and actor.role != "admin":
        raise ForbiddenError("Only admins can delete global prompts")
    name = prompt.name
    await db.delete(prompt)
    await db.commit()
    await log_event(
        db, "prompt.deleted", f"Prompt '{name}' deleted",
        level="warning",
        user_id=actor.id,
        username=actor.username,
        details={"prompt_id": str(prompt_id), "name": name},
    )


async def set_global_default(
    db: AsyncSession, prompt_id: uuid.UUID
) -> Prompt:
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    prompt = result.scalar_one_or_none()
    if not prompt:
        raise NotFoundError(f"Prompt {prompt_id} not found")
    if prompt.user_id is not None:
        raise ForbiddenError("Only global prompts can be set as default")

    # Unset current default of same type
    existing = await db.execute(
        select(Prompt).where(
            Prompt.user_id == None,  # noqa: E711
            Prompt.prompt_type == prompt.prompt_type,
            Prompt.is_default,
            Prompt.id != prompt_id,
        )
    )
    prev_default_id: str | None = None
    for p in existing.scalars().all():
        prev_default_id = str(p.id)
        p.is_default = False

    prompt.is_default = True
    await db.commit()
    await db.refresh(prompt)
    await log_event(
        db, "prompt.set_default", f"Prompt '{prompt.name}' set as global default",
        details={
            "prompt_id": str(prompt.id),
            "name": prompt.name,
            "prompt_type": prompt.prompt_type,
            "prev_default_id": prev_default_id,
        },
    )
    return prompt
