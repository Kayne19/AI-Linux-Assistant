"""add auth0 identity fields

Revision ID: 20260403_0004
Revises: 20260403_0003
Create Date: 2026-04-03 00:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260403_0004"
down_revision = "20260403_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "username", existing_type=sa.String(length=120), nullable=True)
    op.add_column("users", sa.Column("auth_provider", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("auth_subject", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_auth_provider", "users", ["auth_provider"], unique=False)
    op.create_index("ix_users_auth_subject", "users", ["auth_subject"], unique=False)
    op.create_unique_constraint(
        "uq_users_auth_provider_subject",
        "users",
        ["auth_provider", "auth_subject"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_auth_provider_subject", "users", type_="unique")
    op.drop_index("ix_users_auth_subject", table_name="users")
    op.drop_index("ix_users_auth_provider", table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "display_name")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "email")
    op.drop_column("users", "auth_subject")
    op.drop_column("users", "auth_provider")
    op.alter_column("users", "username", existing_type=sa.String(length=120), nullable=False)
