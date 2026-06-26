from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from .config import BASE_DIR
from .database import Base, engine
from . import models  # noqa: F401


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
    print("Prestart: user account schema verified.", flush=True)


def main() -> None:
    # Create tables that do not yet exist, repair known legacy schema gaps, then
    # apply versioned migrations. The final verification prevents Render from
    # starting the app against an incomplete database.
    Base.metadata.create_all(bind=engine)
    _repair_legacy_user_columns()

    config = Config(str(BASE_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    _verify_schema()


if __name__ == "__main__":
    main()
