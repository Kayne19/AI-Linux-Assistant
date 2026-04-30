"""add ingest_identity_normalizer settings columns

Revision ID: 20260429_0010
Revises: 20260412_0009
Create Date: 2026-04-29 00:10:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260429_0010"
down_revision = "20260412_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("ingest_identity_normalizer_provider", sa.Text(), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("ingest_identity_normalizer_model", sa.Text(), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column(
            "ingest_identity_normalizer_reasoning_effort", sa.Text(), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "ingest_identity_normalizer_reasoning_effort")
    op.drop_column("app_settings", "ingest_identity_normalizer_model")
    op.drop_column("app_settings", "ingest_identity_normalizer_provider")
