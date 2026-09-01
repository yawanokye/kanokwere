"""Task feature governance and standalone examinations.

Revision ID: 20260901_10
Revises: 20260831_09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_10"
down_revision = "20260831_09"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    if column.name not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    if name not in {item["name"] for item in inspector.get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "feature_settings" not in tables:
        op.create_table(
            "feature_settings",
            sa.Column("feature_key", sa.String(length=80), primary_key=True),
            sa.Column(
                "activation_mode",
                sa.String(length=30),
                nullable=False,
                server_default="lecturer_allowed",
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "assessment_tasks" not in tables:
        op.create_table(
            "assessment_tasks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "course_id",
                sa.String(length=36),
                sa.ForeignKey("courses.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("instructions", sa.Text(), nullable=True),
            sa.Column(
                "mode",
                sa.String(length=40),
                nullable=False,
                server_default="submission_assurance",
            ),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column(
                "assessment_question_count", sa.Integer(), nullable=False, server_default="20"
            ),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "created_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_assessment_tasks_course_id", "assessment_tasks", ["course_id"])
        op.create_index("ix_assessment_tasks_created_by", "assessment_tasks", ["created_by"])
    tables = set(sa.inspect(bind).get_table_names())
    if "task_feature_activations" not in tables:
        op.create_table(
            "task_feature_activations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(length=36),
                sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("feature_key", sa.String(length=80), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "activated_by_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "activated_by_role", sa.String(length=30), nullable=False, server_default="lecturer"
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("task_id", "feature_key", name="uq_task_feature_activation"),
        )
        op.create_index("ix_task_feature_activations_task_id", "task_feature_activations", ["task_id"])
        op.create_index("ix_task_feature_activations_feature_key", "task_feature_activations", ["feature_key"])
        op.create_index(
            "ix_task_feature_activations_activated_by_user_id",
            "task_feature_activations",
            ["activated_by_user_id"],
        )

    _add_column_if_missing(
        "courses", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1")
    )
    _add_column_if_missing(
        "assessment_tasks",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    _add_column_if_missing(
        "documents",
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("assessment_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _add_column_if_missing(
        "documents", sa.Column("features_json", sa.Text(), nullable=False, server_default="{}")
    )
    _add_column_if_missing(
        "assessments", sa.Column("features_json", sa.Text(), nullable=False, server_default="{}")
    )
    _add_column_if_missing(
        "assessments", sa.Column("candidate_name", sa.String(length=180), nullable=True)
    )
    _add_column_if_missing(
        "assessments", sa.Column("candidate_id", sa.String(length=100), nullable=True)
    )
    _create_index_if_missing("ix_documents_task_id", "documents", ["task_id"])
    _create_index_if_missing("ix_assessments_candidate_id", "assessments", ["candidate_id"])
    if "assessment_recording_chunks" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "assessment_recording_chunks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "assessment_id",
                sa.String(length=36),
                sa.ForeignKey("assessments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("recording_type", sa.String(length=30), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=False),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column("byte_count", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "assessment_id", "recording_type", "sequence",
                name="uq_assessment_recording_chunk_sequence",
            ),
        )
        op.create_index(
            "ix_assessment_recording_chunks_assessment_id",
            "assessment_recording_chunks",
            ["assessment_id"],
        )
        op.create_index(
            "ix_assessment_recording_chunks_recording_type",
            "assessment_recording_chunks",
            ["recording_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "assessment_recording_chunks" in tables:
        op.drop_table("assessment_recording_chunks")
    if "assessments" in tables:
        indexes = {item["name"] for item in inspector.get_indexes("assessments")}
        if "ix_assessments_candidate_id" in indexes:
            op.drop_index("ix_assessments_candidate_id", table_name="assessments")
        columns = {item["name"] for item in inspector.get_columns("assessments")}
        for name in ("candidate_id", "candidate_name", "features_json"):
            if name in columns:
                op.drop_column("assessments", name)
    inspector = sa.inspect(bind)
    if "documents" in set(inspector.get_table_names()):
        indexes = {item["name"] for item in inspector.get_indexes("documents")}
        if "ix_documents_task_id" in indexes:
            op.drop_index("ix_documents_task_id", table_name="documents")
        columns = {item["name"] for item in inspector.get_columns("documents")}
        for name in ("features_json", "task_id"):
            if name in columns:
                op.drop_column("documents", name)
    for table in ("task_feature_activations", "assessment_tasks", "feature_settings"):
        if table in set(sa.inspect(bind).get_table_names()):
            op.drop_table(table)
    inspector = sa.inspect(bind)
    if "courses" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("courses")}
        if "max_attempts" in columns:
            op.drop_column("courses", "max_attempts")
