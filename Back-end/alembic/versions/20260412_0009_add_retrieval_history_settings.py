"""add retrieval and history tuning settings

Revision ID: 20260412_0009
Revises: 20260412_0008
Create Date: 2026-04-12 00:09:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260412_0009"
down_revision = "20260412_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("retrieval_initial_fetch", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("retrieval_final_top_k", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("retrieval_neighbor_pages", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("retrieval_max_expanded", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("retrieval_source_profile_sample", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("history_max_recent_turns", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("history_summarize_turn_threshold", sa.Integer(), nullable=True))
    op.add_column("app_settings", sa.Column("history_summarize_char_threshold", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "history_summarize_char_threshold")
    op.drop_column("app_settings", "history_summarize_turn_threshold")
    op.drop_column("app_settings", "history_max_recent_turns")
    op.drop_column("app_settings", "retrieval_source_profile_sample")
    op.drop_column("app_settings", "retrieval_max_expanded")
    op.drop_column("app_settings", "retrieval_neighbor_pages")
    op.drop_column("app_settings", "retrieval_final_top_k")
    op.drop_column("app_settings", "retrieval_initial_fetch")
