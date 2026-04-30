"""add chat_session_id to project_memory_candidates

Revision ID: 20260429_0010
Revises: 20260412_0009
Create Date: 2026-04-29 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260429_0010"
down_revision = "20260412_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "chat_session_id", sa.String(36), nullable=True, server_default=""
            )
        )
    op.execute(
        "UPDATE project_memory_candidates SET chat_session_id = '' WHERE chat_session_id IS NULL"
    )
    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.alter_column("chat_session_id", nullable=False)
        batch_op.create_index(
            "ix_project_memory_candidates_chat_active",
            ["chat_session_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.drop_index("ix_project_memory_candidates_chat_active")
        batch_op.drop_column("chat_session_id")
