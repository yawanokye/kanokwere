"""Phase 3 AI policy and assessment evidence profile.

Revision ID: 20260831_09
Revises: 20260717_08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_09"
down_revision = "20260717_08"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    existing = {item["name"] for item in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    existing = {item["name"] for item in inspector.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _add_column_if_missing(
        "courses",
        sa.Column(
            "ai_use_policy",
            sa.String(length=40),
            nullable=False,
            server_default="permitted_with_disclosure",
        ),
    )
    _add_column_if_missing(
        "courses",
        sa.Column("ai_policy_guidance", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "courses",
        sa.Column("live_exam_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column_if_missing(
        "courses",
        sa.Column("live_exam_title", sa.String(length=240), nullable=True),
    )
    _add_column_if_missing(
        "courses",
        sa.Column("live_exam_instructions", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "assessments",
        sa.Column("assurance_decision", sa.String(length=50), nullable=True),
    )
    _add_column_if_missing(
        "assessments",
        sa.Column("assurance_decision_note", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "assessments",
        sa.Column(
            "assurance_decided_by",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _add_column_if_missing(
        "assessments",
        sa.Column("assurance_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    _create_index_if_missing(
        "ix_assessments_assurance_decided_by",
        "assessments",
        ["assurance_decided_by"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    assessment_columns = {item["name"] for item in inspector.get_columns("assessments")}
    course_columns = {item["name"] for item in inspector.get_columns("courses")}
    indexes = {item["name"] for item in inspector.get_indexes("assessments")}
    if "ix_assessments_assurance_decided_by" in indexes:
        op.drop_index("ix_assessments_assurance_decided_by", table_name="assessments")
    for column in (
        "assurance_decided_at",
        "assurance_decided_by",
        "assurance_decision_note",
        "assurance_decision",
    ):
        if column in assessment_columns:
            op.drop_column("assessments", column)
    for column in (
        "live_exam_instructions", "live_exam_title", "live_exam_monitoring_enabled",
        "ai_policy_guidance", "ai_use_policy",
    ):
        if column in course_columns:
            op.drop_column("courses", column)
