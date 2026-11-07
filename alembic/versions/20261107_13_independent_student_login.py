"""Index independent student sign-in without course-code lookup.

Revision ID: 20261107_13
Revises: 20261107_12
"""
from alembic import op
import sqlalchemy as sa

revision = "20261107_13"
down_revision = "20261107_12"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "student_accounts" in inspector.get_table_names():
        if "ix_student_accounts_student_number" not in {row["name"] for row in inspector.get_indexes("student_accounts")}:
            op.create_index("ix_student_accounts_student_number", "student_accounts", ["student_number"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "student_accounts" in inspector.get_table_names():
        if "ix_student_accounts_student_number" in {row["name"] for row in inspector.get_indexes("student_accounts")}:
            op.drop_index("ix_student_accounts_student_number", table_name="student_accounts")
