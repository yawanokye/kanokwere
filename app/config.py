from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Kanokware")
    environment: str = os.getenv("ENVIRONMENT", "development")
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'kanokware.db').as_posix()}"
    )
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.5")
    openai_timeout_seconds: int = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))
    openai_max_retries: int = int(os.getenv("OPENAI_MAX_RETRIES", "0"))
    generation_attempts: int = int(os.getenv("GENERATION_ATTEMPTS", "2"))
    generation_stale_minutes: int = int(os.getenv("GENERATION_STALE_MINUTES", "8"))
    admin_key: str = os.getenv("ADMIN_KEY", "change-me-before-production")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "15"))
    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "80000"))
    pass_threshold: int = int(os.getenv("PASS_THRESHOLD", "80"))
    question_time_seconds: int = int(os.getenv("QUESTION_TIME_SECONDS", "30"))
    delete_original_after_processing: bool = _as_bool(
        os.getenv("DELETE_ORIGINAL_AFTER_PROCESSING"), True
    )
    allow_demo_questions: bool = _as_bool(os.getenv("ALLOW_DEMO_QUESTIONS"), True)
    session_token_ttl_minutes: int = int(os.getenv("SESSION_TOKEN_TTL_MINUTES", "30"))
    max_attempts_per_document: int = int(os.getenv("MAX_ATTEMPTS_PER_DOCUMENT", "1"))
    webcam_required: bool = _as_bool(os.getenv("WEBCAM_REQUIRED"), True)
    webcam_max_image_kb: int = int(os.getenv("WEBCAM_MAX_IMAGE_KB", "700"))
    lecturer_session_hours: int = int(os.getenv("LECTURER_SESSION_HOURS", "12"))
    auth_cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "kanokware_lecturer_session")
    login_max_failures: int = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
    login_lock_minutes: int = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
    registration_enabled: bool = _as_bool(os.getenv("LECTURER_REGISTRATION_ENABLED"), False)
    account_setup_code_hours: int = int(os.getenv("ACCOUNT_SETUP_CODE_HOURS", "48"))


settings = Settings()
