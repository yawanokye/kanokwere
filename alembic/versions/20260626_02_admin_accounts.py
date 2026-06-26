"""Add administrator-created accounts and password reset workflow.

Revision ID: 20260626_02
Revises: 20260626_01
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_02"
down_revision = "20260626_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" in tables:
        columns = {item["name"] for item in inspector.get_columns("users")}
        if "must_change_password" not in columns:
            op.add_column(
                "users",
                sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
            )
            op.alter_column("users", "must_change_password", server_default=None)
    if "password_reset_requests" not in tables:
        op.create_table(
            "password_reset_requests",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_password_reset_requests_user_id", "password_reset_requests", ["user_id"], unique=False)
        op.create_index("ix_password_reset_requests_email", "password_reset_requests", ["email"], unique=False)


def downgrade() -> None:
    pass
