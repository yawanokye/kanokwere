"""Phase 2 optional learning-assurance modules.

Revision ID: 20260717_08
Revises: 20260717_07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260717_08"
down_revision = "20260717_07"
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
    existing = {item["name"] for item in inspector.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def upgrade() -> None:
    course_columns = [
        sa.Column("oral_defence_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("oral_defence_question_count", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("practical_tasks_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("practical_task_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supervisor_meetings_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cross_version_analysis_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("random_moderation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("moderation_sample_percent", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("moderation_variance_threshold", sa.Float(), nullable=False, server_default="10"),
        sa.Column("marker_anomaly_detection_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]
    for column in course_columns:
        _add_column_if_missing("courses", column)

    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "oral_defences" not in tables:
        op.create_table(
            "oral_defences",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("examiner_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="prepared"),
            sa.Column("questions_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("scores_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("total_score", sa.Float(), nullable=True),
            sa.Column("outcome", sa.String(length=40), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("assessment_id", name="uq_oral_defence_assessment"),
        )
        for name, columns in [
            ("ix_oral_defences_assessment_id", ["assessment_id"]),
            ("ix_oral_defences_course_id", ["course_id"]),
            ("ix_oral_defences_document_id", ["document_id"]),
            ("ix_oral_defences_examiner_id", ["examiner_id"]),
        ]:
            op.create_index(name, "oral_defences", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "practical_tasks" not in tables:
        op.create_table(
            "practical_tasks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("assessed_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("task_type", sa.String(length=50), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("student_response", sa.Text(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("max_score", sa.Float(), nullable=False, server_default="20"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="prepared"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("assessment_id", "sequence", name="uq_practical_task_assessment_sequence"),
        )
        for name, columns in [
            ("ix_practical_tasks_assessment_id", ["assessment_id"]),
            ("ix_practical_tasks_course_id", ["course_id"]),
            ("ix_practical_tasks_document_id", ["document_id"]),
            ("ix_practical_tasks_assessed_by", ["assessed_by"]),
        ]:
            op.create_index(name, "practical_tasks", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "supervisor_meetings" not in tables:
        op.create_table(
            "supervisor_meetings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
            sa.Column("student_id", sa.String(length=100), nullable=False),
            sa.Column("recorded_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("meeting_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("meeting_type", sa.String(length=40), nullable=False, server_default="supervision"),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("decisions", sa.Text(), nullable=True),
            sa.Column("action_items", sa.Text(), nullable=True),
            sa.Column("next_meeting_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in [
            ("ix_supervisor_meetings_course_id", ["course_id"]),
            ("ix_supervisor_meetings_document_id", ["document_id"]),
            ("ix_supervisor_meetings_student_id", ["student_id"]),
            ("ix_supervisor_meetings_recorded_by", ["recorded_by"]),
        ]:
            op.create_index(name, "supervisor_meetings", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "version_analyses" not in tables:
        op.create_table(
            "version_analyses",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("previous_document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("generated_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="low"),
            sa.Column("analysis_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("document_id", name="uq_version_analysis_document"),
        )
        for name, columns in [
            ("ix_version_analyses_course_id", ["course_id"]),
            ("ix_version_analyses_document_id", ["document_id"]),
            ("ix_version_analyses_previous_document_id", ["previous_document_id"]),
            ("ix_version_analyses_generated_by", ["generated_by"]),
        ]:
            op.create_index(name, "version_analyses", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "moderation_records" not in tables:
        op.create_table(
            "moderation_records",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("moderator_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="selected"),
            sa.Column("selected_reason", sa.String(length=180), nullable=False),
            sa.Column("original_mark", sa.Float(), nullable=False),
            sa.Column("moderated_mark", sa.Float(), nullable=True),
            sa.Column("variance", sa.Float(), nullable=True),
            sa.Column("decision", sa.String(length=40), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("assessment_id", name="uq_moderation_record_assessment"),
        )
        for name, columns in [
            ("ix_moderation_records_assessment_id", ["assessment_id"]),
            ("ix_moderation_records_course_id", ["course_id"]),
            ("ix_moderation_records_moderator_id", ["moderator_id"]),
        ]:
            op.create_index(name, "moderation_records", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "marker_anomaly_alerts" not in tables:
        op.create_table(
            "marker_anomaly_alerts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("marker_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("anomaly_type", sa.String(length=60), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
            sa.Column("metric_value", sa.Float(), nullable=True),
            sa.Column("threshold_value", sa.Float(), nullable=True),
            sa.Column("details", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
        )
        for name, columns in [
            ("ix_marker_anomaly_alerts_course_id", ["course_id"]),
            ("ix_marker_anomaly_alerts_marker_id", ["marker_id"]),
            ("ix_marker_anomaly_alerts_anomaly_type", ["anomaly_type"]),
            ("ix_marker_anomaly_alerts_resolved_by", ["resolved_by"]),
        ]:
            op.create_index(name, "marker_anomaly_alerts", columns)


def downgrade() -> None:
    for table in [
        "marker_anomaly_alerts",
        "moderation_records",
        "version_analyses",
        "supervisor_meetings",
        "practical_tasks",
        "oral_defences",
    ]:
        if table in set(sa.inspect(op.get_bind()).get_table_names()):
            op.drop_table(table)
    for name in [
        "marker_anomaly_detection_enabled",
        "moderation_variance_threshold",
        "moderation_sample_percent",
        "random_moderation_enabled",
        "cross_version_analysis_enabled",
        "supervisor_meetings_enabled",
        "practical_task_count",
        "practical_tasks_enabled",
        "oral_defence_question_count",
        "oral_defence_enabled",
    ]:
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("courses")}
        if name in columns:
            op.drop_column("courses", name)
