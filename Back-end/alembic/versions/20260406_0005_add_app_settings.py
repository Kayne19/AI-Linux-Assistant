"""add app_settings singleton table

Revision ID: 20260406_0005
Revises: 20260403_0004
Create Date: 2026-04-06 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260406_0005"
down_revision = "20260403_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        # Core pipeline
        sa.Column("classifier_provider", sa.Text(), nullable=True),
        sa.Column("classifier_model", sa.Text(), nullable=True),
        sa.Column("classifier_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("contextualizer_provider", sa.Text(), nullable=True),
        sa.Column("contextualizer_model", sa.Text(), nullable=True),
        sa.Column("contextualizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("responder_provider", sa.Text(), nullable=True),
        sa.Column("responder_model", sa.Text(), nullable=True),
        sa.Column("responder_reasoning_effort", sa.Text(), nullable=True),
        # Magi full
        sa.Column("magi_eager_provider", sa.Text(), nullable=True),
        sa.Column("magi_eager_model", sa.Text(), nullable=True),
        sa.Column("magi_eager_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_provider", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_model", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_historian_provider", sa.Text(), nullable=True),
        sa.Column("magi_historian_model", sa.Text(), nullable=True),
        sa.Column("magi_historian_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_provider", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_model", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_reasoning_effort", sa.Text(), nullable=True),
        # Magi lite
        sa.Column("magi_lite_eager_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_eager_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_eager_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_reasoning_effort", sa.Text(), nullable=True),
        # Utility (advanced)
        sa.Column("history_summarizer_provider", sa.Text(), nullable=True),
        sa.Column("history_summarizer_model", sa.Text(), nullable=True),
        sa.Column("history_summarizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("context_summarizer_provider", sa.Text(), nullable=True),
        sa.Column("context_summarizer_model", sa.Text(), nullable=True),
        sa.Column("context_summarizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("memory_extractor_provider", sa.Text(), nullable=True),
        sa.Column("memory_extractor_model", sa.Text(), nullable=True),
        sa.Column("memory_extractor_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("registry_updater_provider", sa.Text(), nullable=True),
        sa.Column("registry_updater_model", sa.Text(), nullable=True),
        sa.Column("registry_updater_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_provider", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_model", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("chat_namer_provider", sa.Text(), nullable=True),
        sa.Column("chat_namer_model", sa.Text(), nullable=True),
        sa.Column("chat_namer_reasoning_effort", sa.Text(), nullable=True),
        # Metadata
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="app_settings_singleton"),
    )
    op.execute("INSERT INTO app_settings (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("app_settings")
