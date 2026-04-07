"""Split GitLab servers from repositories

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-07 00:00:00.000000
"""

import uuid
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _build_server_name(base_url: str, existing_names: set[str]) -> str:
    hostname = urlparse(base_url).hostname or "git-server"
    candidate = hostname
    suffix = 2
    while candidate in existing_names:
        candidate = f"{hostname} {suffix}"
        suffix += 1
    existing_names.add(candidate)
    return candidate


def upgrade() -> None:
    op.create_table(
        "git_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("access_token_encrypted", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.add_column(
        "repositories",
        sa.Column("git_server_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_repositories_git_server_id",
        "repositories",
        "git_servers",
        ["git_server_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, gitlab_url, gitlab_token_encrypted, created_at, updated_at
            FROM repositories
            ORDER BY created_at, id
            """
        )
    ).mappings()

    server_ids_by_key: dict[tuple[str, str], uuid.UUID] = {}
    existing_names: set[str] = set()

    for row in rows:
        key = (row["gitlab_url"], row["gitlab_token_encrypted"])
        server_id = server_ids_by_key.get(key)
        if server_id is None:
            server_id = uuid.uuid4()
            server_ids_by_key[key] = server_id
            connection.execute(
                sa.text(
                    """
                    INSERT INTO git_servers (
                        id,
                        name,
                        base_url,
                        access_token_encrypted,
                        is_active,
                        created_at,
                        updated_at
                    ) VALUES (
                        :id,
                        :name,
                        :base_url,
                        :access_token_encrypted,
                        true,
                        :created_at,
                        :updated_at
                    )
                    """
                ),
                {
                    "id": server_id,
                    "name": _build_server_name(row["gitlab_url"], existing_names),
                    "base_url": row["gitlab_url"],
                    "access_token_encrypted": row["gitlab_token_encrypted"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
            )

        connection.execute(
            sa.text(
                "UPDATE repositories SET git_server_id = :git_server_id WHERE id = :repo_id"
            ),
            {"git_server_id": server_id, "repo_id": row["id"]},
        )

    op.alter_column("repositories", "git_server_id", nullable=False)
    op.drop_constraint(
        "repositories_gitlab_project_id_key",
        "repositories",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_repositories_git_server_project",
        "repositories",
        ["git_server_id", "gitlab_project_id"],
    )
    op.drop_column("repositories", "gitlab_token_encrypted")
    op.drop_column("repositories", "gitlab_url")


def downgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("gitlab_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "repositories",
        sa.Column("gitlab_token_encrypted", sa.Text, nullable=True),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE repositories AS r
            SET
                gitlab_url = gs.base_url,
                gitlab_token_encrypted = gs.access_token_encrypted
            FROM git_servers AS gs
            WHERE gs.id = r.git_server_id
            """
        )
    )

    op.alter_column("repositories", "gitlab_url", nullable=False)
    op.alter_column("repositories", "gitlab_token_encrypted", nullable=False)
    op.drop_constraint(
        "uq_repositories_git_server_project",
        "repositories",
        type_="unique",
    )
    op.create_unique_constraint(
        "repositories_gitlab_project_id_key",
        "repositories",
        ["gitlab_project_id"],
    )
    op.drop_constraint("fk_repositories_git_server_id", "repositories", type_="foreignkey")
    op.drop_column("repositories", "git_server_id")
    op.drop_table("git_servers")
