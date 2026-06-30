"""Add per-course question counts and webcam monitoring events.

Revision ID: 20260629_05
Revises: 20260626_04
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260629_05"
down_revision = "20260626_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "courses" in tables:
        columns = {item["name"] for item in inspector.get_columns("courses")}
        if "assessment_question_count" not in columns:
            op.add_column(
                "courses",
                sa.Column(
                    "assessment_question_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="20",
                ),
            )

    if "assessments" in tables:
        columns = {item["name"] for item in inspector.get_columns("assessments")}
        if "question_count" not in columns:
            op.add_column(
                "assessments",
                sa.Column(
                    "question_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="20",
                ),
            )

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "monitoring_events" not in tables:
        op.create_table(
            "monitoring_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("question_position", sa.Integer(), nullable=True),
            sa.Column("message", sa.String(length=300), nullable=True),
            sa.Column("corrected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_monitoring_events_assessment_id",
            "monitoring_events",
            ["assessment_id"],
        )
        op.create_index(
            "ix_monitoring_events_event_type",
            "monitoring_events",
            ["event_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "monitoring_events" in tables:
        op.drop_index("ix_monitoring_events_event_type", table_name="monitoring_events")
        op.drop_index("ix_monitoring_events_assessment_id", table_name="monitoring_events")
        op.drop_table("monitoring_events")

    inspector = sa.inspect(bind)
    if "assessments" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("assessments")}
        if "question_count" in columns:
            op.drop_column("assessments", "question_count")

    inspector = sa.inspect(bind)
    if "courses" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("courses")}
        if "assessment_question_count" in columns:
            op.drop_column("courses", "assessment_question_count")
