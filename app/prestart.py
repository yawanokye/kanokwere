from __future__ import annotations

from alembic import command
from alembic.config import Config

from .config import BASE_DIR
from .database import Base, engine
from . import models  # noqa: F401


def main() -> None:
    # Create new tables first. Alembic then adds columns to legacy tables and
    # records the schema revision without deleting existing submissions.
    Base.metadata.create_all(bind=engine)
    config = Config(str(BASE_DIR / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
