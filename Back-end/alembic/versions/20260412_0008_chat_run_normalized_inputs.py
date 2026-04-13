"""add canonical normalized inputs to chat runs

Revision ID: 20260412_0008
Revises: 20260412_0007
Create Date: 2026-04-12 00:15:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260412_0008"
down_revision = "20260412_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_runs") as batch_op:
        batch_op.add_column(sa.Column("normalized_inputs_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_runs") as batch_op:
        batch_op.drop_column("normalized_inputs_json")
