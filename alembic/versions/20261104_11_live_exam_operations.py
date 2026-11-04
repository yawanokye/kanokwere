"""Live examination scheduling, admission, timing and proctor controls.

Revision ID: 20261104_11
Revises: 20260901_10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261104_11"
down_revision = "20260901_10"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return
    if column.name not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing(
        "assessment_tasks", sa.Column("exam_starts_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing(
        "assessment_tasks", sa.Column("exam_ends_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing(
        "assessment_tasks", sa.Column("duration_minutes", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        "assessment_tasks",
        sa.Column("late_entry_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "assessment_tasks",
        sa.Column("roster_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column_if_missing("assessment_tasks", sa.Column("geofence_label", sa.String(length=240), nullable=True))
    _add_column_if_missing("assessment_tasks", sa.Column("geofence_latitude", sa.Float(), nullable=True))
    _add_column_if_missing("assessment_tasks", sa.Column("geofence_longitude", sa.Float(), nullable=True))
    _add_column_if_missing("assessment_tasks", sa.Column("geofence_radius_meters", sa.Integer(), nullable=True))
    _add_column_if_missing(
        "assessment_tasks",
        sa.Column("geofence_max_accuracy_meters", sa.Integer(), nullable=False, server_default="100"),
    )

    _add_column_if_missing(
        "assessments", sa.Column("exam_deadline_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing(
        "assessments", sa.Column("proctor_paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing("assessments", sa.Column("proctor_message", sa.Text(), nullable=True))
    _add_column_if_missing(
        "assessments", sa.Column("proctor_message_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing(
        "assessments",
        sa.Column("proctor_message_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "assessments",
        sa.Column("extra_time_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing("assessments", sa.Column("location_distance_meters", sa.Float(), nullable=True))
    _add_column_if_missing("assessments", sa.Column("location_accuracy_meters", sa.Float(), nullable=True))
    _add_column_if_missing("assessments", sa.Column("location_verified_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    if "exam_candidates" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "exam_candidates",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(length=36),
                sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("candidate_id", sa.String(length=100), nullable=False),
            sa.Column("candidate_name", sa.String(length=180), nullable=False),
            sa.Column("access_pin_hash", sa.String(length=255), nullable=False),
            sa.Column("extra_time_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("monitor_group", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("task_id", "candidate_id", name="uq_exam_candidate_task_id"),
        )
        op.create_index("ix_exam_candidates_task_id", "exam_candidates", ["task_id"])
        op.create_index("ix_exam_candidates_candidate_id", "exam_candidates", ["candidate_id"])
        op.create_index("ix_exam_candidates_monitor_group", "exam_candidates", ["monitor_group"])
    else:
        _add_column_if_missing(
            "exam_candidates", sa.Column("monitor_group", sa.String(length=120), nullable=True)
        )

    if "exam_monitor_assignments" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "exam_monitor_assignments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("assessment_tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("group_label", sa.String(length=120), nullable=False),
            sa.Column("monitor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("task_id", "group_label", name="uq_exam_monitor_task_group"),
        )
        op.create_index("ix_exam_monitor_assignments_task_id", "exam_monitor_assignments", ["task_id"])
        op.create_index("ix_exam_monitor_assignments_group_label", "exam_monitor_assignments", ["group_label"])
        op.create_index("ix_exam_monitor_assignments_monitor_user_id", "exam_monitor_assignments", ["monitor_user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "exam_monitor_assignments" in set(inspector.get_table_names()):
        op.drop_table("exam_monitor_assignments")
    inspector = sa.inspect(bind)
    if "exam_candidates" in set(inspector.get_table_names()):
        op.drop_table("exam_candidates")
    for table, names in (
        (
            "assessments",
            (
                "extra_time_minutes",
                "proctor_message_acknowledged_at",
                "proctor_message_at",
                "proctor_message",
                "proctor_paused_at",
                "exam_deadline_at",
                "location_verified_at",
                "location_accuracy_meters",
                "location_distance_meters",
            ),
        ),
        (
            "assessment_tasks",
            (
                "roster_required",
                "late_entry_minutes",
                "duration_minutes",
                "exam_ends_at",
                "exam_starts_at",
                "geofence_max_accuracy_meters",
                "geofence_radius_meters",
                "geofence_longitude",
                "geofence_latitude",
                "geofence_label",
            ),
        ),
    ):
        inspector = sa.inspect(bind)
        if table not in set(inspector.get_table_names()):
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        for name in names:
            if name in columns:
                op.drop_column(table, name)
