"""phase 1 learning assurance

Revision ID: 20260717_07
Revises: 20260630_06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260717_07"
down_revision = "20260630_06"
branch_labels = None
depends_on = None

DEFAULT_RUBRIC = (
    '[{"key":"understanding_defence","label":"Understanding and defence",'
    '"description":"Explains the central ideas, decisions and claims in the submitted work.","max_score":30.0},'
    '{"key":"reasoning_method","label":"Methodological or analytical reasoning",'
    '"description":"Justifies the approach, method, evidence or analytical choices used.","max_score":25.0},'
    '{"key":"application_transfer","label":"Application and transfer",'
    '"description":"Applies the work to a new case, identifies implications and responds to counterfactual questions.","max_score":20.0},'
    '{"key":"accuracy_evidence","label":"Accuracy and use of evidence",'
    '"description":"Uses the document accurately and distinguishes supported claims from unsupported claims.","max_score":15.0},'
    '{"key":"reflection_ai_integrity","label":"Reflection and AI-use integrity",'
    '"description":"Identifies limitations, explains personal contribution and declares AI assistance responsibly.","max_score":10.0}]'
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "courses" in tables:
        columns = {item["name"] for item in inspector.get_columns("courses")}
        additions = [
            sa.Column("checkpoint_question_count", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("milestones_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version_history_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("final_justification_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ai_declaration_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("rubric_json", sa.Text(), nullable=False, server_default=DEFAULT_RUBRIC),
        ]
        for column in additions:
            if column.name not in columns:
                op.add_column("courses", column)

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "course_milestones" not in tables:
        op.create_table(
            "course_milestones",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(length=180), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("checkpoint_question_count", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("course_id", "sequence", name="uq_course_milestone_sequence"),
        )
        op.create_index("ix_course_milestones_course_id", "course_milestones", ["course_id"])

    inspector = sa.inspect(bind)
    if "documents" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("documents")}
        additions = [
            sa.Column("milestone_id", sa.String(length=36), nullable=True),
            sa.Column("previous_version_id", sa.String(length=36), nullable=True),
            sa.Column("submission_stage", sa.String(length=30), nullable=False, server_default="final"),
            sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("assessment_question_count_override", sa.Integer(), nullable=True),
            sa.Column("understanding_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("key_decisions", sa.Text(), nullable=False, server_default=""),
            sa.Column("main_contribution", sa.Text(), nullable=False, server_default=""),
            sa.Column("stated_limitation", sa.Text(), nullable=False, server_default=""),
            sa.Column("ai_use_level", sa.String(length=30), nullable=False, server_default="none"),
            sa.Column("ai_use_details", sa.Text(), nullable=True),
        ]
        for column in additions:
            if column.name not in columns:
                op.add_column("documents", column)
        inspector = sa.inspect(bind)
        foreign_keys = {fk.get("name") for fk in inspector.get_foreign_keys("documents")}
        if bind.dialect.name != "sqlite":
            if "fk_documents_milestone_id" not in foreign_keys:
                op.create_foreign_key("fk_documents_milestone_id", "documents", "course_milestones", ["milestone_id"], ["id"], ondelete="SET NULL")
            if "fk_documents_previous_version_id" not in foreign_keys:
                op.create_foreign_key("fk_documents_previous_version_id", "documents", "documents", ["previous_version_id"], ["id"], ondelete="SET NULL")
        indexes = {index["name"] for index in inspector.get_indexes("documents")}
        if "ix_documents_milestone_id" not in indexes:
            op.create_index("ix_documents_milestone_id", "documents", ["milestone_id"])
        if "ix_documents_previous_version_id" not in indexes:
            op.create_index("ix_documents_previous_version_id", "documents", ["previous_version_id"])

    inspector = sa.inspect(bind)
    if "questions" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("questions")}
        if "category" not in columns:
            op.add_column("questions", sa.Column("category", sa.String(length=40), nullable=False, server_default="document_knowledge"))
    inspector = sa.inspect(bind)
    if "assessment_items" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("assessment_items")}
        if "adaptive_reason" not in columns:
            op.add_column("assessment_items", sa.Column("adaptive_reason", sa.String(length=80), nullable=True))

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "learning_alerts" not in tables:
        op.create_table(
            "learning_alerts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=True),
            sa.Column("document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=True),
            sa.Column("student_id", sa.String(length=100), nullable=False),
            sa.Column("alert_key", sa.String(length=120), nullable=False),
            sa.Column("alert_type", sa.String(length=60), nullable=False),
            sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("document_id", "alert_key", name="uq_learning_alert_document_key"),
        )
        op.create_index("ix_learning_alerts_course_id", "learning_alerts", ["course_id"])
        op.create_index("ix_learning_alerts_document_id", "learning_alerts", ["document_id"])
        op.create_index("ix_learning_alerts_assessment_id", "learning_alerts", ["assessment_id"])
        op.create_index("ix_learning_alerts_student_id", "learning_alerts", ["student_id"])
        op.create_index("ix_learning_alerts_alert_type", "learning_alerts", ["alert_type"])

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "rubric_marks" not in tables:
        op.create_table(
            "rubric_marks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("graded_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("criteria_json", sa.Text(), nullable=False),
            sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("overall_comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("assessment_id", name="uq_rubric_mark_assessment"),
        )
        op.create_index("ix_rubric_marks_assessment_id", "rubric_marks", ["assessment_id"])
        op.create_index("ix_rubric_marks_graded_by", "rubric_marks", ["graded_by"])

    inspector = sa.inspect(bind)
    if "mark_audit_logs" not in set(inspector.get_table_names()):
        op.create_table(
            "mark_audit_logs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("assessment_id", sa.String(length=36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("previous_total", sa.Float(), nullable=True),
            sa.Column("new_total", sa.Float(), nullable=False),
            sa.Column("previous_json", sa.Text(), nullable=True),
            sa.Column("new_json", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_mark_audit_logs_assessment_id", "mark_audit_logs", ["assessment_id"])
        op.create_index("ix_mark_audit_logs_user_id", "mark_audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mark_audit_logs_user_id", table_name="mark_audit_logs")
    op.drop_index("ix_mark_audit_logs_assessment_id", table_name="mark_audit_logs")
    op.drop_table("mark_audit_logs")
    op.drop_index("ix_rubric_marks_graded_by", table_name="rubric_marks")
    op.drop_index("ix_rubric_marks_assessment_id", table_name="rubric_marks")
    op.drop_table("rubric_marks")
    op.drop_index("ix_learning_alerts_alert_type", table_name="learning_alerts")
    op.drop_index("ix_learning_alerts_student_id", table_name="learning_alerts")
    op.drop_index("ix_learning_alerts_assessment_id", table_name="learning_alerts")
    op.drop_index("ix_learning_alerts_document_id", table_name="learning_alerts")
    op.drop_index("ix_learning_alerts_course_id", table_name="learning_alerts")
    op.drop_table("learning_alerts")
    op.drop_column("assessment_items", "adaptive_reason")
    op.drop_column("questions", "category")
    op.drop_index("ix_documents_previous_version_id", table_name="documents")
    op.drop_index("ix_documents_milestone_id", table_name="documents")
    op.drop_constraint("fk_documents_previous_version_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_milestone_id", "documents", type_="foreignkey")
    for name in [
        "ai_use_details", "ai_use_level", "stated_limitation", "main_contribution",
        "key_decisions", "understanding_summary", "assessment_question_count_override",
        "version_number", "submission_stage", "previous_version_id", "milestone_id",
    ]:
        op.drop_column("documents", name)
    op.drop_index("ix_course_milestones_course_id", table_name="course_milestones")
    op.drop_table("course_milestones")
    for name in [
        "rubric_json", "ai_declaration_required", "final_justification_required",
        "version_history_enabled", "milestones_enabled", "checkpoint_question_count",
    ]:
        op.drop_column("courses", name)
