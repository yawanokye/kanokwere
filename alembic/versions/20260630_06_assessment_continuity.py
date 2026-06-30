"""Add assessment heartbeat, interruption, locking, and secure resume fields.

Revision ID: 20260630_06
Revises: 20260629_05
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260630_06"
down_revision = "20260629_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "assessments" not in set(inspector.get_table_names()):
        return

    columns = {item["name"] for item in inspector.get_columns("assessments")}
    additions = [
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interruption_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interruption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_offline_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resume_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_interruption_reason", sa.String(length=80), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_reason", sa.String(length=120), nullable=True),
        sa.Column("interruption_excused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interruption_note", sa.Text(), nullable=True),
        sa.Column("client_instance_id", sa.String(length=120), nullable=True),
        sa.Column("camera_reverification_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("assessments", column)

    op.execute(
        sa.text(
            "UPDATE assessments SET last_seen_at = started_at "
            "WHERE last_seen_at IS NULL AND status IN ('in_progress', 'interrupted')"
        )
    )


def downgrade() -> None:
    # Continuity records are retained intentionally because they form part of
    # the assessment audit trail.
    pass
