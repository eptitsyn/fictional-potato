"""Add event_logs table

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-08 00:01:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.String(150), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_logs_event_type", "event_logs", ["event_type"])
    op.create_index("ix_event_logs_user_id", "event_logs", ["user_id"])
    op.create_index("ix_event_logs_timestamp", "event_logs", ["timestamp"])


def downgrade():
    op.drop_index("ix_event_logs_timestamp", table_name="event_logs")
    op.drop_index("ix_event_logs_user_id", table_name="event_logs")
    op.drop_index("ix_event_logs_event_type", table_name="event_logs")
    op.drop_table("event_logs")
