from __future__ import annotations

from alembic import command
import os
from pathlib import Path
import shutil
import zipfile
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from .config import BASE_DIR
from .database import Base, engine
from . import models  # noqa: F401


REPOSITORY_MONITORING_ASSET_DIR = BASE_DIR / "app" / "static" / "vendor" / "mediapipe-tasks-vision"
RUNTIME_MONITORING_ASSET_DIR = Path(
    os.getenv("KANOKWARE_MONITORING_ASSET_DIR", "/tmp/kanokware-mediapipe-tasks-vision")
)
MONITORING_ASSET_BUNDLE = BASE_DIR / "app" / "vendor" / "mediapipe-tasks-vision-assets.zip"
REQUIRED_MONITORING_ASSETS = {
    "vision_bundle.mjs",
    "models/face_detection_short_range.tflite",
    "wasm/vision_wasm_internal.js",
    "wasm/vision_wasm_internal.wasm",
    "wasm/vision_wasm_module_internal.js",
    "wasm/vision_wasm_module_internal.wasm",
    "wasm/vision_wasm_nosimd_internal.js",
    "wasm/vision_wasm_nosimd_internal.wasm",
}


REQUIRED_USER_COLUMNS = {
    "id",
    "institution_id",
    "full_name",
    "email",
    "password_hash",
    "role",
    "department",
    "email_verified",
    "account_status",
    "failed_login_count",
    "locked_until",
    "approved_at",
    "last_login_at",
    "must_change_password",
    "setup_code_hash",
    "setup_code_expires_at",
    "recovery_pin_hash",
    "activated_at",
    "created_at",
}


