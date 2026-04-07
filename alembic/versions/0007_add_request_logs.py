"""Add request_logs table

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.String(150), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(2000), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_user_id", "request_logs", ["user_id"])
    op.create_index("ix_request_logs_timestamp", "request_logs", ["timestamp"])


def downgrade():
    op.drop_index("ix_request_logs_timestamp", table_name="request_logs")
    op.drop_index("ix_request_logs_user_id", table_name="request_logs")
    op.drop_table("request_logs")
