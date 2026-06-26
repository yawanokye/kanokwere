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
from sqlalchemy import delete, func, select, text
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
    AuthSession,
    Course,
    CourseLecturer,
    Document,
    Institution,
    Question,
    User,
    WebcamSnapshot,
)
from .question_service import generate_question_bank, question_to_record
from .report_service import build_pdf_report
from .schemas import (
    AnswerRequest,
    ChangePasswordRequest,
    CourseCollaboratorRequest,
    CourseCreateRequest,
    FocusEventRequest,
    LecturerRegisterRequest,
    LoginRequest,
    StartAssessmentRequest,
    UserApprovalRequest,
    UserSuspensionRequest,
)
from .security import (
    bearer_token,
    create_lecturer_session,
    current_user,
    hash_password,
    hash_token,
    require_admin,
    verify_password,
)


logger = logging.getLogger("kanokwere")


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


app = FastAPI(title="Kanokwere", version="0.4.0", lifespan=lifespan)
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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
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
        "staff_id": user.staff_id,
        "account_status": user.account_status,
        "email_verified": user.email_verified,
        "institution": {
            "id": user.institution.id,
            "name": user.institution.name,
            "domain": user.institution.domain,
            "status": user.institution.status,
        } if user.institution else None,
    }


@app.post("/api/auth/register", status_code=201)
def register_lecturer(
    payload: LecturerRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not settings.registration_enabled:
        raise HTTPException(status_code=403, detail="Lecturer registration is currently closed.")

    email = normalize_email(str(payload.email))
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account already exists for this email address.")

    domain = email_domain(email)
    institution = db.scalar(select(Institution).where(Institution.domain == domain))
    if not institution:
        institution = db.scalar(
            select(Institution).where(func.lower(Institution.name) == payload.institution_name.strip().casefold())
        )
    if not institution:
        institution = Institution(
            name=payload.institution_name.strip(),
            domain=domain,
            status="pending",
        )
        db.add(institution)
        db.flush()

    user = User(
        institution_id=institution.id,
        full_name=payload.full_name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        department=payload.department.strip(),
        staff_id=payload.staff_id.strip(),
        role="lecturer",
        account_status="pending",
        email_verified=False,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        request,
        "lecturer_registered",
        user=user,
        resource_type="user",
        resource_id=user.id,
        detail={"institution": institution.name, "domain": domain},
    )
    db.commit()
    return {
        "status": "pending",
        "message": "Registration received. A platform or institution administrator must approve the account before sign-in.",
        "email": email,
        "institution": institution.name,
    }


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

    locked_until = aware(user.locked_until)
    if locked_until and locked_until > utcnow():
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = utcnow() + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if user.account_status == "pending":
        raise HTTPException(status_code=403, detail="Your lecturer account is awaiting approval.")
    if user.account_status != "active" or not user.email_verified:
        raise HTTPException(status_code=403, detail="This lecturer account is not active.")
    if not user.institution or user.institution.status != "active":
        raise HTTPException(status_code=403, detail="Your institution is not active on Kanokwere.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    token = create_lecturer_session(db, user)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.lecturer_session_hours * 3600,
        httponly=True,
        secure=settings.environment.casefold() == "production",
        samesite="lax",
        path="/",
    )
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
def lecturer_me(user: User = Depends(current_user)) -> dict[str, object]:
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
    db.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != request.state.auth_session.id)
    )
    audit(db, request, "password_changed", user=user, resource_type="user", resource_id=user.id)
    db.commit()
    return {"changed": True}


# ---------------------------------------------------------------------------
# Platform administration and approvals
# ---------------------------------------------------------------------------


@app.get("/api/platform/pending", dependencies=[Depends(require_admin)])
def platform_pending(db: Session = Depends(get_db)) -> dict[str, object]:
    users = db.scalars(
        select(User)
        .options(selectinload(User.institution))
        .where(User.account_status == "pending")
        .order_by(User.created_at.asc())
    ).all()
    return {"users": [_user_payload(user) | {"created_at": user.created_at.isoformat()} for user in users]}


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
    user.account_status = "active"
    user.email_verified = True
    user.role = payload.role
    user.approved_at = utcnow()
    if user.institution:
        user.institution.status = "active"
    audit(db, request, "lecturer_approved", resource_type="user", resource_id=user.id, detail={"role": payload.role})
    db.commit()
    return {"approved": True, "user": _user_payload(user)}


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
    return {
        "document_id": document.id,
        "status": document.status,
        "question_count": int(question_count or 0),
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
        "question_count": 20,
        "pass_threshold": settings.pass_threshold,
        "webcam_required": settings.webcam_required,
        "instructions": (
            f"Questions appear one at a time. Each question allows {settings.question_time_seconds} seconds. "
            "You cannot return to an earlier question. The webcam remains active during the assessment. "
            "No video or audio is recorded, and one still image is captured at a random point."
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
        db.commit()
    return {"recorded": True, "count": assessment.focus_loss_count}


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


def _submission_rows(db: Session, documents: list[Document]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for document in documents:
        latest = db.scalar(
            select(Assessment)
            .options(selectinload(Assessment.webcam_snapshot))
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
        })
    return rows


def _assessment_for_lecturer(db: Session, user: User, assessment_id: str) -> Assessment:
    assessment = db.scalar(
        select(Assessment)
        .options(
            selectinload(Assessment.document),
            selectinload(Assessment.webcam_snapshot),
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
    return {"summary": summary, "questions": details}


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
        headers={"Cache-Control": "private, no-store", "Content-Disposition": 'inline; filename="kanokwere-webcam-snapshot.jpg"'},
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
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="kanokwere-{safe_id}.pdf"'})


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
        .options(selectinload(Assessment.document), selectinload(Assessment.webcam_snapshot), selectinload(Assessment.items).selectinload(AssessmentItem.question))
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
