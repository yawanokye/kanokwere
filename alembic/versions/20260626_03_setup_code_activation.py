"""Add no-email lecturer activation and recovery PIN fields.

Revision ID: 20260626_03
Revises: 20260626_02
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_03"
down_revision = "20260626_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    columns = {item["name"] for item in inspector.get_columns("users")}
    additions = [
        ("setup_code_hash", sa.String(length=64)),
        ("setup_code_expires_at", sa.DateTime(timezone=True)),
        ("recovery_pin_hash", sa.String(length=255)),
        ("activated_at", sa.DateTime(timezone=True)),
    ]
    for name, column_type in additions:
        if name not in columns:
            op.add_column("users", sa.Column(name, column_type, nullable=True))

    # Staff ID is intentionally no longer used by the application. The legacy
    # column is retained, when present, to avoid destructive production changes.
    op.execute(
        sa.text(
            "UPDATE users SET account_status = 'pending_activation' "
            "WHERE account_status = 'pending'"
        )
    )


def downgrade() -> None:
    # Account recovery data is retained deliberately to avoid locking users out.
    pass