def _repair_legacy_user_columns() -> None:
    """Repair columns even when an old Alembic revision was stamped incorrectly."""
    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        if "users" not in set(inspector.get_table_names()):
            return

        existing = {item["name"] for item in inspector.get_columns("users")}
        operations = Operations(MigrationContext.configure(connection))
        additions: list[sa.Column] = [
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("setup_code_hash", sa.String(length=64), nullable=True),
            sa.Column("setup_code_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("recovery_pin_hash", sa.String(length=255), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        ]
        for column in additions:
            if column.name not in existing:
                print(f"Prestart: adding missing users.{column.name}", flush=True)
                operations.add_column("users", column)

        # A legacy staff_id field must not be required because the current app
        # no longer asks administrators to provide it.
        inspector = sa.inspect(connection)
        columns = {item["name"]: item for item in inspector.get_columns("users")}
        staff_id = columns.get("staff_id")
        if staff_id and not staff_id.get("nullable", True) and connection.dialect.name != "sqlite":
            print("Prestart: making legacy users.staff_id nullable", flush=True)
            operations.alter_column(
                "users",
                "staff_id",
                existing_type=staff_id["type"],
                nullable=True,
            )


def _repair_assessment_columns() -> None:
    """Repair course and assessment settings if an older database was stamped ahead."""
    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        tables = set(inspector.get_table_names())
        operations = Operations(MigrationContext.configure(connection))

        if "courses" in tables:
            columns = {item["name"] for item in inspector.get_columns("courses")}
            if "assessment_question_count" not in columns:
                print("Prestart: adding missing courses.assessment_question_count", flush=True)
                operations.add_column(
                    "courses",
                    sa.Column(
                        "assessment_question_count",
                        sa.Integer(),
                        nullable=False,
                        server_default="20",
                    ),
                )

        inspector = sa.inspect(connection)
        if "assessments" in set(inspector.get_table_names()):
            columns = {item["name"] for item in inspector.get_columns("assessments")}
            assessment_additions = [
                sa.Column("question_count", sa.Integer(), nullable=False, server_default="20"),
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
            for column in assessment_additions:
                if column.name not in columns:
                    print(f"Prestart: adding missing assessments.{column.name}", flush=True)
                    operations.add_column("assessments", column)


def _verify_schema() -> None:
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        raise RuntimeError("Database upgrade incomplete: users table is missing.")
    columns = {item["name"] for item in inspector.get_columns("users")}
    missing = sorted(REQUIRED_USER_COLUMNS - columns)
    if missing:
        raise RuntimeError(
            "Database upgrade incomplete. Missing users columns: " + ", ".join(missing)
        )
    course_columns = {item["name"] for item in inspector.get_columns("courses")}
    if "assessment_question_count" not in course_columns:
        raise RuntimeError("Database upgrade incomplete: courses.assessment_question_count is missing.")
    assessment_columns = {item["name"] for item in inspector.get_columns("assessments")}
    required_assessment_columns = {
        "question_count",
        "last_seen_at",
        "interruption_started_at",
        "interruption_count",
        "total_offline_seconds",
        "resume_count",
        "resume_deadline_at",
        "last_resumed_at",
        "last_interruption_reason",
        "locked_at",
        "lock_reason",
        "interruption_excused",
        "interruption_note",
        "client_instance_id",
        "camera_reverification_required",
    }
    missing_assessment = sorted(required_assessment_columns - assessment_columns)
    if missing_assessment:
        raise RuntimeError(
            "Database upgrade incomplete. Missing assessment columns: "
            + ", ".join(missing_assessment)
        )
    if "monitoring_events" not in tables:
        raise RuntimeError("Database upgrade incomplete: monitoring_events table is missing.")
    print("Prestart: user, course, assessment continuity, and monitoring schema verified.", flush=True)


def _missing_assets(asset_dir: Path) -> list[str]:
    return sorted(
        relative_path
        for relative_path in REQUIRED_MONITORING_ASSETS
        if not (asset_dir / relative_path).is_file()
    )


def _extract_monitoring_assets() -> Path:
    repository_missing = _missing_assets(REPOSITORY_MONITORING_ASSET_DIR)
    if not repository_missing:
        return REPOSITORY_MONITORING_ASSET_DIR

    if not MONITORING_ASSET_BUNDLE.is_file():
        raise RuntimeError(
            "Face-monitoring deployment is incomplete. The bundled asset archive is missing: "
            "app/vendor/mediapipe-tasks-vision-assets.zip"
        )

    if RUNTIME_MONITORING_ASSET_DIR.exists():
        shutil.rmtree(RUNTIME_MONITORING_ASSET_DIR)
    RUNTIME_MONITORING_ASSET_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(MONITORING_ASSET_BUNDLE, "r") as archive:
        archive_names = set(archive.namelist())
        for relative_path in sorted(REQUIRED_MONITORING_ASSETS):
            candidates = (
                relative_path,
                f"mediapipe-tasks-vision/{relative_path}",
            )
            member = next((name for name in candidates if name in archive_names), None)
            if member is None:
                raise RuntimeError(
                    "The bundled face-monitoring archive is incomplete. Missing: "
                    + relative_path
                )
            destination = RUNTIME_MONITORING_ASSET_DIR / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)

    runtime_missing = _missing_assets(RUNTIME_MONITORING_ASSET_DIR)
    if runtime_missing:
        raise RuntimeError(
            "Face-monitoring asset extraction failed. Missing files: "
            + ", ".join(runtime_missing)
        )

    print(
        "Prestart: MediaPipe Tasks Vision assets extracted to "
        + str(RUNTIME_MONITORING_ASSET_DIR),
        flush=True,
    )
    return RUNTIME_MONITORING_ASSET_DIR


def _verify_monitoring_assets() -> None:
    asset_dir = _extract_monitoring_assets()
    missing = _missing_assets(asset_dir)
    if missing:
        raise RuntimeError(
            "Face-monitoring deployment is incomplete. Missing files: "
            + ", ".join(missing)
        )
    print("Prestart: MediaPipe Tasks Vision assets verified.", flush=True)


def main() -> None:
    # Create tables that do not yet exist, repair known legacy schema gaps, then
    # apply versioned migrations. The final verification prevents Render from
    # starting the app against an incomplete database.
    _verify_monitoring_assets()
    Base.metadata.create_all(bind=engine)
    _repair_legacy_user_columns()
    _repair_assessment_columns()

    config = Config(str(BASE_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    _verify_schema()


if __name__ == "__main__":
    main()
