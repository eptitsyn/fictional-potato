import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    gitlab_project_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    gitlab_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Fernet-encrypted project-scoped access token
    gitlab_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Fernet-encrypted webhook secret
    webhook_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_commits: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_mrs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    llm_model: Mapped["LLMModel | None"] = relationship(  # noqa: F821
        "LLMModel", back_populates="repositories"
    )
    review_jobs: Mapped[list["ReviewJob"]] = relationship(  # noqa: F821
        "ReviewJob", back_populates="repository", cascade="all, delete-orphan"
    )
