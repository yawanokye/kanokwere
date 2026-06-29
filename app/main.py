from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    current_question,
    get_assessment,
    result_payload,
    start_assessment,
    submit_answer,
)
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .document_service import read_and_extract
from .models import (
    Assessment,
    AssessmentItem,
    AuditLog,
    AuthSession,
    Course,
    CourseLecturer,
    Document,
    Institution,
    MonitoringEvent,
    PasswordResetRequest,
    Question,
    User,
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
    ChangePasswordRequest,
    CourseCollaboratorRequest,
    CourseCreateRequest,
    CourseSettingsRequest,
    FocusEventRequest,
    MonitoringEventRequest,
    LecturerRegisterRequest,
    LoginRequest,
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
        if interrupted:
            db.commit()
    yield


app = FastAPI(title="Kanokware", version="0.7.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
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
        created_by=user.id,
        status="active",
    )
    db.add(course)
    db.flush()
    db.add(CourseLecturer(course_id=course.id, lecturer_id=user.id, access_level="owner"))
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
    course.assessment_question_count = payload.assessment_question_count
    audit(
        db,
        request,
        "course_assessment_settings_updated",
        user=user,
        resource_type="course",
        resource_id=course.id,
        detail={"assessment_question_count": payload.assessment_question_count},
    )
    db.commit()
    db.refresh(course)
    return {"course": _course_payload(db, course, user)}


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
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    course = db.scalar(
        select(Course).where(
            func.upper(Course.enrollment_code) == course_code.strip().upper(),
            Course.status == "active",
        )
    )
    if not course:
        raise HTTPException(status_code=404, detail="The course enrolment code is invalid or inactive.")
    owner_id = db.scalar(
        select(CourseLecturer.lecturer_id).where(
            CourseLecturer.course_id == course.id,
            CourseLecturer.access_level == "owner",
        ).limit(1)
    )
    _, extracted_text, filename, digest = await read_and_extract(file)
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
    )
    db.add(document)
    db.flush()
    audit(
        db,
        request,
        "student_document_uploaded",
        resource_type="document",
        resource_id=document.id,
        detail={"course_id": course.id, "student_id": student_id.strip()},
    )
    db.commit()
    db.refresh(document)
    background_tasks.add_task(_generate_questions_job, document.id)
    return {
        "document_id": document.id,
        "status": document.status,
        "word_count": document.word_count,
        "course": f"{course.course_code} · {course.title}",
        "assessment_question_count": int(course.assessment_question_count or 20),
        "message": "The document was accepted and question generation has started.",
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
    assessment_question_count = int(course.assessment_question_count or 20) if course else 20
    return {
        "document_id": document.id,
        "status": document.status,
        "question_count": int(question_count or 0),
        "assessment_question_count": assessment_question_count,
        "pass_threshold": settings.pass_threshold,
        "generation_mode": document.generation_mode,
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
    assessment, token = start_assessment(db, payload.document_id)
    return {
        "assessment_id": assessment.id,
        "session_token": token,
        "question_count": int(assessment.question_count or len(assessment.items) or 20),
        "pass_threshold": settings.pass_threshold,
        "webcam_required": settings.webcam_required,
        "monitoring_enabled": settings.webcam_required,
        "instructions": (
            f"Questions appear one at a time. Each question allows {settings.question_time_seconds} seconds. "
            "You cannot return to an earlier question. The webcam remains active during the assessment. "
            "No video or audio is recorded. Live face-presence checks run in the browser, one still image "
            "is captured at a random point, and warning events are available to the assigned lecturer."
        ),
    }


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
        course = db.get(Course, document.course_id) if document.course_id else None
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
    return assessment


def _assessment_detail(assessment: Assessment) -> dict[str, object]:
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
    snapshot = assessment.webcam_snapshot
    summary["snapshot_available"] = bool(snapshot and snapshot.image_data)
    summary["snapshot_status"] = snapshot.status if snapshot else None
    summary["snapshot_captured_at"] = snapshot.captured_at.isoformat() if snapshot and snapshot.captured_at else None
    monitoring_events = sorted(
        list(assessment.monitoring_events or []),
        key=lambda event: event.created_at,
    )
    summary["monitoring_event_count"] = len(monitoring_events)
    summary["monitoring_unresolved_count"] = sum(1 for event in monitoring_events if not event.corrected)
    summary["monitoring_critical_count"] = sum(1 for event in monitoring_events if event.severity == "critical")
    return {
        "summary": summary,
        "questions": details,
        "monitoring_events": [_monitoring_event_payload(event) for event in monitoring_events],
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
    return _assessment_detail(assessment)


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
    return _assessment_detail(assessment)


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
