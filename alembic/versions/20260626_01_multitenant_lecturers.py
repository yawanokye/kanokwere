"""Add lecturer accounts, institutions, courses, and scoped submissions.

Revision ID: 20260626_01
Revises:
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_01"
down_revision = None
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_indexes(table_name) if item.get("name")}




def _foreign_key_names(inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_foreign_keys(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "documents" in tables:
        columns = _columns(inspector, "documents")
        additions = [
            ("institution_id", sa.String(length=36)),
            ("course_id", sa.String(length=36)),
            ("submitted_to_lecturer_id", sa.String(length=36)),
        ]
        for name, column_type in additions:
            if name not in columns:
                op.add_column("documents", sa.Column(name, column_type, nullable=True))
        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "documents")
        for name in ("institution_id", "course_id", "submitted_to_lecturer_id"):
            index_name = f"ix_documents_{name}"
            if index_name not in indexes:
                op.create_index(index_name, "documents", [name], unique=False)
        if bind.dialect.name != "sqlite":
            inspector = sa.inspect(bind)
            fk_names = _foreign_key_names(inspector, "documents")
            foreign_keys = [
                ("fk_documents_institution_id", "institution_id", "institutions", "id", "SET NULL"),
                ("fk_documents_course_id", "course_id", "courses", "id", "SET NULL"),
                ("fk_documents_submitted_to_lecturer_id", "submitted_to_lecturer_id", "users", "id", "SET NULL"),
            ]
            for constraint_name, local_col, remote_table, remote_col, ondelete in foreign_keys:
                if constraint_name not in fk_names:
                    op.create_foreign_key(
                        constraint_name,
                        "documents",
                        remote_table,
                        [local_col],
                        [remote_col],
                        ondelete=ondelete,
                    )

    if "assessments" in tables:
        columns = _columns(inspector, "assessments")
        for name in ("course_id", "lecturer_id"):
            if name not in columns:
                op.add_column("assessments", sa.Column(name, sa.String(length=36), nullable=True))
        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "assessments")
        for name in ("course_id", "lecturer_id"):
            index_name = f"ix_assessments_{name}"
            if index_name not in indexes:
                op.create_index(index_name, "assessments", [name], unique=False)
        if bind.dialect.name != "sqlite":
            inspector = sa.inspect(bind)
            fk_names = _foreign_key_names(inspector, "assessments")
            foreign_keys = [
                ("fk_assessments_course_id", "course_id", "courses", "id", "SET NULL"),
                ("fk_assessments_lecturer_id", "lecturer_id", "users", "id", "SET NULL"),
            ]
            for constraint_name, local_col, remote_table, remote_col, ondelete in foreign_keys:
                if constraint_name not in fk_names:
                    op.create_foreign_key(
                        constraint_name,
                        "assessments",
                        remote_table,
                        [local_col],
                        [remote_col],
                        ondelete=ondelete,
                    )


def downgrade() -> None:
    # Data-bearing multi-tenant columns are intentionally retained on downgrade.
    pass
