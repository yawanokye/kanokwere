"""Repair legacy user-table columns required by account management.

Revision ID: 20260626_04
Revises: 20260626_03
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_04"
down_revision = "20260626_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "users" not in set(inspector.get_table_names()):
        return

    columns = {item["name"] for item in inspector.get_columns("users")}
    additions: list[sa.Column] = [
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("setup_code_hash", sa.String(length=64), nullable=True),
        sa.Column("setup_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_pin_hash", sa.String(length=255), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    ]

    for column in additions:
        if column.name not in columns:
            op.add_column("users", column)

    # Staff ID is no longer used. Keep a legacy column, when present, but make
    # sure it cannot block creation of new lecturer accounts.
    inspector = sa.inspect(bind)
    user_columns = {item["name"]: item for item in inspector.get_columns("users")}
    staff_id = user_columns.get("staff_id")
    if staff_id and not staff_id.get("nullable", True) and bind.dialect.name != "sqlite":
        op.alter_column(
            "users",
            "staff_id",
            existing_type=staff_id["type"],
            nullable=True,
        )


def downgrade() -> None:
    # These fields hold account access and recovery data and are retained.
    pass
