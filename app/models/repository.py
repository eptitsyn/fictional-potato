import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "git_server_id",
            "gitlab_project_id",
            name="uq_repositories_git_server_project",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    git_server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("git_servers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    gitlab_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
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

    git_server: Mapped["GitServer"] = relationship(  # noqa: F821
        "GitServer", back_populates="repositories"
    )
    llm_model: Mapped["LLMModel | None"] = relationship(  # noqa: F821
        "LLMModel", back_populates="repositories"
    )
    review_jobs: Mapped[list["ReviewJob"]] = relationship(  # noqa: F821
        "ReviewJob", back_populates="repository", cascade="all, delete-orphan"
    )

    @property
    def git_server_name(self) -> str | None:
        return self.git_server.name if self.git_server else None

    @property
    def git_server_url(self) -> str | None:
        return self.git_server.base_url if self.git_server else None
