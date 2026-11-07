"""Optional student accounts, reviewed photographs and oral evidence.

Revision ID: 20261107_12
Revises: 20261104_11
"""
from alembic import op
import sqlalchemy as sa

revision = "20261107_12"
down_revision = "20261104_11"
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", sa.String(36), primary_key=True)


def fk(name, target, *, primary=False, nullable=False, ondelete="CASCADE"):
    return sa.Column(name, sa.String(36), sa.ForeignKey(target, ondelete=ondelete), primary_key=primary, nullable=nullable)


def create(name, *columns):
    if name not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(name, *columns)


def upgrade():
    create("student_accounts", pk(), fk("institution_id", "institutions.id"),
        sa.Column("student_number", sa.String(100), nullable=False), sa.Column("full_name", sa.String(180), nullable=False),
        sa.Column("password_hash", sa.String(255)), sa.Column("setup_hash", sa.String(64)),
        sa.Column("setup_expires_at", sa.DateTime(timezone=True)), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False), sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("institution_id", "student_number", name="uq_student_institution_number"))
    create("student_enrollments", pk(), fk("course_id", "courses.id"), fk("student_id", "student_accounts.id"),
        sa.Column("status", sa.String(30), nullable=False), sa.UniqueConstraint("course_id", "student_id", name="uq_student_enrollment"))
    create("student_sessions", pk(), fk("student_id", "student_accounts.id"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    create("student_photos", pk(), fk("student_id", "student_accounts.id"), sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        fk("reviewer_id", "users.id", nullable=True, ondelete="SET NULL"), sa.Column("review_note", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    create("student_document_links", fk("document_id", "documents.id", primary=True), fk("student_id", "student_accounts.id"))
    create("task_distance_settings", fk("task_id", "assessment_tasks.id", primary=True),
        sa.Column("oral_question_count", sa.Integer(), nullable=False), sa.Column("oral_response_seconds", sa.Integer(), nullable=False),
        sa.Column("oral_window_hours", sa.Integer(), nullable=False), sa.Column("typed_alternative", sa.Boolean(), nullable=False),
        sa.Column("proctor_groups_json", sa.Text(), nullable=False))
    create("attempt_admission_locks", sa.Column("lock_key", sa.String(64), primary_key=True))
    create("assessment_distance_evidence", fk("assessment_id", "assessments.id", primary=True),
        fk("student_id", "student_accounts.id", nullable=True, ondelete="SET NULL"),
        fk("reference_photo_id", "student_photos.id", nullable=True, ondelete="SET NULL"),
        sa.Column("start_photo", sa.LargeBinary()), sa.Column("start_photo_mime", sa.String(40)),
        sa.Column("identity_status", sa.String(40), nullable=False), sa.Column("identity_note", sa.Text()),
        fk("identity_reviewer_id", "users.id", nullable=True, ondelete="SET NULL"),
        sa.Column("settings_json", sa.Text(), nullable=False), sa.Column("oral_questions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    create("student_oral_responses", pk(), fk("assessment_id", "assessments.id"),
        sa.Column("question_id", sa.String(80), nullable=False), sa.Column("audio_data", sa.LargeBinary()),
        sa.Column("mime_type", sa.String(80)), sa.Column("typed_text", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assessment_id", "question_id", name="uq_student_oral_response"))
    indexes = {"student_accounts": ["institution_id"], "student_enrollments": ["course_id", "student_id"],
        "student_sessions": ["student_id", "token_hash"], "student_photos": ["student_id"],
        "student_document_links": ["student_id"], "assessment_distance_evidence": ["student_id"],
        "student_oral_responses": ["assessment_id"]}
    for table, columns in indexes.items():
        existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
        for column in columns:
            name = f"ix_{table}_{column}"
            if name not in existing:
                op.create_index(name, table, [column])


def downgrade():
    for name in ["student_oral_responses", "assessment_distance_evidence", "attempt_admission_locks", "task_distance_settings", "student_document_links", "student_photos", "student_sessions", "student_enrollments", "student_accounts"]:
        op.drop_table(name)
