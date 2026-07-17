from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import mimetypes

from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .access_service import (
    audit,
    course_ids_for_user,
    email_domain,
    generate_enrollment_code,
    normalize_email,
    require_course_access,
    require_document_access,
)
from .assessment_service import (
    allow_assessment_resume,
    assessment_state_payload,
    current_question,
    finish_interrupted_assessment,
    get_assessment,
    heartbeat_assessment,
    interrupt_assessment,
    refresh_interruption_state,
    result_payload,
    set_interruption_excused,
    start_assessment,
    submit_answer,
)
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .document_service import read_and_extract
from .learning_service import (
    alert_payload,
    course_learning_progress,
    default_rubric_json,
    parse_rubric,
    refresh_document_alerts,
    version_comparison,
)
from .phase2_service import (
    anomaly_payload,
    complete_oral_defence,
    course_phase2_settings,
    ensure_random_moderation,
    ensure_version_analysis,
    moderation_payload,
    oral_defence_payload,
    practical_task_payload,
    prepare_oral_defence,
    prepare_practical_tasks,
    scan_marker_anomalies,
    supervisor_meeting_payload,
    version_analysis_payload,
)
from .models import (
    Assessment,
    AssessmentItem,
    AuditLog,
    AuthSession,
    Course,
    CourseLecturer,
    CourseMilestone,
    Document,
    Institution,
    LearningAlert,
    MarkAuditLog,
    MarkerAnomalyAlert,
    ModerationRecord,
    MonitoringEvent,
    OralDefence,
    PasswordResetRequest,
    PracticalTask,
    Question,
    RubricMark,
    SupervisorMeeting,
    User,
    VersionAnalysis,
    WebcamSnapshot,
)
from .question_service import generate_question_bank, question_to_record
from .report_service import build_pdf_report
from .schemas import (
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserStatusRequest,
    ActivateAccountRequest,
    AnswerRequest,
    AssessmentHeartbeatRequest,
    AssessmentInterruptRequest,
    ChangePasswordRequest,
    CourseCollaboratorRequest,
    CourseCreateRequest,
    CourseMilestoneCreateRequest,
    CourseRubricRequest,
    CourseSettingsRequest,
    FocusEventRequest,
    LearningAlertActionRequest,
    InterruptionNoteRequest,
    MonitoringEventRequest,
    LecturerRegisterRequest,
    MarkerAnomalyActionRequest,
    ModerationCompleteRequest,
    OralDefenceCompleteRequest,
    OralDefenceStartRequest,
    PracticalTaskGenerateRequest,
    PracticalTaskScoreRequest,
    RubricMarkRequest,
    LoginRequest,
    SupervisorMeetingRequest,
    SelfServicePasswordResetRequest,
    StartAssessmentRequest,
    UserApprovalRequest,
    UserSuspensionRequest,
)
from .security import (
    bearer_token,
    create_lecturer_session,
    current_user,
    optional_current_user,
    generate_setup_code,
    hash_password,
    hash_token,
    require_admin,
    verify_password,
    verify_token,
)


logger = logging.getLogger("kanokware")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        interrupted = db.scalars(
            select(Document).where(Document.status.in_(["queued", "generating"]))
        ).all()
        for document in interrupted:
            document.status = "failed"
            document.processing_error = (
                "Question generation was interrupted by a service restart. "
                "Use Retry generation or upload the document again."
            )
        active_assessments = db.scalars(
            select(Assessment).where(Assessment.status.in_(["in_progress", "interrupted"]))
        ).all()
        for assessment in active_assessments:
            refresh_interruption_state(db, assessment, commit=False)
        if interrupted or active_assessments:
            db.commit()
    yield


mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

app = FastAPI(title="Kanokware", version="0.9.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
REPOSITORY_MONITORING_ASSET_DIR = STATIC_DIR / "vendor" / "mediapipe-tasks-vision"
RUNTIME_MONITORING_ASSET_DIR = Path(
    os.getenv("KANOKWARE_MONITORING_ASSET_DIR", "/tmp/kanokware-mediapipe-tasks-vision")
)
MONITORING_ASSETS = {
    "vision_bundle.mjs": "application/javascript",
    "models/face_detection_short_range.tflite": "application/octet-stream",
    "wasm/vision_wasm_internal.js": "application/javascript",
    "wasm/vision_wasm_internal.wasm": "application/wasm",
    "wasm/vision_wasm_module_internal.js": "application/javascript",
    "wasm/vision_wasm_module_internal.wasm": "application/wasm",
    "wasm/vision_wasm_nosimd_internal.js": "application/javascript",
    "wasm/vision_wasm_nosimd_internal.wasm": "application/wasm",
}


def _resolve_monitoring_asset_dir() -> Path:
    for candidate in (RUNTIME_MONITORING_ASSET_DIR, REPOSITORY_MONITORING_ASSET_DIR):
        if all((candidate / relative_path).is_file() for relative_path in MONITORING_ASSETS):
            return candidate
    return RUNTIME_MONITORING_ASSET_DIR


MONITORING_ASSET_DIR = _resolve_monitoring_asset_dir()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/monitoring-assets/"):
        response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
    else:
        response.headers["Cache-Control"] = "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'wasm-unsafe-eval'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self'; worker-src 'self' blob:; "
        "frame-ancestors 'none'"
    )
    return response


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


def _missing_monitoring_assets() -> list[str]:
    return [
        relative_path
        for relative_path in MONITORING_ASSETS
        if not (MONITORING_ASSET_DIR / relative_path).is_file()
    ]


@app.get("/api/monitoring/assets-status", include_in_schema=False)
def monitoring_assets_status() -> dict[str, object]:
    missing = _missing_monitoring_assets()
    return {
        "ready": not missing,
        "missing": missing,
        "asset_base": "/monitoring-assets",
    }


@app.get("/monitoring-assets/{asset_path:path}", include_in_schema=False)
def monitoring_asset(asset_path: str) -> FileResponse:
    normalized = asset_path.strip("/")
    media_type = MONITORING_ASSETS.get(normalized)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Monitoring asset not found.")

    file_path = (MONITORING_ASSET_DIR / normalized).resolve()
    asset_root = MONITORING_ASSET_DIR.resolve()
    if asset_root not in file_path.parents or not file_path.is_file():
        logger.error("Monitoring asset missing from deployment: %s", normalized)
        raise HTTPException(status_code=404, detail="Monitoring asset is missing from this deployment.")

    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "app": settings.app_name}


# ---------------------------------------------------------------------------
# Lecturer registration and authentication
# ---------------------------------------------------------------------------


def _user_payload(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "department": user.department,
        "account_status": user.account_status,
        "email_verified": user.email_verified,
        "must_change_password": user.must_change_password,
        "activation_required": user.account_status == "pending_activation",
        "setup_code_expires_at": user.setup_code_expires_at.isoformat() if user.setup_code_expires_at else None,
        "activated_at": user.activated_at.isoformat() if user.activated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "institution": {
            "id": user.institution.id,
            "name": user.institution.name,
            "domain": user.institution.domain,
            "status": user.institution.status,
        } if user.institution else None,
    }


@app.post("/api/auth/register", status_code=403)
def register_lecturer() -> None:
    raise HTTPException(
        status_code=403,
        detail="Lecturer self-registration is disabled. Ask the Kanokware administrator to create your account.",
    )


def _credential_locked(user: User) -> bool:
    locked_until = aware(user.locked_until)
    return bool(locked_until and locked_until > utcnow())


def _register_failed_credential(db: Session, user: User) -> None:
    user.failed_login_count += 1
    if user.failed_login_count >= settings.login_max_failures:
        user.locked_until = utcnow() + timedelta(minutes=settings.login_lock_minutes)
        user.failed_login_count = 0
    db.commit()


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.lecturer_session_hours * 3600,
        httponly=True,
        secure=settings.environment.casefold() == "production",
        samesite="lax",
        path="/",
    )


