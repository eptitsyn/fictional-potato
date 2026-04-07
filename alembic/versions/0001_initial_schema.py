"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

user_role_enum = postgresql.ENUM(
    "admin", "reviewer", "viewer", name="user_role_enum", create_type=False
)
prompt_type_enum = postgresql.ENUM(
    "system", "commit_review", "mr_review", name="prompt_type_enum", create_type=False
)
trigger_type_enum = postgresql.ENUM("commit", "mr", name="trigger_type_enum", create_type=False)
review_status_enum = postgresql.ENUM(
    "pending", "running", "completed", "failed", name="review_status_enum", create_type=False
)
severity_enum = postgresql.ENUM(
    "info", "warning", "error", name="severity_enum", create_type=False
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Enums
    op.execute("CREATE TYPE user_role_enum AS ENUM ('admin', 'reviewer', 'viewer')")
    op.execute("CREATE TYPE prompt_type_enum AS ENUM ('system', 'commit_review', 'mr_review')")
    op.execute("CREATE TYPE trigger_type_enum AS ENUM ('commit', 'mr')")
    op.execute(
        "CREATE TYPE review_status_enum AS ENUM ('pending', 'running', 'completed', 'failed')"
    )
    op.execute("CREATE TYPE severity_enum AS ENUM ('info', 'warning', 'error')")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", user_role_enum, nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "llm_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("api_key_encrypted", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "llm_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("endpoint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("max_context_tokens", sa.Integer, nullable=False, server_default="8192"),
        sa.Column("temperature", sa.Float, nullable=False, server_default="0.2"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_global_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("endpoint_id", "model_name", name="uq_llm_model_endpoint_name"),
    )

    op.create_table(
        "repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("gitlab_project_id", sa.Integer, nullable=False, unique=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("gitlab_url", sa.String(500), nullable=False),
        sa.Column("gitlab_token_encrypted", sa.Text, nullable=False),
        sa.Column("webhook_secret_encrypted", sa.Text, nullable=False),
        sa.Column("llm_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_commits", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("review_mrs", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("prompt_type", prompt_type_enum, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_prompts_user_id", "prompts", ["user_id"])

    op.create_table(
        "review_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_type", trigger_type_enum, nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("mr_iid", sa.Integer, nullable=True),
        sa.Column(
            "status",
            review_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("triggered_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("llm_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_review_jobs_repository_id", "review_jobs", ["repository_id"])

    op.create_table(
        "review_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("review_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("line_number", sa.Integer, nullable=True),
        sa.Column("comment_body", sa.Text, nullable=False),
        sa.Column("severity", severity_enum, nullable=False, server_default="info"),
        sa.Column("gitlab_note_id", sa.String(100), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_review_comments_job_id", "review_comments", ["job_id"])


def downgrade() -> None:
    op.drop_table("review_comments")
    op.drop_table("review_jobs")
    op.drop_table("prompts")
    op.drop_table("repositories")
    op.drop_table("llm_models")
    op.drop_table("llm_endpoints")
    op.drop_table("users")
    op.execute("DROP TYPE severity_enum")
    op.execute("DROP TYPE review_status_enum")
    op.execute("DROP TYPE trigger_type_enum")
    op.execute("DROP TYPE prompt_type_enum")
    op.execute("DROP TYPE user_role_enum")
