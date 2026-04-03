"""add role to users

Revision ID: 20260403_0003
Revises: 20260330_0002
Create Date: 2026-04-03 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260403_0003"
down_revision = "20260330_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