@app.post("/api/auth/activate")
def activate_lecturer_account(
    payload: ActivateAccountRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = normalize_email(str(payload.email))
    user = db.scalar(
        select(User).options(selectinload(User.institution)).where(User.email == email)
    )
    if not user or not user.setup_code_hash:
        raise HTTPException(status_code=400, detail="The account details or setup code are invalid.")
    if user.account_status == "suspended":
        raise HTTPException(status_code=403, detail="This lecturer account is suspended.")
    if _credential_locked(user):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    expires_at = aware(user.setup_code_expires_at)
    if not expires_at or expires_at <= utcnow():
        raise HTTPException(status_code=400, detail="The setup code has expired. Ask the administrator to issue a new code.")
    setup_code = payload.setup_code.strip().upper()
    if not verify_token(setup_code, user.setup_code_hash):
        _register_failed_credential(db, user)
        raise HTTPException(status_code=400, detail="The account details or setup code are invalid.")

    user.password_hash = hash_password(payload.new_password)
    user.recovery_pin_hash = hash_password(payload.recovery_pin)
    user.account_status = "active"
    user.email_verified = True
    user.must_change_password = False
    user.activated_at = utcnow()
    user.setup_code_hash = None
    user.setup_code_expires_at = None
    user.failed_login_count = 0
    user.locked_until = None
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request, "lecturer_account_activated", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    token = create_lecturer_session(db, user)
    _set_auth_cookie(response, token)
    return {"activated": True, "authenticated": True, "user": _user_payload(user)}


@app.post("/api/auth/reset-password")
def self_service_password_reset(
    payload: SelfServicePasswordResetRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = normalize_email(str(payload.email))
    user = db.scalar(
        select(User).options(selectinload(User.institution)).where(User.email == email)
    )
    generic_error = "The account details or recovery PIN are invalid."
    if not user or not user.recovery_pin_hash or user.account_status != "active":
        raise HTTPException(status_code=400, detail=generic_error)
    if _credential_locked(user):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    if not verify_password(payload.recovery_pin, user.recovery_pin_hash):
        _register_failed_credential(db, user)
        raise HTTPException(status_code=400, detail=generic_error)

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request, "self_service_password_reset", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    token = create_lecturer_session(db, user)
    _set_auth_cookie(response, token)
    return {"reset": True, "authenticated": True, "user": _user_payload(user)}


@app.post("/api/auth/login")
def login_lecturer(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = normalize_email(str(payload.email))
    user = db.scalar(
        select(User).options(selectinload(User.institution)).where(User.email == email)
    )
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if _credential_locked(user):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    if user.account_status in {"pending", "pending_activation"}:
        raise HTTPException(status_code=403, detail="Activate your lecturer account with the setup code provided by the administrator.")

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = utcnow() + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if user.account_status != "active" or not user.email_verified:
        raise HTTPException(status_code=403, detail="This lecturer account is not active.")
    if not user.institution or user.institution.status != "active":
        raise HTTPException(status_code=403, detail="Your institution is not active on Kanokware.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    token = create_lecturer_session(db, user)
    _set_auth_cookie(response, token)
    audit(db, request, "lecturer_login", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    return {"authenticated": True, "user": _user_payload(user)}


@app.post("/api/auth/logout")
def logout_lecturer(
    response: Response,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    auth_session = getattr(request.state, "auth_session", None)
    if auth_session:
        auth_session.revoked_at = utcnow()
    audit(db, request, "lecturer_logout", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"logged_out": True}


@app.get("/api/auth/me")
def lecturer_me(user: User | None = Depends(optional_current_user)) -> dict[str, object | None]:
    if user is None:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": _user_payload(user)}


@app.post("/api/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    db.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != request.state.auth_session.id)
    )
    audit(db, request, "password_changed", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    return {"changed": True}


# ---------------------------------------------------------------------------
# Platform administration and lecturer account management
# ---------------------------------------------------------------------------


def _find_or_create_institution(db: Session, name: str, email: str) -> Institution:
    domain = email_domain(email)
    institution = db.scalar(select(Institution).where(Institution.domain == domain))
    if not institution:
        institution = db.scalar(
            select(Institution).where(func.lower(Institution.name) == name.strip().casefold())
        )
    if not institution:
        institution = Institution(name=name.strip(), domain=domain, status="active")
        db.add(institution)
        db.flush()
    else:
        institution.status = "active"
        if not institution.domain:
            institution.domain = domain
    return institution


def _platform_user_row(db: Session, user: User) -> dict[str, object]:
    course_count = db.scalar(
        select(func.count(CourseLecturer.id)).where(CourseLecturer.lecturer_id == user.id)
    ) or 0
    submission_count = db.scalar(
        select(func.count(Document.id)).where(Document.submitted_to_lecturer_id == user.id)
    ) or 0
    return _user_payload(user) | {
        "course_count": int(course_count),
        "submission_count": int(submission_count),
    }


@app.get("/api/platform/verify", dependencies=[Depends(require_admin)])
def platform_verify() -> dict[str, bool]:
    return {"authenticated": True}


@app.get("/api/platform/users", dependencies=[Depends(require_admin)])
def platform_users(db: Session = Depends(get_db)) -> dict[str, object]:
    users = db.scalars(
        select(User)
        .options(selectinload(User.institution))
        .order_by(User.created_at.desc())
    ).all()
    return {"users": [_platform_user_row(db, user) for user in users]}


def _issue_setup_code(user: User) -> tuple[str, datetime]:
    setup_code = generate_setup_code()
    expires_at = utcnow() + timedelta(hours=settings.account_setup_code_hours)
    user.setup_code_hash = hash_token(setup_code)
    user.setup_code_expires_at = expires_at
    user.account_status = "pending_activation"
    user.must_change_password = False
    user.recovery_pin_hash = None
    user.activated_at = None
    user.failed_login_count = 0
    user.locked_until = None
    return setup_code, expires_at


@app.post("/api/platform/users", status_code=201, dependencies=[Depends(require_admin)])
def platform_create_user(
    payload: AdminUserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = normalize_email(str(payload.email))
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account already exists for this email address.")
    institution = _find_or_create_institution(db, payload.institution_name, email)
    user = User(
        institution_id=institution.id,
        full_name=payload.full_name.strip(),
        email=email,
        password_hash=hash_password(generate_setup_code() + generate_setup_code()),
        role=payload.role,
        department=payload.department.strip(),
        email_verified=True,
        account_status="pending_activation",
        approved_at=utcnow(),
        must_change_password=False,
    )
    setup_code, expires_at = _issue_setup_code(user)
    db.add(user)
    db.flush()
    audit(
        db,
        request,
        "lecturer_account_created",
        resource_type="user",
        resource_id=user.id,
        detail={"email": email, "role": payload.role, "institution": institution.name},
    )
    db.commit()
    db.refresh(user)
    return {
        "created": True,
        "user": _user_payload(user),
        "setup_code": setup_code,
        "setup_code_expires_at": expires_at.isoformat(),
        "message": "Account created. Give the login email and one-time setup code to the lecturer.",
    }


@app.get("/api/platform/pending", dependencies=[Depends(require_admin)])
def platform_pending(db: Session = Depends(get_db)) -> dict[str, object]:
    users = db.scalars(
        select(User)
        .options(selectinload(User.institution))
        .where(User.account_status.in_(["pending", "pending_activation"]))
        .order_by(User.created_at.asc())
    ).all()
    return {"users": [_user_payload(user) for user in users]}


@app.post("/api/platform/users/{user_id}/approve", dependencies=[Depends(require_admin)])
def platform_approve_user(
    user_id: str,
    payload: UserApprovalRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.scalar(select(User).options(selectinload(User.institution)).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="Lecturer account not found.")
    user.email_verified = True
    user.role = payload.role
    user.approved_at = utcnow()
    setup_code, expires_at = _issue_setup_code(user)
    if user.institution:
        user.institution.status = "active"
    audit(db, request, "lecturer_approved", resource_type="user", resource_id=user.id, detail={"role": payload.role})
    db.commit()
    return {"approved": True, "user": _user_payload(user), "setup_code": setup_code, "setup_code_expires_at": expires_at.isoformat()}


@app.post("/api/platform/users/{user_id}/reset-password", dependencies=[Depends(require_admin)])
def platform_reset_password(
    user_id: str,
    payload: AdminPasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.scalar(select(User).options(selectinload(User.institution)).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="Lecturer account not found.")
    setup_code, expires_at = _issue_setup_code(user)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request, "lecturer_setup_code_reissued", resource_type="user", resource_id=user.id)
    db.commit()
    return {
        "reset": True,
        "user": _user_payload(user),
        "setup_code": setup_code,
        "setup_code_expires_at": expires_at.isoformat(),
        "message": "A new one-time setup code was issued. Give it to the lecturer so they can create a new password and recovery PIN.",
    }


@app.post("/api/platform/users/{user_id}/status", dependencies=[Depends(require_admin)])
def platform_set_user_status(
    user_id: str,
    payload: AdminUserStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.scalar(select(User).options(selectinload(User.institution)).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="Lecturer account not found.")
    user.account_status = payload.status
    if payload.status == "active":
        if not user.recovery_pin_hash or not user.activated_at:
            raise HTTPException(status_code=409, detail="This account must be activated with a setup code before it can be active.")
        user.email_verified = True
        user.failed_login_count = 0
        user.locked_until = None
        if user.institution:
            user.institution.status = "active"
    else:
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request, f"lecturer_{payload.status}", resource_type="user", resource_id=user.id)
    db.commit()
    return {"updated": True, "user": _user_payload(user)}


@app.post("/api/platform/users/{user_id}/suspend", dependencies=[Depends(require_admin)])
def platform_suspend_user(
    user_id: str,
    payload: UserSuspensionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Lecturer account not found.")
    user.account_status = "suspended"
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, request, "lecturer_suspended", resource_type="user", resource_id=user.id, detail=payload.reason)
    db.commit()
    return {"suspended": True}


def _replacement_for_course(db: Session, course: Course, removed_user_id: str) -> User | None:
    links = db.execute(
        select(User, CourseLecturer.access_level)
        .join(CourseLecturer, CourseLecturer.lecturer_id == User.id)
        .where(
            CourseLecturer.course_id == course.id,
            User.id != removed_user_id,
            User.account_status == "active",
        )
    ).all()
    if links:
        priority = {"owner": 0, "co_lecturer": 1, "viewer": 2}
        links.sort(key=lambda row: priority.get(row[1], 9))
        return links[0][0]
    return db.scalar(
        select(User).where(
            User.institution_id == course.institution_id,
            User.id != removed_user_id,
            User.role == "institution_admin",
            User.account_status == "active",
        ).limit(1)
    )


@app.delete("/api/platform/users/{user_id}", dependencies=[Depends(require_admin)])
def platform_delete_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Lecturer account not found.")
    created_courses = db.scalars(select(Course).where(Course.created_by == user.id)).all()
    replacements: list[tuple[Course, User]] = []
    blocked: list[str] = []
    for course in created_courses:
        replacement = _replacement_for_course(db, course, user.id)
        if not replacement:
            blocked.append(f"{course.course_code} · {course.title}")
        else:
            replacements.append((course, replacement))
    if blocked:
        raise HTTPException(
            status_code=409,
            detail="Create or assign another active lecturer to these courses before deleting the account: " + ", ".join(blocked),
        )
    for course, replacement in replacements:
        course.created_by = replacement.id
        link = db.scalar(
            select(CourseLecturer).where(
                CourseLecturer.course_id == course.id,
                CourseLecturer.lecturer_id == replacement.id,
            )
        )
        if link:
            link.access_level = "owner"
        else:
            db.add(CourseLecturer(course_id=course.id, lecturer_id=replacement.id, access_level="owner"))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.execute(delete(CourseLecturer).where(CourseLecturer.lecturer_id == user.id))
    db.execute(update(Document).where(Document.submitted_to_lecturer_id == user.id).values(submitted_to_lecturer_id=None))
    db.execute(update(Assessment).where(Assessment.lecturer_id == user.id).values(lecturer_id=None))
    db.execute(update(PasswordResetRequest).where(PasswordResetRequest.user_id == user.id).values(user_id=None))
    db.execute(update(AuditLog).where(AuditLog.user_id == user.id).values(user_id=None))
    email = user.email
    db.delete(user)
    audit(db, request, "lecturer_account_deleted", resource_type="user", resource_id=user_id, detail={"email": email})
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Courses and co-lecturers
# ---------------------------------------------------------------------------


def _course_payload(db: Session, course: Course, current_user: User | None = None) -> dict[str, object]:
    lecturers = db.execute(
        select(User.full_name, User.email, CourseLecturer.access_level)
        .join(CourseLecturer, CourseLecturer.lecturer_id == User.id)
        .where(CourseLecturer.course_id == course.id)
        .order_by(CourseLecturer.access_level.desc(), User.full_name)
    ).all()
    submission_count = db.scalar(select(func.count(Document.id)).where(Document.course_id == course.id)) or 0
    my_access_level = None
    if current_user:
        if current_user.role == "institution_admin" and current_user.institution_id == course.institution_id:
            my_access_level = "institution_admin"
        else:
            my_access_level = db.scalar(
                select(CourseLecturer.access_level).where(
                    CourseLecturer.course_id == course.id,
                    CourseLecturer.lecturer_id == current_user.id,
                )
            )
    return {
        "id": course.id,
        "course_code": course.course_code,
        "title": course.title,
        "academic_year": course.academic_year,
        "semester": course.semester,
        "enrollment_code": course.enrollment_code,
        "status": course.status,
        "assessment_question_count": int(course.assessment_question_count or 20),
        "checkpoint_question_count": int(course.checkpoint_question_count or 3),
        "milestones_enabled": bool(course.milestones_enabled),
        "version_history_enabled": bool(course.version_history_enabled),
        "final_justification_required": bool(course.final_justification_required),
        "ai_declaration_required": bool(course.ai_declaration_required),
        **course_phase2_settings(course),
        "milestones": [
            {
                "id": milestone.id,
                "title": milestone.title,
                "description": milestone.description,
                "sequence": milestone.sequence,
                "required": milestone.required,
                "checkpoint_question_count": milestone.checkpoint_question_count,
            }
            for milestone in course.milestones
        ],
        "rubric": parse_rubric(course),
        "submission_count": int(submission_count),
        "my_access_level": my_access_level,
        "lecturers": [
            {"full_name": name, "email": email, "access_level": access}
            for name, email, access in lecturers
        ],
    }


@app.get("/api/lecturer/courses")
def lecturer_courses(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    ids = course_ids_for_user(db, user)
    courses = db.scalars(
        select(Course).where(Course.id.in_(ids)).order_by(Course.created_at.desc())
    ).all() if ids else []
    return {"courses": [_course_payload(db, course, user) for course in courses]}


@app.post("/api/lecturer/courses", status_code=201)
def create_course(
    payload: CourseCreateRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = Course(
        institution_id=user.institution_id,
        course_code=payload.course_code.strip().upper(),
        title=payload.title.strip(),
        academic_year=payload.academic_year.strip(),
        semester=payload.semester.strip(),
        enrollment_code=generate_enrollment_code(db),
        assessment_question_count=payload.assessment_question_count,
        checkpoint_question_count=payload.checkpoint_question_count,
        milestones_enabled=payload.milestones_enabled,
        version_history_enabled=payload.version_history_enabled,
        final_justification_required=True,
        ai_declaration_required=True,
        rubric_json=default_rubric_json(),
        oral_defence_enabled=payload.oral_defence_enabled,
        oral_defence_question_count=payload.oral_defence_question_count,
        practical_tasks_enabled=payload.practical_tasks_enabled,
        practical_task_count=payload.practical_task_count,
        supervisor_meetings_enabled=payload.supervisor_meetings_enabled,
        cross_version_analysis_enabled=payload.cross_version_analysis_enabled,
        random_moderation_enabled=payload.random_moderation_enabled,
        moderation_sample_percent=payload.moderation_sample_percent,
        moderation_variance_threshold=payload.moderation_variance_threshold,
        marker_anomaly_detection_enabled=payload.marker_anomaly_detection_enabled,
        created_by=user.id,
        status="active",
    )
    db.add(course)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This course already exists for the selected academic period.") from exc
    db.add(CourseLecturer(course_id=course.id, lecturer_id=user.id, access_level="owner"))
    milestone_titles = payload.milestone_titles
    if payload.milestones_enabled and not milestone_titles:
        milestone_titles = ["Problem or task definition", "Approach or method", "Draft development", "Final preparation"]
    for sequence, title in enumerate(milestone_titles, start=1):
        db.add(
            CourseMilestone(
                course_id=course.id,
                title=title,
                sequence=sequence,
                required=True,
                checkpoint_question_count=payload.checkpoint_question_count,
            )
        )
    audit(db, request, "course_created", user=user, resource_type="course", resource_id=course.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This course already exists for the selected academic period.") from exc
    return {"course": _course_payload(db, course, user)}


@app.post("/api/lecturer/courses/{course_id}/collaborators", status_code=201)
def add_course_collaborator(
    course_id: str,
    payload: CourseCollaboratorRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    collaborator = db.scalar(select(User).where(User.email == normalize_email(str(payload.email))))
    if not collaborator or collaborator.account_status != "active":
        raise HTTPException(status_code=404, detail="No active lecturer account was found for that email.")
    if collaborator.institution_id != course.institution_id:
        raise HTTPException(status_code=409, detail="A co-lecturer must belong to the same institution.")
    existing = db.scalar(
        select(CourseLecturer).where(
            CourseLecturer.course_id == course.id,
            CourseLecturer.lecturer_id == collaborator.id,
        )
    )
    if existing:
        existing.access_level = payload.access_level
    else:
        db.add(
            CourseLecturer(
                course_id=course.id,
                lecturer_id=collaborator.id,
                access_level=payload.access_level,
            )
        )
    audit(
        db,
        request,
        "course_collaborator_added",
        user=user,
        resource_type="course",
        resource_id=course.id,
        detail={"email": collaborator.email, "access_level": payload.access_level},
    )
    db.commit()
    return {"added": True, "course": _course_payload(db, course, user)}


@app.patch("/api/lecturer/courses/{course_id}/settings")
def update_course_settings(
    course_id: str,
    payload: CourseSettingsRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    changes: dict[str, object] = {}
    if payload.assessment_question_count is not None:
        course.assessment_question_count = payload.assessment_question_count
        changes["assessment_question_count"] = payload.assessment_question_count
    if payload.checkpoint_question_count is not None:
        course.checkpoint_question_count = payload.checkpoint_question_count
        changes["checkpoint_question_count"] = payload.checkpoint_question_count
        for milestone in course.milestones:
            milestone.checkpoint_question_count = payload.checkpoint_question_count
    if payload.milestones_enabled is not None:
        course.milestones_enabled = payload.milestones_enabled
        changes["milestones_enabled"] = payload.milestones_enabled
    if payload.version_history_enabled is not None:
        course.version_history_enabled = payload.version_history_enabled
        changes["version_history_enabled"] = payload.version_history_enabled
    for field_name in (
        "oral_defence_enabled",
        "oral_defence_question_count",
        "practical_tasks_enabled",
        "practical_task_count",
        "supervisor_meetings_enabled",
        "cross_version_analysis_enabled",
        "random_moderation_enabled",
        "moderation_sample_percent",
        "moderation_variance_threshold",
        "marker_anomaly_detection_enabled",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(course, field_name, value)
            changes[field_name] = value
    audit(
        db,
        request,
        "course_assessment_settings_updated",
        user=user,
        resource_type="course",
        resource_id=course.id,
        detail=changes,
    )
    db.commit()
    db.refresh(course)
    return {"course": _course_payload(db, course, user)}


def _require_grading_access(db: Session, user: User, course_id: str) -> Course:
    course = require_course_access(db, user, course_id)
    if user.role == "institution_admin" and user.institution_id == course.institution_id:
        return course
    access = db.scalar(
        select(CourseLecturer.access_level).where(
            CourseLecturer.course_id == course.id,
            CourseLecturer.lecturer_id == user.id,
        )
    )
    if access not in {"owner", "co_lecturer"}:
        raise HTTPException(status_code=403, detail="This account has view-only access to the course.")
    return course


@app.post("/api/lecturer/courses/{course_id}/milestones", status_code=201)
def create_course_milestone(
    course_id: str,
    payload: CourseMilestoneCreateRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    sequence = (db.scalar(select(func.max(CourseMilestone.sequence)).where(CourseMilestone.course_id == course.id)) or 0) + 1
    milestone = CourseMilestone(
        course_id=course.id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        sequence=sequence,
        required=payload.required,
        checkpoint_question_count=payload.checkpoint_question_count,
    )
    course.milestones_enabled = True
    db.add(milestone)
    audit(db, request, "course_milestone_created", user=user, resource_type="course", resource_id=course.id, detail={"title": milestone.title})
    db.commit()
    db.refresh(course)
    return {"course": _course_payload(db, course, user)}


@app.delete("/api/lecturer/courses/{course_id}/milestones/{milestone_id}")
def delete_course_milestone(
    course_id: str,
    milestone_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    milestone = db.get(CourseMilestone, milestone_id)
    if not milestone or milestone.course_id != course.id:
        raise HTTPException(status_code=404, detail="Milestone not found.")
    linked = db.scalar(select(func.count(Document.id)).where(Document.milestone_id == milestone.id)) or 0
    if linked:
        raise HTTPException(status_code=409, detail="This milestone already has student submissions and cannot be deleted.")
    db.delete(milestone)
    audit(db, request, "course_milestone_deleted", user=user, resource_type="course", resource_id=course.id, detail={"title": milestone.title})
    db.commit()
    return {"deleted": True}


@app.get("/api/lecturer/courses/{course_id}/rubric")
def get_course_rubric(
    course_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id)
    return {"course_id": course.id, "criteria": parse_rubric(course)}


@app.put("/api/lecturer/courses/{course_id}/rubric")
def update_course_rubric(
    course_id: str,
    payload: CourseRubricRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    criteria = [item.model_dump() for item in payload.criteria]
    course.rubric_json = json.dumps(criteria, ensure_ascii=False)
    audit(db, request, "course_rubric_updated", user=user, resource_type="course", resource_id=course.id, detail={"criteria_count": len(criteria)})
    db.commit()
    return {"course_id": course.id, "criteria": criteria}


@app.get("/api/lecturer/courses/{course_id}/learning-progress")
def learning_progress(
    course_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id)
    return {
        "course": _course_payload(db, course, user),
        "students": course_learning_progress(db, course),
    }


@app.get("/api/courses/resolve")
def resolve_course_code(
    code: str = Query(min_length=6, max_length=24),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = db.scalar(
        select(Course)
        .options(selectinload(Course.milestones))
        .where(func.upper(Course.enrollment_code) == code.strip().upper(), Course.status == "active")
    )
    if not course:
        raise HTTPException(status_code=404, detail="The course enrolment code is invalid or inactive.")
    owner = db.scalar(
        select(User.full_name)
        .join(CourseLecturer, CourseLecturer.lecturer_id == User.id)
        .where(CourseLecturer.course_id == course.id, CourseLecturer.access_level == "owner")
        .limit(1)
    )
    institution = db.get(Institution, course.institution_id)
    return {
        "course_id": course.id,
        "course": f"{course.course_code} · {course.title}",
        "institution": institution.name if institution else None,
        "lecturer": owner,
        "assessment_question_count": int(course.assessment_question_count or 20),
        "checkpoint_question_count": int(course.checkpoint_question_count or 3),
        "milestones_enabled": bool(course.milestones_enabled),
        "version_history_enabled": bool(course.version_history_enabled),
        "final_justification_required": True,
        "ai_declaration_required": True,
        "milestones": [
            {
                "id": milestone.id,
                "title": milestone.title,
                "sequence": milestone.sequence,
                "required": milestone.required,
                "checkpoint_question_count": milestone.checkpoint_question_count,
            }
            for milestone in course.milestones
        ],
    }


@app.post("/api/lecturer/courses/{course_id}/regenerate-code")
def regenerate_course_code(
    course_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    course = require_course_access(db, user, course_id, owner_only=True)
    course.enrollment_code = generate_enrollment_code(db)
    audit(db, request, "course_code_regenerated", user=user, resource_type="course", resource_id=course.id)
    db.commit()
    return {"enrollment_code": course.enrollment_code}


# ---------------------------------------------------------------------------
# Student document upload and assessment
# ---------------------------------------------------------------------------


def _generate_questions_job(document_id: str) -> None:
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return
        logger.info("Question generation started for document %s", document_id)
        document.status = "generating"
        document.processing_error = None
        db.commit()
        bank, mode = generate_question_bank(document.extracted_text, document.title)
        db.execute(delete(Question).where(Question.document_id == document.id))
        for generated in bank.questions:
            db.add(Question(document_id=document.id, **question_to_record(generated)))
        document.generation_mode = mode
        document.status = "ready"
        db.commit()
        logger.info("Question generation completed for document %s", document_id)
    except Exception as exc:
        logger.exception("Question generation failed for document %s", document_id)
        db.rollback()
        document = db.get(Document, document_id)
        if document:
            document.status = "failed"
            detail = getattr(exc, "detail", None)
            document.processing_error = str(detail or exc)[:1500]
            db.commit()
    finally:
        db.close()


@app.post("/api/documents", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    request: Request,
    student_name: str = Form(..., min_length=2, max_length=180),
    student_id: str = Form(..., min_length=2, max_length=100),
    title: str = Form(..., min_length=3, max_length=300),
    course_code: str = Form(..., min_length=6, max_length=24),
    submission_stage: str = Form(default="final", max_length=30),
    milestone_id: str | None = Form(default=None),
    understanding_summary: str = Form(..., min_length=60, max_length=4000),
    key_decisions: str = Form(..., min_length=60, max_length=4000),
    main_contribution: str = Form(..., min_length=40, max_length=3000),
    stated_limitation: str = Form(..., min_length=30, max_length=2500),
    ai_use_level: str = Form(default="none", max_length=30),
    ai_use_details: str | None = Form(default=None, max_length=3000),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = db.scalar(
        select(Course)
        .options(selectinload(Course.milestones))
        .where(
            func.upper(Course.enrollment_code) == course_code.strip().upper(),
            Course.status == "active",
        )
    )
    if not course:
        raise HTTPException(status_code=404, detail="The course enrolment code is invalid or inactive.")

    stage = submission_stage.strip().lower()
    if stage not in {"milestone", "final"}:
        raise HTTPException(status_code=422, detail="Submission stage must be milestone or final.")
    milestone: CourseMilestone | None = None
    if stage == "milestone":
        if not course.milestones_enabled:
            raise HTTPException(status_code=409, detail="This course is configured for final submission only.")
        if not milestone_id:
            raise HTTPException(status_code=422, detail="Select the learning milestone for this submission.")
        milestone = db.get(CourseMilestone, milestone_id)
        if not milestone or milestone.course_id != course.id:
            raise HTTPException(status_code=404, detail="The selected milestone does not belong to this course.")
    elif milestone_id:
        raise HTTPException(status_code=422, detail="A final submission cannot be linked to a milestone.")

    ai_level = ai_use_level.strip().lower()
    if ai_level not in {"none", "limited", "substantial", "required"}:
        raise HTTPException(status_code=422, detail="Select a valid AI-use declaration.")
    if ai_level != "none" and not (ai_use_details or "").strip():
        raise HTTPException(status_code=422, detail="Explain how AI was used and how its output was checked.")

    owner_id = db.scalar(
        select(CourseLecturer.lecturer_id).where(
            CourseLecturer.course_id == course.id,
            CourseLecturer.access_level == "owner",
        ).limit(1)
    )
    _, extracted_text, filename, digest = await read_and_extract(file)

    previous: Document | None = None
    version_number = 1
    if course.version_history_enabled or course.cross_version_analysis_enabled:
        previous = db.scalar(
            select(Document)
            .where(
                Document.course_id == course.id,
                func.lower(Document.student_id) == student_id.strip().lower(),
                func.lower(Document.title) == title.strip().lower(),
            )
            .order_by(Document.version_number.desc(), Document.created_at.desc())
            .limit(1)
        )
        if previous:
            version_number = int(previous.version_number or 1) + 1

    question_override = (
        int(milestone.checkpoint_question_count or course.checkpoint_question_count or 3)
        if milestone
        else int(course.assessment_question_count or 20)
    )
    document = Document(
        student_name=student_name.strip(),
        student_id=student_id.strip(),
        title=title.strip(),
        original_filename=filename,
        file_hash=digest,
        extracted_text=extracted_text,
        word_count=len(extracted_text.split()),
        status="queued",
        generation_mode="pending",
        institution_id=course.institution_id,
        course_id=course.id,
        submitted_to_lecturer_id=owner_id,
        milestone_id=milestone.id if milestone else None,
        previous_version_id=previous.id if previous else None,
        submission_stage=stage,
        version_number=version_number,
        assessment_question_count_override=question_override,
        understanding_summary=understanding_summary.strip(),
        key_decisions=key_decisions.strip(),
        main_contribution=main_contribution.strip(),
        stated_limitation=stated_limitation.strip(),
        ai_use_level=ai_level,
        ai_use_details=(ai_use_details or "").strip() or None,
    )
    db.add(document)
    db.flush()
    refresh_document_alerts(db, document)
    if course.cross_version_analysis_enabled and document.previous_version_id:
        ensure_version_analysis(db, document)
    audit(
        db,
        request,
        "student_document_uploaded",
        resource_type="document",
        resource_id=document.id,
        detail={
            "course_id": course.id,
            "student_id": student_id.strip(),
            "submission_stage": stage,
            "milestone_id": milestone.id if milestone else None,
            "version_number": version_number,
            "ai_use_level": ai_level,
        },
    )
    db.commit()
    db.refresh(document)
    background_tasks.add_task(_generate_questions_job, document.id)
    return {
        "document_id": document.id,
        "status": document.status,
        "word_count": document.word_count,
        "course": f"{course.course_code} · {course.title}",
        "submission_stage": stage,
        "milestone": milestone.title if milestone else None,
        "version_number": version_number,
        "assessment_question_count": question_override,
        "message": "The submission was accepted and the understanding assessment is being prepared.",
    }


@app.get("/api/documents/{document_id}/status")
def document_status(document_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    created_at = aware(document.created_at) or utcnow()
    elapsed_seconds = max(0, int((utcnow() - created_at).total_seconds()))
    stale_seconds = max(1, settings.generation_stale_minutes) * 60
    if document.status in {"queued", "generating"} and elapsed_seconds >= stale_seconds:
        document.status = "failed"
        document.processing_error = (
            f"Question generation exceeded {settings.generation_stale_minutes} minutes and was stopped. "
            "Retry generation. If it happens again, reduce MAX_CONTEXT_CHARS or check OpenAI billing and logs."
        )
        db.commit()
    question_count = db.scalar(
        select(func.count()).select_from(Question).where(Question.document_id == document.id)
    )
    course = db.get(Course, document.course_id) if document.course_id else None
    assessment_question_count = int(
        document.assessment_question_count_override
        or (course.assessment_question_count if course else 20)
        or 20
    )
    return {
        "document_id": document.id,
        "status": document.status,
        "question_count": int(question_count or 0),
        "assessment_question_count": assessment_question_count,
        "pass_threshold": settings.pass_threshold,
        "generation_mode": document.generation_mode,
        "submission_stage": document.submission_stage,
        "version_number": document.version_number,
        "error": document.processing_error,
        "elapsed_seconds": elapsed_seconds,
        "stale_after_seconds": stale_seconds,
    }


@app.post("/api/documents/{document_id}/retry", status_code=202)
def retry_document_generation(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed or timed-out generation can be retried.")
    db.execute(delete(Question).where(Question.document_id == document.id))
    document.status = "queued"
    document.generation_mode = "pending"
    document.processing_error = None
    document.created_at = utcnow()
    db.commit()
    background_tasks.add_task(_generate_questions_job, document.id)
    return {"document_id": document.id, "status": document.status, "message": "Question generation restarted."}


@app.post("/api/assessments/start")
def begin_assessment(payload: StartAssessmentRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    assessment, token = start_assessment(
        db, payload.document_id, payload.client_instance_id
    )
    return {
        "assessment_id": assessment.id,
        "session_token": token,
        "question_count": int(assessment.question_count or len(assessment.items) or 20),
        "pass_threshold": settings.pass_threshold,
        "webcam_required": settings.webcam_required,
        "monitoring_enabled": settings.webcam_required,
        "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
        "resume_window_minutes": settings.assessment_resume_window_minutes,
        "max_interruptions": settings.max_assessment_interruptions,
        "max_offline_seconds": settings.max_assessment_offline_seconds,
        "instructions": (
            f"Questions appear one at a time. Each question allows {settings.question_time_seconds} seconds. "
            "You cannot return to an earlier question. Camera monitoring and connection continuity checks "
            "remain active during the assessment. Interrupted sessions resume only after camera reverification."
        ),
    }


@app.post("/api/assessments/{assessment_id}/heartbeat")
def assessment_heartbeat(
    assessment_id: str,
    payload: AssessmentHeartbeatRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    return heartbeat_assessment(
        db,
        assessment,
        client_instance_id=payload.client_instance_id,
        camera_verified=payload.camera_verified,
        reason=payload.reason,
    )


@app.post("/api/assessments/{assessment_id}/interrupt")
def assessment_interrupt(
    assessment_id: str,
    payload: AssessmentInterruptRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    if assessment.client_instance_id and assessment.client_instance_id != payload.client_instance_id:
        raise HTTPException(status_code=403, detail="The assessment browser identity does not match.")
    interrupt_assessment(db, assessment, reason=payload.reason)
    return assessment_state_payload(assessment)


@app.get("/api/assessments/{assessment_id}/question")
def assessment_question(
    assessment_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    return current_question(db, assessment)


@app.post("/api/assessments/{assessment_id}/answer")
def assessment_answer(
    assessment_id: str,
    payload: AnswerRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    return submit_answer(db, assessment, payload.selected_index)


@app.post("/api/assessments/{assessment_id}/snapshot")
async def assessment_snapshot(
    assessment_id: str,
    image: UploadFile = File(...),
    capture_reason: str = Form(default="random"),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    snapshot = assessment.webcam_snapshot
    if not snapshot:
        raise HTTPException(status_code=409, detail="No webcam capture was scheduled for this assessment.")
    if snapshot.image_data:
        return {"captured": True, "already_captured": True, "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None}
    content_type = (image.content_type or "").lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="The webcam snapshot must be a JPEG or PNG image.")
    payload = await image.read()
    max_bytes = max(100, settings.webcam_max_image_kb) * 1024
    if not payload:
        raise HTTPException(status_code=400, detail="The webcam snapshot was empty.")
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail=f"The webcam snapshot exceeds {settings.webcam_max_image_kb} KB.")
    snapshot.image_data = payload
    snapshot.mime_type = content_type
    snapshot.capture_reason = capture_reason[:30]
    snapshot.captured_at = utcnow()
    snapshot.status = "captured"
    db.commit()
    return {"captured": True, "already_captured": False, "captured_at": snapshot.captured_at.isoformat()}


@app.post("/api/assessments/{assessment_id}/focus-event")
def focus_event(
    assessment_id: str,
    payload: FocusEventRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    if assessment.status == "in_progress":
        assessment.focus_loss_count += 1
        db.add(
            MonitoringEvent(
                assessment_id=assessment.id,
                event_type="tab_hidden",
                severity="warning",
                duration_ms=0,
                question_position=assessment.current_position,
                message="The assessment tab or window lost focus.",
                corrected=True,
                resolved_at=utcnow(),
            )
        )
        db.commit()
    return {"recorded": True, "count": assessment.focus_loss_count}


@app.post("/api/assessments/{assessment_id}/monitoring-event")
def assessment_monitoring_event(
    assessment_id: str,
    payload: MonitoringEventRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    if assessment.status != "in_progress":
        return {
            "recorded": False,
            "reason": "assessment_complete",
            "monitoring_event_count": len(assessment.monitoring_events),
        }

    if payload.corrected:
        event = db.scalar(
            select(MonitoringEvent)
            .where(
                MonitoringEvent.assessment_id == assessment.id,
                MonitoringEvent.event_type == payload.event_type,
                MonitoringEvent.corrected.is_(False),
            )
            .order_by(MonitoringEvent.created_at.desc())
            .limit(1)
        )
        if event:
            event.corrected = True
            event.resolved_at = utcnow()
            if payload.duration_ms:
                event.duration_ms = max(event.duration_ms, payload.duration_ms)
            db.commit()
        count = db.scalar(
            select(func.count(MonitoringEvent.id)).where(
                MonitoringEvent.assessment_id == assessment.id
            )
        ) or 0
        return {"recorded": bool(event), "corrected": True, "monitoring_event_count": int(count)}

    # Avoid writing repeated rows for the same ongoing condition.
    existing = db.scalar(
        select(MonitoringEvent)
        .where(
            MonitoringEvent.assessment_id == assessment.id,
            MonitoringEvent.event_type == payload.event_type,
            MonitoringEvent.corrected.is_(False),
        )
        .order_by(MonitoringEvent.created_at.desc())
        .limit(1)
    )
    if existing:
        existing.duration_ms = max(existing.duration_ms, payload.duration_ms)
        existing.question_position = payload.question_position or existing.question_position
        existing.severity = "critical" if "critical" in {existing.severity, payload.severity} else "warning"
        existing.message = payload.message or existing.message
        db.commit()
        count = db.scalar(
            select(func.count(MonitoringEvent.id)).where(
                MonitoringEvent.assessment_id == assessment.id
            )
        ) or 0
        return {
            "recorded": True,
            "event_id": existing.id,
            "monitoring_event_count": int(count),
            "updated": True,
        }

    event = MonitoringEvent(
        assessment_id=assessment.id,
        event_type=payload.event_type,
        severity=payload.severity,
        duration_ms=payload.duration_ms,
        question_position=payload.question_position or assessment.current_position,
        message=payload.message,
        corrected=False,
    )
    db.add(event)
    db.commit()
    count = db.scalar(
        select(func.count(MonitoringEvent.id)).where(
            MonitoringEvent.assessment_id == assessment.id
        )
    ) or 0
    return {
        "recorded": True,
        "event_id": event.id,
        "monitoring_event_count": int(count),
        "updated": False,
    }


@app.get("/api/assessments/{assessment_id}/result")
def assessment_result(
    assessment_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    return result_payload(assessment)


# ---------------------------------------------------------------------------
# Lecturer-scoped review and reporting
# ---------------------------------------------------------------------------


def _monitoring_event_payload(event: MonitoringEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "severity": event.severity,
        "duration_ms": event.duration_ms,
        "question_position": event.question_position,
        "message": event.message,
        "corrected": event.corrected,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "resolved_at": event.resolved_at.isoformat() if event.resolved_at else None,
    }


def _submission_rows(db: Session, documents: list[Document]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for document in documents:
        latest = db.scalar(
            select(Assessment)
            .options(
                selectinload(Assessment.webcam_snapshot),
                selectinload(Assessment.monitoring_events),
            )
            .where(Assessment.document_id == document.id)
            .order_by(Assessment.started_at.desc())
            .limit(1)
        )
        if latest:
            refresh_interruption_state(db, latest)
        course = db.get(Course, document.course_id) if document.course_id else None
        milestone = db.get(CourseMilestone, document.milestone_id) if document.milestone_id else None
        alerts = db.scalars(
            select(LearningAlert)
            .where(LearningAlert.document_id == document.id, LearningAlert.status != "resolved")
            .order_by(LearningAlert.created_at.desc())
        ).all()
        rows.append({
            "document_id": document.id,
            "student_name": document.student_name,
            "student_id": document.student_id,
            "title": document.title,
            "course_id": course.id if course else None,
            "course": f"{course.course_code} · {course.title}" if course else "Legacy submission",
            "status": document.status,
            "generation_mode": document.generation_mode,
            "word_count": document.word_count,
            "submission_stage": document.submission_stage,
            "milestone": milestone.title if milestone else None,
            "version_number": document.version_number,
            "ai_use_level": document.ai_use_level,
            "learning_alert_count": len(alerts),
            "learning_alert_critical_count": sum(1 for alert in alerts if alert.severity == "critical"),
            "learning_alerts": [alert_payload(alert) for alert in alerts[:5]],
            "created_at": document.created_at.isoformat(),
            "assessment_id": latest.id if latest else None,
            "assessment_status": latest.status if latest else None,
            "score": latest.score if latest else None,
            "decision": latest.decision if latest else None,
            "snapshot_available": bool(latest and latest.webcam_snapshot and latest.webcam_snapshot.image_data),
            "snapshot_status": latest.webcam_snapshot.status if latest and latest.webcam_snapshot else None,
            "snapshot_captured_at": latest.webcam_snapshot.captured_at.isoformat() if latest and latest.webcam_snapshot and latest.webcam_snapshot.captured_at else None,
            "monitoring_event_count": len(latest.monitoring_events) if latest else 0,
            "monitoring_unresolved_count": sum(1 for event in latest.monitoring_events if not event.corrected) if latest else 0,
            "monitoring_critical_count": sum(1 for event in latest.monitoring_events if event.severity == "critical") if latest else 0,
            "interruption_count": latest.interruption_count if latest else 0,
            "total_offline_seconds": latest.total_offline_seconds if latest else 0,
            "current_offline_seconds": (
                max(0, int((utcnow() - aware(latest.interruption_started_at)).total_seconds()))
                if latest and latest.interruption_started_at else 0
            ),
            "resume_count": latest.resume_count if latest else 0,
            "resume_deadline_at": latest.resume_deadline_at.isoformat() if latest and latest.resume_deadline_at else None,
            "last_interruption_reason": latest.last_interruption_reason if latest else None,
            "lock_reason": latest.lock_reason if latest else None,
            "interruption_excused": latest.interruption_excused if latest else False,
            "oral_defence_status": (
                db.scalar(select(OralDefence.status).where(OralDefence.assessment_id == latest.id))
                if latest else None
            ),
            "practical_task_count": (
                db.scalar(select(func.count(PracticalTask.id)).where(PracticalTask.assessment_id == latest.id)) or 0
                if latest else 0
            ),
            "moderation_status": (
                db.scalar(select(ModerationRecord.status).where(ModerationRecord.assessment_id == latest.id))
                if latest else None
            ),
        })
    return rows


def _assessment_for_lecturer(db: Session, user: User, assessment_id: str) -> Assessment:
    assessment = db.scalar(
        select(Assessment)
        .options(
            selectinload(Assessment.document),
            selectinload(Assessment.webcam_snapshot),
            selectinload(Assessment.monitoring_events),
            selectinload(Assessment.items).selectinload(AssessmentItem.question),
        )
        .where(Assessment.id == assessment_id)
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    require_document_access(db, user, assessment.document_id)
    refresh_interruption_state(db, assessment)
    return assessment


def _assessment_detail(db: Session, assessment: Assessment) -> dict[str, object]:
    details = []
    for item in sorted(assessment.items, key=lambda value: value.position):
        options = json.loads(item.shuffled_options_json)
        details.append({
            "position": item.position,
            "stem": item.question.stem,
            "options": options,
            "selected_index": item.selected_index,
            "correct_index": item.correct_shuffled_index,
            "is_correct": item.is_correct,
            "timed_out": item.timed_out,
            "response_ms": item.response_ms,
            "difficulty": item.question.difficulty,
            "category": item.question.category,
            "adaptive_reason": item.adaptive_reason,
            "time_limit_seconds": settings.question_time_seconds,
            "source_location": item.question.source_location,
            "source_quote": item.question.source_quote,
            "explanation": item.question.explanation,
        })
    summary = result_payload(assessment) if assessment.status == "completed" else {
        "assessment_id": assessment.id,
        "status": assessment.status,
        "student_name": assessment.document.student_name,
        "student_id": assessment.document.student_id,
        "document_title": assessment.document.title,
    }
    document = assessment.document
    course = db.get(Course, document.course_id) if document.course_id else None
    milestone = db.get(CourseMilestone, document.milestone_id) if document.milestone_id else None
    snapshot = assessment.webcam_snapshot
    summary["snapshot_available"] = bool(snapshot and snapshot.image_data)
    summary["snapshot_status"] = snapshot.status if snapshot else None
    summary["snapshot_captured_at"] = snapshot.captured_at.isoformat() if snapshot and snapshot.captured_at else None
    summary.update({
        "document_id": document.id,
        "course_id": document.course_id,
        "submission_stage": document.submission_stage,
        "milestone": milestone.title if milestone else None,
        "version_number": document.version_number,
        "ai_use_level": document.ai_use_level,
    })
    monitoring_events = sorted(list(assessment.monitoring_events or []), key=lambda event: event.created_at)
    summary["monitoring_event_count"] = len(monitoring_events)
    summary["monitoring_unresolved_count"] = sum(1 for event in monitoring_events if not event.corrected)
    summary["monitoring_critical_count"] = sum(1 for event in monitoring_events if event.severity == "critical")
    summary.update({
        "interruption_count": assessment.interruption_count,
        "total_offline_seconds": assessment.total_offline_seconds,
        "current_offline_seconds": (
            max(0, int((utcnow() - aware(assessment.interruption_started_at)).total_seconds()))
            if assessment.interruption_started_at else 0
        ),
        "resume_count": assessment.resume_count,
        "resume_deadline_at": assessment.resume_deadline_at.isoformat() if assessment.resume_deadline_at else None,
        "last_interruption_reason": assessment.last_interruption_reason,
        "lock_reason": assessment.lock_reason,
        "locked_at": assessment.locked_at.isoformat() if assessment.locked_at else None,
        "camera_reverification_required": assessment.camera_reverification_required,
        "interruption_excused": assessment.interruption_excused,
        "interruption_note": assessment.interruption_note,
    })

    versions = db.scalars(
        select(Document)
        .where(
            Document.course_id == document.course_id,
            func.lower(Document.student_id) == document.student_id.lower(),
            func.lower(Document.title) == document.title.lower(),
        )
        .order_by(Document.version_number, Document.created_at)
    ).all() if document.course_id else [document]
    previous = db.get(Document, document.previous_version_id) if document.previous_version_id else None
    alerts = db.scalars(
        select(LearningAlert).where(LearningAlert.document_id == document.id).order_by(LearningAlert.created_at.desc())
    ).all()
    rubric_mark = db.scalar(select(RubricMark).where(RubricMark.assessment_id == assessment.id))
    audit_rows = db.scalars(
        select(MarkAuditLog).where(MarkAuditLog.assessment_id == assessment.id).order_by(MarkAuditLog.created_at.desc())
    ).all()
    rubric = parse_rubric(course) if course else []
    oral_defence = db.scalar(select(OralDefence).where(OralDefence.assessment_id == assessment.id))
    practical_tasks = db.scalars(
        select(PracticalTask).where(PracticalTask.assessment_id == assessment.id).order_by(PracticalTask.sequence)
    ).all()
    version_analysis = db.scalar(select(VersionAnalysis).where(VersionAnalysis.document_id == document.id))
    moderation = db.scalar(select(ModerationRecord).where(ModerationRecord.assessment_id == assessment.id))
    supervisor_meetings = db.scalars(
        select(SupervisorMeeting)
        .where(
            SupervisorMeeting.course_id == document.course_id,
            func.lower(SupervisorMeeting.student_id) == document.student_id.lower(),
        )
        .order_by(SupervisorMeeting.meeting_at.desc())
        .limit(20)
    ).all() if document.course_id else []
    mark_payload = None
    if rubric_mark:
        mark_payload = {
            "id": rubric_mark.id,
            "criteria": json.loads(rubric_mark.criteria_json),
            "total_score": rubric_mark.total_score,
            "overall_comment": rubric_mark.overall_comment,
            "updated_at": rubric_mark.updated_at.isoformat() if rubric_mark.updated_at else None,
        }

    return {
        "summary": summary,
        "questions": details,
        "monitoring_events": [_monitoring_event_payload(event) for event in monitoring_events],
        "justification": {
            "understanding_summary": document.understanding_summary,
            "key_decisions": document.key_decisions,
            "main_contribution": document.main_contribution,
            "stated_limitation": document.stated_limitation,
            "ai_use_level": document.ai_use_level,
            "ai_use_details": document.ai_use_details,
        },
        "version_history": [
            {
                "document_id": item.id,
                "version_number": item.version_number,
                "submission_stage": item.submission_stage,
                "milestone": (db.get(CourseMilestone, item.milestone_id).title if item.milestone_id and db.get(CourseMilestone, item.milestone_id) else None),
                "word_count": item.word_count,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in versions
        ],
        "version_comparison": version_comparison(document, previous),
        "learning_alerts": [alert_payload(alert) for alert in alerts],
        "phase2_settings": course_phase2_settings(course),
        "oral_defence": oral_defence_payload(oral_defence),
        "practical_tasks": [practical_task_payload(task) for task in practical_tasks],
        "supervisor_meetings": [supervisor_meeting_payload(item) for item in supervisor_meetings],
        "cross_version_analysis": version_analysis_payload(version_analysis),
        "moderation": moderation_payload(moderation),
        "rubric": rubric,
        "rubric_mark": mark_payload,
        "mark_audit": [
            {
                "id": row.id,
                "previous_total": row.previous_total,
                "new_total": row.new_total,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in audit_rows
        ],
    }


@app.get("/api/lecturer/submissions")
def lecturer_submissions(
    course_id: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    ids = course_ids_for_user(db, user)
    if course_id:
        require_course_access(db, user, course_id)
        ids = [course_id]
    documents = db.scalars(
        select(Document).where(Document.course_id.in_(ids)).order_by(Document.created_at.desc()).limit(200)
    ).all() if ids else []
    return {"submissions": _submission_rows(db, documents)}


@app.get("/api/lecturer/assessments/{assessment_id}")
def lecturer_assessment_detail(
    assessment_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    audit(db, request, "assessment_reviewed", user=user, resource_type="assessment", resource_id=assessment.id)
    db.commit()
    return _assessment_detail(db, assessment)


@app.post("/api/lecturer/assessments/{assessment_id}/rubric-mark")
def save_rubric_mark(
    assessment_id: str,
    payload: RubricMarkRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    if not assessment.course_id:
        raise HTTPException(status_code=409, detail="This legacy assessment has no course rubric.")
    course = _require_grading_access(db, user, assessment.course_id)
    rubric = parse_rubric(course)
    rubric_by_key = {item["key"]: item for item in rubric}
    submitted = {item.criterion_key: item for item in payload.scores}
    if set(submitted) != set(rubric_by_key):
        raise HTTPException(status_code=422, detail="A score is required for every rubric criterion.")

    criteria_payload: list[dict[str, object]] = []
    total = 0.0
    for key, criterion in rubric_by_key.items():
        score_item = submitted[key]
        maximum = float(criterion["max_score"])
        if score_item.score > maximum:
            raise HTTPException(status_code=422, detail=f"{criterion['label']} cannot exceed {maximum:g}.")
        total += score_item.score
        criteria_payload.append({
            "key": key,
            "label": criterion["label"],
            "description": criterion.get("description"),
            "max_score": maximum,
            "score": round(score_item.score, 2),
            "comment": score_item.comment.strip() if score_item.comment else None,
        })
    total = round(total, 2)
    existing = db.scalar(select(RubricMark).where(RubricMark.assessment_id == assessment.id))
    if existing and not (payload.change_reason or "").strip():
        raise HTTPException(status_code=422, detail="Give a reason when changing an existing rubric mark.")
    previous_total = existing.total_score if existing else None
    previous_json = existing.criteria_json if existing else None
    new_json = json.dumps(criteria_payload, ensure_ascii=False)
    if existing:
        existing.criteria_json = new_json
        existing.total_score = total
        existing.overall_comment = payload.overall_comment.strip() if payload.overall_comment else None
        existing.graded_by = user.id
        existing.updated_at = utcnow()
        mark = existing
    else:
        mark = RubricMark(
            assessment_id=assessment.id,
            graded_by=user.id,
            criteria_json=new_json,
            total_score=total,
            overall_comment=payload.overall_comment.strip() if payload.overall_comment else None,
        )
        db.add(mark)
    db.add(MarkAuditLog(
        assessment_id=assessment.id,
        user_id=user.id,
        previous_total=previous_total,
        new_total=total,
        previous_json=previous_json,
        new_json=new_json,
        reason=(payload.change_reason or "Initial rubric mark").strip(),
    ))
    ensure_random_moderation(db, assessment, course, total)
    if course.marker_anomaly_detection_enabled:
        scan_marker_anomalies(db, course)
    audit(db, request, "rubric_mark_saved", user=user, resource_type="assessment", resource_id=assessment.id, detail={"previous_total": previous_total, "new_total": total})
    db.commit()
    return {"saved": True, "total_score": total, "assessment": _assessment_detail(db, assessment)}



# ---------------------------------------------------------------------------
# Optional Phase 2 learning-assurance modules
# ---------------------------------------------------------------------------


def _phase2_assessment_course(
    db: Session,
    user: User,
    assessment_id: str,
    feature: str,
) -> tuple[Assessment, Course]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    if not assessment.course_id:
        raise HTTPException(status_code=409, detail="This assessment is not assigned to a course.")
    course = _require_grading_access(db, user, assessment.course_id)
    if not bool(getattr(course, feature, False)):
        raise HTTPException(status_code=409, detail="This optional Phase 2 module is not enabled for the course.")
    return assessment, course


@app.post("/api/lecturer/assessments/{assessment_id}/oral-defence/start")
def start_oral_defence(
    assessment_id: str,
    payload: OralDefenceStartRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment, course = _phase2_assessment_course(db, user, assessment_id, "oral_defence_enabled")
    record = prepare_oral_defence(db, assessment, course, user.id, payload.question_count)
    audit(db, request, "oral_defence_prepared", user=user, resource_type="assessment", resource_id=assessment.id)
    db.commit()
    return {"oral_defence": oral_defence_payload(record)}


@app.post("/api/lecturer/assessments/{assessment_id}/oral-defence/complete")
def finish_oral_defence(
    assessment_id: str,
    payload: OralDefenceCompleteRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment, course = _phase2_assessment_course(db, user, assessment_id, "oral_defence_enabled")
    record = db.scalar(select(OralDefence).where(OralDefence.assessment_id == assessment.id))
    if not record:
        record = prepare_oral_defence(db, assessment, course, user.id)
    try:
        complete_oral_defence(
            record,
            user.id,
            [item.model_dump() for item in payload.scores],
            payload.outcome,
            payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        request,
        "oral_defence_completed",
        user=user,
        resource_type="assessment",
        resource_id=assessment.id,
        detail={"score": record.total_score, "outcome": record.outcome},
    )
    db.commit()
    return {"oral_defence": oral_defence_payload(record)}


@app.post("/api/lecturer/assessments/{assessment_id}/practical-tasks/generate")
def generate_practical_tasks(
    assessment_id: str,
    payload: PracticalTaskGenerateRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment, course = _phase2_assessment_course(db, user, assessment_id, "practical_tasks_enabled")
    tasks = prepare_practical_tasks(db, assessment, course, payload.count)
    audit(db, request, "practical_tasks_prepared", user=user, resource_type="assessment", resource_id=assessment.id, detail={"count": len(tasks)})
    db.commit()
    return {"practical_tasks": [practical_task_payload(item) for item in tasks]}


@app.post("/api/lecturer/practical-tasks/{task_id}/score")
def score_practical_task(
    task_id: str,
    payload: PracticalTaskScoreRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    task = db.get(PracticalTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Practical task not found.")
    assessment, _ = _phase2_assessment_course(db, user, task.assessment_id, "practical_tasks_enabled")
    if payload.score > task.max_score:
        raise HTTPException(status_code=422, detail=f"The task score cannot exceed {task.max_score:g}.")
    task.student_response = payload.student_response.strip()
    task.score = payload.score
    task.notes = payload.notes.strip() if payload.notes else None
    task.assessed_by = user.id
    task.status = "completed"
    task.completed_at = utcnow()
    audit(db, request, "practical_task_scored", user=user, resource_type="practical_task", resource_id=task.id, detail={"assessment_id": assessment.id, "score": task.score})
    db.commit()
    return {"practical_task": practical_task_payload(task)}


@app.get("/api/lecturer/courses/{course_id}/supervisor-meetings")
def list_supervisor_meetings(
    course_id: str,
    student_id: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id)
    if not course.supervisor_meetings_enabled:
        raise HTTPException(status_code=409, detail="Supervisor meeting records are not enabled for this course.")
    statement = select(SupervisorMeeting).where(SupervisorMeeting.course_id == course.id)
    if student_id:
        statement = statement.where(func.lower(SupervisorMeeting.student_id) == student_id.strip().lower())
    meetings = db.scalars(statement.order_by(SupervisorMeeting.meeting_at.desc()).limit(200)).all()
    return {"meetings": [supervisor_meeting_payload(item) for item in meetings]}


@app.post("/api/lecturer/courses/{course_id}/supervisor-meetings", status_code=201)
def create_supervisor_meeting(
    course_id: str,
    payload: SupervisorMeetingRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = _require_grading_access(db, user, course_id)
    if not course.supervisor_meetings_enabled:
        raise HTTPException(status_code=409, detail="Supervisor meeting records are not enabled for this course.")
    document = db.get(Document, payload.document_id) if payload.document_id else None
    if document and document.course_id != course.id:
        raise HTTPException(status_code=422, detail="The selected document does not belong to this course.")
    meeting = SupervisorMeeting(
        course_id=course.id,
        document_id=document.id if document else None,
        student_id=payload.student_id.strip(),
        recorded_by=user.id,
        meeting_at=payload.meeting_at,
        meeting_type=payload.meeting_type,
        summary=payload.summary.strip(),
        decisions=payload.decisions.strip() if payload.decisions else None,
        action_items=payload.action_items.strip() if payload.action_items else None,
        next_meeting_at=payload.next_meeting_at,
    )
    db.add(meeting)
    db.flush()
    audit(db, request, "supervisor_meeting_recorded", user=user, resource_type="supervisor_meeting", resource_id=meeting.id, detail={"student_id": meeting.student_id})
    db.commit()
    return {"meeting": supervisor_meeting_payload(meeting)}


@app.delete("/api/lecturer/courses/{course_id}/supervisor-meetings/{meeting_id}")
def delete_supervisor_meeting(
    course_id: str,
    meeting_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    course = _require_grading_access(db, user, course_id)
    meeting = db.get(SupervisorMeeting, meeting_id)
    if not meeting or meeting.course_id != course.id:
        raise HTTPException(status_code=404, detail="Supervisor meeting record not found.")
    db.delete(meeting)
    audit(db, request, "supervisor_meeting_deleted", user=user, resource_type="supervisor_meeting", resource_id=meeting_id)
    db.commit()
    return {"deleted": True}


@app.post("/api/lecturer/documents/{document_id}/version-analysis")
def run_cross_version_analysis(
    document_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    document = require_document_access(db, user, document_id, write=True)
    course = db.get(Course, document.course_id) if document.course_id else None
    if not course or not course.cross_version_analysis_enabled:
        raise HTTPException(status_code=409, detail="Cross-version analysis is not enabled for this course.")
    if not document.previous_version_id:
        raise HTTPException(status_code=409, detail="A previous version is required for cross-version analysis.")
    analysis = ensure_version_analysis(db, document, user.id, refresh=True)
    audit(db, request, "cross_version_analysis_run", user=user, resource_type="document", resource_id=document.id)
    db.commit()
    return {"cross_version_analysis": version_analysis_payload(analysis)}


@app.get("/api/lecturer/courses/{course_id}/moderation-queue")
def moderation_queue(
    course_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id)
    if not course.random_moderation_enabled:
        raise HTTPException(status_code=409, detail="Random moderation is not enabled for this course.")
    rows = db.execute(
        select(ModerationRecord, Assessment, Document)
        .join(Assessment, Assessment.id == ModerationRecord.assessment_id)
        .join(Document, Document.id == Assessment.document_id)
        .where(ModerationRecord.course_id == course.id)
        .order_by(ModerationRecord.selected_at.desc())
    ).all()
    return {"moderation_queue": [
        {
            **(moderation_payload(record) or {}),
            "assessment_id": assessment.id,
            "student_name": document.student_name,
            "student_id": document.student_id,
            "document_title": document.title,
        }
        for record, assessment, document in rows
    ]}


@app.post("/api/lecturer/assessments/{assessment_id}/moderation/complete")
def complete_moderation(
    assessment_id: str,
    payload: ModerationCompleteRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment, course = _phase2_assessment_course(db, user, assessment_id, "random_moderation_enabled")
    record = db.scalar(select(ModerationRecord).where(ModerationRecord.assessment_id == assessment.id))
    if not record:
        raise HTTPException(status_code=404, detail="This assessment was not selected for moderation.")
    original_mark = db.scalar(select(RubricMark).where(RubricMark.assessment_id == assessment.id))
    if original_mark and original_mark.graded_by == user.id and user.role != "institution_admin":
        raise HTTPException(status_code=403, detail="The original marker cannot moderate the same assessment.")
    record.moderator_id = user.id
    record.moderated_mark = payload.moderated_mark
    record.variance = round(payload.moderated_mark - record.original_mark, 2)
    record.decision = payload.decision
    record.notes = payload.notes.strip()
    record.status = "completed"
    record.completed_at = utcnow()
    audit(db, request, "moderation_completed", user=user, resource_type="assessment", resource_id=assessment.id, detail={"variance": record.variance, "decision": record.decision})
    db.commit()
    return {"moderation": moderation_payload(record), "variance_threshold": course.moderation_variance_threshold}


@app.get("/api/lecturer/courses/{course_id}/marker-anomalies")
def get_marker_anomalies(
    course_id: str,
    refresh: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = require_course_access(db, user, course_id, owner_only=True)
    if not course.marker_anomaly_detection_enabled:
        raise HTTPException(status_code=409, detail="Marker anomaly detection is not enabled for this course.")
    if refresh:
        scan_marker_anomalies(db, course)
        db.commit()
    alerts = db.scalars(
        select(MarkerAnomalyAlert).where(MarkerAnomalyAlert.course_id == course.id).order_by(MarkerAnomalyAlert.generated_at.desc())
    ).all()
    marker_names = {
        marker_id: full_name for marker_id, full_name in db.execute(
            select(User.id, User.full_name).where(User.id.in_([item.marker_id for item in alerts if item.marker_id]))
        ).all()
    } if alerts else {}
    return {"anomalies": [{**anomaly_payload(item), "marker_name": marker_names.get(item.marker_id, "Unknown marker")} for item in alerts]}


@app.post("/api/lecturer/marker-anomalies/{alert_id}")
def update_marker_anomaly(
    alert_id: str,
    payload: MarkerAnomalyActionRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    alert = db.get(MarkerAnomalyAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Marker anomaly alert not found.")
    require_course_access(db, user, alert.course_id, owner_only=True)
    alert.status = payload.status
    alert.resolution_note = payload.note.strip() if payload.note else None
    alert.resolved_by = user.id
    alert.resolved_at = utcnow() if payload.status == "resolved" else None
    audit(db, request, "marker_anomaly_updated", user=user, resource_type="marker_anomaly", resource_id=alert.id, detail={"status": payload.status})
    db.commit()
    return {"anomaly": anomaly_payload(alert)}


@app.post("/api/lecturer/learning-alerts/{alert_id}")
def update_learning_alert(
    alert_id: str,
    payload: LearningAlertActionRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    alert = db.get(LearningAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Learning alert not found.")
    require_document_access(db, user, alert.document_id, write=True)
    alert.status = payload.status
    alert.resolution_note = payload.note.strip() if payload.note else None
    alert.resolved_at = utcnow() if payload.status == "resolved" else None
    audit(db, request, "learning_alert_updated", user=user, resource_type="learning_alert", resource_id=alert.id, detail={"status": payload.status})
    db.commit()
    return {"alert": alert_payload(alert)}


@app.get("/api/lecturer/assessments/{assessment_id}/snapshot")
def lecturer_assessment_snapshot(
    assessment_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    snapshot = assessment.webcam_snapshot
    if not snapshot or not snapshot.image_data:
        raise HTTPException(status_code=404, detail="No webcam snapshot is available.")
    audit(db, request, "snapshot_viewed", user=user, resource_type="assessment", resource_id=assessment.id)
    db.commit()
    return Response(
        content=snapshot.image_data,
        media_type=snapshot.mime_type or "image/jpeg",
        headers={"Cache-Control": "private, no-store", "Content-Disposition": 'inline; filename="kanokware-webcam-snapshot.jpg"'},
    )


@app.get("/api/lecturer/assessments/{assessment_id}/report.pdf")
def lecturer_pdf_report(
    assessment_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    if assessment.status != "completed":
        raise HTTPException(status_code=409, detail="The assessment is not complete.")
    pdf = build_pdf_report(assessment)
    audit(db, request, "report_downloaded", user=user, resource_type="assessment", resource_id=assessment.id)
    db.commit()
    safe_id = assessment.document.student_id.replace("/", "-").replace("\\", "-")
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="kanokware-{safe_id}.pdf"'})


@app.post("/api/lecturer/assessments/{assessment_id}/allow-resume")
def lecturer_allow_resume(
    assessment_id: str,
    payload: InterruptionNoteRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    require_document_access(db, user, assessment.document_id, write=True)
    allow_assessment_resume(db, assessment, payload.note)
    audit(
        db, request, "assessment_resume_allowed", user=user,
        resource_type="assessment", resource_id=assessment.id,
        detail={"note": payload.note},
    )
    db.commit()
    return {"allowed": True, "state": assessment_state_payload(assessment)}


@app.post("/api/lecturer/assessments/{assessment_id}/finish-interrupted")
def lecturer_finish_interrupted(
    assessment_id: str,
    payload: InterruptionNoteRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    require_document_access(db, user, assessment.document_id, write=True)
    if payload.note:
        assessment.interruption_note = payload.note.strip()
    finish_interrupted_assessment(db, assessment)
    audit(
        db, request, "assessment_finished_after_interruption", user=user,
        resource_type="assessment", resource_id=assessment.id,
        detail={"note": payload.note},
    )
    db.commit()
    return {"finished": True, "result": result_payload(assessment)}


@app.post("/api/lecturer/assessments/{assessment_id}/excuse-interruption")
def lecturer_excuse_interruption(
    assessment_id: str,
    payload: InterruptionNoteRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    require_document_access(db, user, assessment.document_id, write=True)
    set_interruption_excused(db, assessment, excused=True, note=payload.note)
    audit(
        db, request, "assessment_interruption_excused", user=user,
        resource_type="assessment", resource_id=assessment.id,
        detail={"note": payload.note},
    )
    db.commit()
    return {"excused": True, "state": assessment_state_payload(assessment)}


@app.delete("/api/lecturer/assessments/{assessment_id}")
def lecturer_reset_assessment(
    assessment_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    assessment = _assessment_for_lecturer(db, user, assessment_id)
    require_document_access(db, user, assessment.document_id, write=True)
    db.delete(assessment)
    audit(db, request, "assessment_reset", user=user, resource_type="assessment", resource_id=assessment_id)
    db.commit()
    return {"deleted": True, "retake_enabled": True}


@app.delete("/api/lecturer/documents/{document_id}")
def lecturer_delete_document(
    document_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    document = require_document_access(db, user, document_id, write=True)
    db.delete(document)
    audit(db, request, "submission_deleted", user=user, resource_type="document", resource_id=document_id)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Backward-compatible platform administrator review endpoints
# ---------------------------------------------------------------------------


@app.get("/api/admin/submissions", dependencies=[Depends(require_admin)])
def admin_submissions(db: Session = Depends(get_db)) -> dict[str, object]:
    documents = db.scalars(select(Document).order_by(Document.created_at.desc()).limit(200)).all()
    return {"submissions": _submission_rows(db, documents)}


@app.get("/api/admin/assessments/{assessment_id}", dependencies=[Depends(require_admin)])
def admin_assessment_detail(assessment_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    assessment = db.scalar(
        select(Assessment)
        .options(
            selectinload(Assessment.document),
            selectinload(Assessment.webcam_snapshot),
            selectinload(Assessment.monitoring_events),
            selectinload(Assessment.items).selectinload(AssessmentItem.question),
        )
        .where(Assessment.id == assessment_id)
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    return _assessment_detail(db, assessment)


@app.get("/api/admin/assessments/{assessment_id}/snapshot", dependencies=[Depends(require_admin)])
def admin_assessment_snapshot(assessment_id: str, db: Session = Depends(get_db)) -> Response:
    snapshot = db.scalar(select(WebcamSnapshot).where(WebcamSnapshot.assessment_id == assessment_id))
    if not snapshot or not snapshot.image_data:
        raise HTTPException(status_code=404, detail="No webcam snapshot is available.")
    return Response(content=snapshot.image_data, media_type=snapshot.mime_type or "image/jpeg", headers={"Cache-Control": "private, no-store"})


@app.get("/api/admin/assessments/{assessment_id}/report.pdf", dependencies=[Depends(require_admin)])
def admin_pdf_report(assessment_id: str, db: Session = Depends(get_db)) -> Response:
    assessment = db.scalar(
        select(Assessment)
        .options(selectinload(Assessment.document), selectinload(Assessment.webcam_snapshot), selectinload(Assessment.monitoring_events), selectinload(Assessment.items).selectinload(AssessmentItem.question))
        .where(Assessment.id == assessment_id)
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if assessment.status != "completed":
        raise HTTPException(status_code=409, detail="The assessment is not complete.")
    return Response(content=build_pdf_report(assessment), media_type="application/pdf")


@app.delete("/api/admin/assessments/{assessment_id}", dependencies=[Depends(require_admin)])
def admin_reset_assessment(assessment_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    db.delete(assessment)
    db.commit()
    return {"deleted": True, "retake_enabled": True}


@app.delete("/api/admin/documents/{document_id}", dependencies=[Depends(require_admin)])
def admin_delete_document(document_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.delete(document)
    db.commit()
    return {"deleted": True}
