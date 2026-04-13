"""preserve superseded project memory candidate history

Revision ID: 20260412_0007
Revises: 20260407_0006
Create Date: 2026-04-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260412_0007"
down_revision = "20260407_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        "UPDATE project_memory_candidates "
        "SET created_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
        "WHERE created_at IS NULL"
    )

    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.alter_column("created_at", nullable=False)
        batch_op.drop_constraint("uq_project_memory_candidate_key", type_="unique")
        batch_op.create_index(
            "ix_project_memory_candidates_project_status_key",
            ["project_id", "status", "item_key"],
            unique=False,
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM project_memory_candidates
        WHERE id IN (
            SELECT id FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id, item_type, item_key, status
                        ORDER BY updated_at DESC, created_at DESC, id DESC
                    ) AS row_num
                FROM project_memory_candidates
            ) ranked
            WHERE ranked.row_num > 1
        )
        """
    )

    with op.batch_alter_table("project_memory_candidates") as batch_op:
        batch_op.drop_index("ix_project_memory_candidates_project_status_key")
        batch_op.create_unique_constraint(
            "uq_project_memory_candidate_key",
            ["project_id", "item_type", "item_key", "status"],
        )
        batch_op.drop_column("created_at")
