"""fix chat_runs message FK on delete set null

Revision ID: 20260407_0006
Revises: 20260406_0005
Create Date: 2026-04-07 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260407_0006"
down_revision = "20260406_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing FK constraints (no ON DELETE behaviour = RESTRICT by default)
    op.drop_constraint("chat_runs_final_user_message_id_fkey", "chat_runs", type_="foreignkey")
    op.drop_constraint("chat_runs_final_assistant_message_id_fkey", "chat_runs", type_="foreignkey")

    # Re-create with ON DELETE SET NULL so deleting messages doesn't block project deletion
    op.create_foreign_key(
        "chat_runs_final_user_message_id_fkey",
        "chat_runs", "chat_messages",
        ["final_user_message_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "chat_runs_final_assistant_message_id_fkey",
        "chat_runs", "chat_messages",
        ["final_assistant_message_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("chat_runs_final_user_message_id_fkey", "chat_runs", type_="foreignkey")
    op.drop_constraint("chat_runs_final_assistant_message_id_fkey", "chat_runs", type_="foreignkey")

    op.create_foreign_key(
        "chat_runs_final_user_message_id_fkey",
        "chat_runs", "chat_messages",
        ["final_user_message_id"], ["id"],
    )
    op.create_foreign_key(
        "chat_runs_final_assistant_message_id_fkey",
        "chat_runs", "chat_messages",
        ["final_assistant_message_id"], ["id"],
    )
