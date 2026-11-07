"""Optional student identity and question-linked oral evidence.

No biometric identification, voiceprints, automated identity decisions or external
speech services are used. MCQ marks remain independent of staff oral judgement.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .access_service import audit, require_course_access, require_document_access
from .assessment_service import get_assessment
from .config import settings
from .database import get_db
from .feature_service import assessment_features
from .models import (Assessment, AssessmentDistanceEvidence, AssessmentTask, Course,
    Document, StudentAccount, StudentDocumentLink, StudentEnrollment, StudentOralResponse,
    StudentPhoto, StudentSession, TaskDistanceSettings, User)
from .phase2_service import prepare_oral_defence
from .schemas import validate_password
from .security import (_aware, bearer_token, current_user, generate_setup_code,
    hash_password, hash_token, utcnow, verify_password)

router = APIRouter()
COOKIE = "kanokware_student_session"
DEFAULT_DISTANCE = {"oral_question_count": 3, "oral_response_seconds": 120,
    "oral_window_hours": 48, "typed_alternative": True}


class StudentCredentials(BaseModel):
    course_code: str = Field(min_length=6, max_length=24)
    student_number: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=1, max_length=128)
    setup_code: str | None = Field(default=None, max_length=64)


class ProvisionStudent(BaseModel):
    student_number: str = Field(min_length=2, max_length=100)
    full_name: str = Field(min_length=2, max_length=180)


class ReviewIdentity(BaseModel):
    status: Literal["verified", "review_required"]
    note: str = Field(min_length=10, max_length=2000)


class DistanceSettings(BaseModel):
    oral_question_count: int = Field(default=3, ge=1)
    oral_response_seconds: int = Field(default=120, ge=10)
    oral_window_hours: int = Field(default=48, ge=1)
    typed_alternative: bool = True
    proctor_groups: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("proctor_groups")
    @classmethod
    def clean_groups(cls, groups: list[str]) -> list[str]:
        cleaned = sorted(set(value.strip() for value in groups if value.strip()))
        if any(len(value) > 120 for value in cleaned):
            raise ValueError("Monitor group labels must not exceed 120 characters.")
        return cleaned


def distance_settings(db: Session, task_id: str | None) -> dict:
    row = db.get(TaskDistanceSettings, task_id) if task_id else None
    result = {key: getattr(row, key) if row else value for key, value in DEFAULT_DISTANCE.items()}
    result["proctor_groups"] = json.loads(row.proctor_groups_json) if row else []
    return result


def course_from_code(db: Session, code: str) -> Course:
    course = db.scalar(select(Course).where(
        func.upper(Course.enrollment_code) == code.strip().upper(), Course.status == "active"))
    if not course:
        raise HTTPException(401, "The student credentials or course code are incorrect.")
    return course


def session_student(db: Session, request: Request, required: bool = True) -> StudentAccount | None:
    token = request.cookies.get(COOKIE, "")
    row = db.scalar(select(StudentSession).where(StudentSession.token_hash == hash_token(token))) if token else None
    account = db.get(StudentAccount, row.student_id) if row and _aware(row.expires_at) > utcnow() else None
    if not account or account.status != "active":
        if required:
            raise HTTPException(401, "Sign in to the student portal to continue.")
        return None
    return account


def enrollment(db: Session, account: StudentAccount, course: Course) -> StudentEnrollment:
    row = db.scalar(select(StudentEnrollment).where(StudentEnrollment.course_id == course.id,
        StudentEnrollment.student_id == account.id, StudentEnrollment.status == "active"))
    if not row or account.institution_id != course.institution_id:
        raise HTTPException(403, "Your account is not on the active student roster for this course.")
    return row


def latest_photo(db: Session, student_id: str, verified: bool = False) -> StudentPhoto | None:
    query = select(StudentPhoto).where(StudentPhoto.student_id == student_id)
    if verified:
        query = query.where(StudentPhoto.status == "verified")
    return db.scalar(query.order_by(StudentPhoto.created_at.desc()).limit(1))


def require_student_for_task(db: Session, request: Request, course: Course, features: dict,
                             candidate_id: str, *, expected_student_id: str | None = None) -> StudentAccount | None:
    required = features.get("student_authentication") or features.get("student_identity_verification")
    # A signed-in student is linked only when the submitted identifier is theirs.
    account = session_student(db, request, required=bool(required or expected_student_id))
    if not account:
        return None
    if not required and not expected_student_id and candidate_id.strip().casefold() != account.student_number:
        return None
    enrollment(db, account, course)
    if candidate_id.strip().casefold() != account.student_number or (expected_student_id and expected_student_id != account.id):
        raise HTTPException(403, "Use the student ID belonging to your signed-in account.")
    if features.get("student_identity_verification") and not latest_photo(db, account.id, verified=True):
        raise HTTPException(409, "An authorised staff member must verify your reference photograph before this task can start.")
    return account


def bind_assessment(db: Session, assessment: Assessment, account: StudentAccount | None) -> None:
    if db.get(AssessmentDistanceEvidence, assessment.id):
        return
    features = assessment_features(assessment)
    identity_required = features.get("student_identity_verification", False)
    photo = latest_photo(db, account.id, verified=True) if account and identity_required else None
    config = distance_settings(db, assessment.document.task_id)
    db.add(AssessmentDistanceEvidence(assessment_id=assessment.id,
        student_id=account.id if account else None, reference_photo_id=photo.id if photo else None,
        identity_status="capture_required" if identity_required else "not_required",
        settings_json=json.dumps(config)))
    db.commit()


def enforce_assessment_student(db: Session, request: Request, assessment_id: str) -> None:
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if evidence and evidence.student_id:
        account = session_student(db, request)
        if account.id != evidence.student_id:
            raise HTTPException(403, "This assessment belongs to another student account.")
        assessment = db.get(Assessment, assessment_id)
        course = db.get(Course, assessment.course_id)
        enrollment(db, account, course)


def student_payload(db: Session, account: StudentAccount) -> dict:
    photo = latest_photo(db, account.id)
    verified = latest_photo(db, account.id, verified=True)
    return {"id": account.id, "student_number": account.student_number, "full_name": account.full_name,
        "status": account.status, "photo_status": photo.status if photo else "not_submitted",
        "verified_reference_available": bool(verified)}


def sign_in(db: Session, response: Response, account: StudentAccount) -> None:
    token = secrets.token_urlsafe(48)
    db.add(StudentSession(student_id=account.id, token_hash=hash_token(token), expires_at=utcnow() + timedelta(hours=12)))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.environment == "production",
        samesite="lax", max_age=43200, path="/")


def credentials_account(db: Session, payload: StudentCredentials) -> StudentAccount:
    course = course_from_code(db, payload.course_code)
    account = db.scalar(select(StudentAccount).where(StudentAccount.institution_id == course.institution_id,
        StudentAccount.student_number == payload.student_number.strip().casefold()))
    if not account:
        raise HTTPException(401, "The student credentials or course code are incorrect.")
    enrollment(db, account, course)
    if account.locked_until and _aware(account.locked_until) > utcnow():
        raise HTTPException(429, "Too many failed attempts. Try again after 15 minutes.")
    return account


def credential_failure(db: Session, account: StudentAccount) -> None:
    account.failed_count += 1
    if account.failed_count >= 5:
        account.locked_until = utcnow() + timedelta(minutes=15)
        account.failed_count = 0
    db.commit()
    raise HTTPException(401, "The student credentials or setup code are incorrect.")


@router.post("/api/student/activate")
def activate(payload: StudentCredentials, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        if len(payload.password) < 10:
            raise ValueError("Password must have at least 10 characters.")
        validate_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    account = credentials_account(db, payload)
    if account.status != "pending_activation" or not account.setup_hash or not account.setup_expires_at or _aware(account.setup_expires_at) <= utcnow():
        raise HTTPException(409, "Ask your course owner for a new student setup code.")
    if not secrets.compare_digest(hash_token((payload.setup_code or "").strip().upper()), account.setup_hash):
        credential_failure(db, account)
    account.password_hash = hash_password(payload.password)
    account.status = "active"
    account.setup_hash = None
    account.setup_expires_at = None
    account.failed_count = 0
    account.locked_until = None
    audit(db, request, "student_account_activated", resource_type="student", resource_id=account.id)
    sign_in(db, response, account)
    return {"student": student_payload(db, account)}


@router.post("/api/student/login")
def login(payload: StudentCredentials, response: Response, db: Session = Depends(get_db)):
    account = credentials_account(db, payload)
    if account.status != "active" or not account.password_hash or not verify_password(payload.password, account.password_hash):
        credential_failure(db, account)
    account.failed_count = 0
    account.locked_until = None
    sign_in(db, response, account)
    return {"student": student_payload(db, account)}


@router.post("/api/student/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    db.execute(delete(StudentSession).where(StudentSession.token_hash == hash_token(request.cookies.get(COOKIE, ""))))
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/api/student/me")
def me(request: Request, db: Session = Depends(get_db)):
    account = session_student(db, request, required=False)
    return {"student": student_payload(db, account) if account else None}


@router.get("/api/student/workspace")
def workspace(request: Request, db: Session = Depends(get_db)):
    account = session_student(db, request)
    courses = db.scalars(select(Course).join(StudentEnrollment, StudentEnrollment.course_id == Course.id)
        .where(StudentEnrollment.student_id == account.id, StudentEnrollment.status == "active", Course.status == "active")).all()
    rows = db.scalars(select(AssessmentDistanceEvidence).where(AssessmentDistanceEvidence.student_id == account.id)
        .order_by(AssessmentDistanceEvidence.created_at.desc()).limit(100)).all()
    attempts = []
    for row in rows:
        assessment = db.get(Assessment, row.assessment_id)
        if assessment.course_id not in {course.id for course in courses}:
            continue
        attempts.append({"assessment_id": assessment.id, "title": assessment.document.title,
            "status": assessment.status, "mcq_score": assessment.score, "identity_status": row.identity_status,
            "oral_enabled": bool(assessment_features(assessment).get("recorded_oral_responses"))})
    return {"student": student_payload(db, account), "courses": [
        {"id": course.id, "title": f"{course.course_code} · {course.title}", "code": course.enrollment_code} for course in courses], "attempts": attempts}


async def image_bytes(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise HTTPException(413, "Use a JPEG or PNG no larger than 1 MB.")
    mime = "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else None
    if not mime:
        raise HTTPException(415, "The image must be a genuine JPEG or PNG.")
    # Decode safely, reject decompression bombs and strip metadata by re-encoding.
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                raise HTTPException(413, "The image dimensions are too large.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1200, 1200))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(415, "The image could not be decoded.")


@router.post("/api/student/reference-photo", status_code=201)
async def submit_reference_photo(request: Request, photo: UploadFile = File(...), consent: bool = Form(False), db: Session = Depends(get_db)):
    account = session_student(db, request)
    if not consent:
        raise HTTPException(422, "Confirm that this photograph may be retained for authorised identity review.")
    pending = db.scalar(select(StudentPhoto).where(StudentPhoto.student_id == account.id, StudentPhoto.status == "pending_review"))
    if pending:
        raise HTTPException(409, "A photograph is already awaiting staff review.")
    data, mime = await image_bytes(photo)
    db.add(StudentPhoto(student_id=account.id, image_data=data, mime_type=mime))
    audit(db, request, "student_reference_photo_submitted", resource_type="student", resource_id=account.id)
    db.commit()
    return {"status": "pending_review", "message": "Uploaded. Staff must check your identity against the institutional record before approving this photograph."}


def provision(db: Session, course: Course, number: str, full_name: str) -> dict:
    number = number.strip().casefold()
    account = db.scalar(select(StudentAccount).where(StudentAccount.institution_id == course.institution_id,
        StudentAccount.student_number == number))
    code = None
    if not account:
        code = generate_setup_code()
        account = StudentAccount(institution_id=course.institution_id, student_number=number,
            full_name=full_name.strip(), setup_hash=hash_token(code), setup_expires_at=utcnow() + timedelta(hours=48))
        db.add(account)
        db.flush()
    link = db.scalar(select(StudentEnrollment).where(StudentEnrollment.course_id == course.id, StudentEnrollment.student_id == account.id))
    if not link:
        db.add(StudentEnrollment(course_id=course.id, student_id=account.id))
    return {"student_id": account.id, "student_number": number, "full_name": account.full_name,
        "setup_code": code, "status": account.status}


@router.post("/api/lecturer/courses/{course_id}/students", status_code=201)
def provision_student(course_id: str, payload: ProvisionStudent, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    course = require_course_access(db, user, course_id, owner_only=True)
    result = provision(db, course, payload.student_number, payload.full_name)
    audit(db, request, "student_enrolled", user=user, resource_type="course", resource_id=course.id)
    db.commit()
    return result


@router.post("/api/lecturer/courses/{course_id}/students/import")
async def import_students(course_id: str, request: Request, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    course = require_course_access(db, user, course_id, owner_only=True)
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise HTTPException(413, "The roster must be smaller than 2 MB. Split larger imports into batches.")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        parsed = [ProvisionStudent(student_number=row.get("student_number", ""), full_name=row.get("full_name", "")) for row in rows]
    except (UnicodeError, ValueError, csv.Error):
        raise HTTPException(422, "Use a UTF-8 CSV with student_number and full_name columns.")
    numbers = [row.student_number.strip().casefold() for row in parsed]
    if not rows or len(numbers) != len(set(numbers)) or len(rows) > 1000:
        raise HTTPException(422, "Use 1–1000 distinct students per import batch.")
    results = [provision(db, course, row.student_number, row.full_name) for row in parsed]
    audit(db, request, "student_roster_imported", user=user, resource_type="course", resource_id=course.id, detail={"count": len(results)})
    db.commit()
    return {"students": results, "message": "Distribute setup codes privately. They expire in 48 hours and are shown only in this response."}


@router.get("/api/lecturer/courses/{course_id}/students")
def list_students(course_id: str, page: int = Query(1, ge=1), search: str = Query("", max_length=100),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_course_access(db, user, course_id, write=True)
    query = select(StudentAccount).join(StudentEnrollment, StudentEnrollment.student_id == StudentAccount.id).where(StudentEnrollment.course_id == course_id)
    if search.strip():
        query = query.where(func.lower(StudentAccount.full_name).contains(search.strip().casefold(), autoescape=True)
            | StudentAccount.student_number.contains(search.strip().casefold(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(StudentAccount.full_name, StudentAccount.id).offset((page - 1) * 100).limit(100)).all()
    result = []
    for account in rows:
        photo = latest_photo(db, account.id)
        result.append({**student_payload(db, account), "photo_id": photo.id if photo else None,
            "photo_url": f"/api/lecturer/courses/{course_id}/students/{account.id}/photo/{photo.id}" if photo else None})
    return {"students": result, "pagination": {"page": page, "total": total, "has_next": page * 100 < total}}


def course_student(db: Session, course_id: str, student_id: str, user: User, owner: bool = False) -> StudentAccount:
    course = require_course_access(db, user, course_id, owner_only=owner, write=True)
    account = db.get(StudentAccount, student_id)
    if not account:
        raise HTTPException(404, "Student not found.")
    enrollment(db, account, course)
    return account


@router.post("/api/lecturer/courses/{course_id}/students/{student_id}/reset")
def reset_student(course_id: str, student_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = course_student(db, course_id, student_id, user, owner=True)
    code = generate_setup_code()
    account.setup_hash = hash_token(code)
    account.setup_expires_at = utcnow() + timedelta(hours=48)
    account.password_hash = None
    account.status = "pending_activation"
    account.failed_count = 0
    account.locked_until = None
    db.execute(delete(StudentSession).where(StudentSession.student_id == account.id))
    audit(db, request, "student_setup_reset", user=user, resource_type="student", resource_id=account.id)
    db.commit()
    return {"setup_code": code, "student_number": account.student_number}


@router.get("/api/lecturer/courses/{course_id}/students/{student_id}/photo/{photo_id}")
def staff_photo(course_id: str, student_id: str, photo_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    course_student(db, course_id, student_id, user)
    photo = db.get(StudentPhoto, photo_id)
    if not photo or photo.student_id != student_id:
        raise HTTPException(404, "Photograph not found.")
    return Response(photo.image_data, media_type=photo.mime_type, headers={"Cache-Control": "no-store"})


@router.post("/api/lecturer/courses/{course_id}/students/{student_id}/photo/{photo_id}/review")
def review_photo(course_id: str, student_id: str, photo_id: str, payload: ReviewIdentity, request: Request,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    course_student(db, course_id, student_id, user)
    photo = db.get(StudentPhoto, photo_id)
    if not photo or photo.student_id != student_id:
        raise HTTPException(404, "Photograph not found.")
    # Versioned photographs are never replaced. Historical attempts retain their reference.
    photo.status = payload.status
    photo.review_note = payload.note.strip()
    photo.reviewer_id = user.id
    photo.reviewed_at = utcnow()
    audit(db, request, "student_photo_reviewed", user=user, resource_type="student_photo", resource_id=photo.id,
        detail={"status": payload.status, "note": payload.note})
    db.commit()
    return {"status": photo.status}


@router.put("/api/lecturer/courses/{course_id}/tasks/{task_id}/distance-settings")
def save_distance_settings(course_id: str, task_id: str, payload: DistanceSettings, request: Request,
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_course_access(db, user, course_id, write=True)
    task = db.get(AssessmentTask, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Task not found.")
    row = db.get(TaskDistanceSettings, task_id)
    if not row:
        row = TaskDistanceSettings(task_id=task_id)
        db.add(row)
    for key, value in payload.model_dump(exclude={"proctor_groups"}).items():
        setattr(row, key, value)
    if payload.proctor_groups and not task.roster_required:
        raise HTTPException(422, "Selected-group proctoring requires an examination eligibility roster with monitor_group labels.")
    row.proctor_groups_json = json.dumps(payload.proctor_groups)
    audit(db, request, "distance_settings_updated", user=user, resource_type="assessment_task", resource_id=task_id, detail=payload.model_dump())
    db.commit()
    return {"settings": distance_settings(db, task_id)}


def oral_questions(db: Session, assessment: Assessment, evidence: AssessmentDistanceEvidence) -> list:
    questions = json.loads(evidence.oral_questions_json)
    if questions:
        return questions
    config = json.loads(evidence.settings_json)
    record = prepare_oral_defence(db, assessment, assessment.document.course, None, config["oral_question_count"])
    questions = json.loads(record.questions_json)
    evidence.oral_questions_json = json.dumps(questions, ensure_ascii=False)
    db.commit()
    return questions


def authorised_student_assessment(db: Session, request: Request, assessment_id: str, authorization: str | None) -> Assessment:
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if evidence and evidence.student_id:
        enforce_assessment_student(db, request, assessment_id)
        assessment = db.get(Assessment, assessment_id)
    else:
        assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    if not assessment:
        raise HTTPException(404, "Assessment not found.")
    return assessment


@router.post("/api/assessments/{assessment_id}/identity-photo", status_code=201)
async def capture_identity(assessment_id: str, request: Request, photo: UploadFile = File(...), consent: bool = Form(False),
                           authorization: str | None = Header(None), db: Session = Depends(get_db)):
    assessment = get_assessment(db, assessment_id, bearer_token(authorization))
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if not evidence or not assessment_features(assessment).get("student_identity_verification"):
        raise HTTPException(409, "Identity capture is not enabled for this attempt.")
    if not consent:
        raise HTTPException(422, "Confirm consent to identity-photo retention.")
    if evidence.start_photo:
        return {"status": evidence.identity_status}
    data, mime = await image_bytes(photo)
    evidence.start_photo = data
    evidence.start_photo_mime = mime
    evidence.identity_status = "pending_review"
    audit(db, request, "assessment_identity_captured", resource_type="assessment", resource_id=assessment_id)
    db.commit()
    return {"status": "pending_review", "message": "Captured for staff comparison. No automated face matching is performed."}


@router.get("/api/assessments/{assessment_id}/identity-status")
def identity_status(assessment_id: str, authorization: str | None = Header(None), db: Session = Depends(get_db)):
    get_assessment(db, assessment_id, bearer_token(authorization))
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    return {"capture_required": bool(evidence and evidence.identity_status == "capture_required")}


@router.get("/api/assessments/{assessment_id}/oral")
def student_oral(assessment_id: str, request: Request, authorization: str | None = Header(None), db: Session = Depends(get_db)):
    assessment = authorised_student_assessment(db, request, assessment_id, authorization)
    if not assessment_features(assessment).get("recorded_oral_responses"):
        return {"enabled": False}
    if assessment.status != "completed":
        raise HTTPException(409, "Complete the MCQ assessment before opening oral verification.")
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if not evidence:
        raise HTTPException(409, "This older attempt was not configured for recorded responses.")
    questions = oral_questions(db, assessment, evidence)
    config = json.loads(evidence.settings_json)
    deadline = _aware(assessment.completed_at) + timedelta(hours=config["oral_window_hours"])
    responses = db.scalars(select(StudentOralResponse).where(StudentOralResponse.assessment_id == assessment.id)).all()
    submitted = {row.question_id for row in responses}
    return {"enabled": True, "settings": config, "deadline": deadline.isoformat(), "closed": utcnow() > deadline,
        "mcq_score": assessment.score, "oral_outcome": assessment.oral_defence.outcome if assessment.oral_defence else None,
        "questions": [{"id": q["id"], "prompt": q["prompt"], "focus": q["focus"], "submitted": q["id"] in submitted} for q in questions]}


@router.post("/api/assessments/{assessment_id}/oral/{question_id}", status_code=201)
async def save_oral(assessment_id: str, question_id: str, request: Request, consent: bool = Form(False),
                   text: str | None = Form(None, max_length=10000), audio: UploadFile | None = File(None),
                   authorization: str | None = Header(None), db: Session = Depends(get_db)):
    assessment = authorised_student_assessment(db, request, assessment_id, authorization)
    payload = student_oral(assessment_id, request, authorization, db)
    if not payload.get("enabled") or question_id not in {q["id"] for q in payload["questions"]}:
        raise HTTPException(404, "Oral question not found.")
    existing = db.scalar(select(StudentOralResponse).where(StudentOralResponse.assessment_id == assessment_id, StudentOralResponse.question_id == question_id))
    if existing:
        # Idempotent retries preserve the original answer and do not create new attempts.
        return {"received": True, "already_received": True}
    if payload["closed"]:
        raise HTTPException(409, "The oral-response window has closed. Contact your lecturer.")
    if not consent:
        raise HTTPException(422, "Confirm consent to retention of this answer for marking.")
    typed = (text or "").strip()
    if bool(audio) == bool(typed):
        raise HTTPException(422, "Submit either a recording or a typed alternative.")
    if typed and not payload["settings"]["typed_alternative"]:
        raise HTTPException(403, "Ask your lecturer to enable an approved typed alternative.")
    data, mime = None, None
    if audio:
        data = await audio.read(10 * 1024 * 1024 + 1)
        mime = (audio.content_type or "").split(";")[0].lower()
        valid = (mime in {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/x-wav"} and
            (data.startswith(b"\x1aE\xdf\xa3") or data.startswith(b"OggS") or data.startswith(b"RIFF") or data[4:8] == b"ftyp"))
        if not valid:
            raise HTTPException(415, "Use a supported browser audio recording.")
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, "The answer exceeds 10 MB. Use a shorter recording or an approved typed alternative.")
    db.add(StudentOralResponse(assessment_id=assessment_id, question_id=question_id,
        audio_data=data, mime_type=mime, typed_text=typed or None))
    audit(db, request, "student_oral_answer_received", resource_type="assessment", resource_id=assessment_id,
        detail={"question_id": question_id, "format": "audio" if audio else "typed"})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"received": True, "already_received": True}
    return {"received": True, "already_received": False}


def staff_assessment(db: Session, user: User, assessment_id: str) -> Assessment:
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        raise HTTPException(404, "Assessment not found.")
    require_document_access(db, user, assessment.document_id, write=True)
    return assessment


@router.get("/api/lecturer/assessments/{assessment_id}/distance-evidence")
def staff_evidence(assessment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    assessment = staff_assessment(db, user, assessment_id)
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if not evidence:
        return {"enabled": False}
    responses = db.scalars(select(StudentOralResponse).where(StudentOralResponse.assessment_id == assessment_id)).all()
    account = db.get(StudentAccount, evidence.student_id) if evidence.student_id else None
    return {"enabled": True, "student": student_payload(db, account) if account else None,
        "identity_status": evidence.identity_status, "identity_note": evidence.identity_note,
        "reference_url": f"/api/lecturer/assessments/{assessment_id}/distance-photo/reference" if evidence.reference_photo_id else None,
        "capture_url": f"/api/lecturer/assessments/{assessment_id}/distance-photo/capture" if evidence.start_photo else None,
        "oral_questions": json.loads(evidence.oral_questions_json),
        "responses": [{"question_id": row.question_id, "typed_text": row.typed_text,
            "received_at": row.received_at.isoformat(),
            "audio_url": f"/api/lecturer/assessments/{assessment_id}/oral-audio/{row.id}" if row.audio_data else None} for row in responses],
        "mcq_score": assessment.score, "disclaimer": "Identity and oral evidence require human judgement. No automated biometric matching is used."}


@router.get("/api/lecturer/assessments/{assessment_id}/distance-photo/{kind}")
def assessment_photo(assessment_id: str, kind: Literal["reference", "capture"], user: User = Depends(current_user), db: Session = Depends(get_db)):
    staff_assessment(db, user, assessment_id)
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    photo = db.get(StudentPhoto, evidence.reference_photo_id) if evidence and evidence.reference_photo_id else None
    data = photo.image_data if kind == "reference" and photo else evidence.start_photo if evidence and kind == "capture" else None
    mime = photo.mime_type if kind == "reference" and photo else evidence.start_photo_mime if evidence else None
    if not data:
        raise HTTPException(404, "Photograph not available.")
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


@router.post("/api/lecturer/assessments/{assessment_id}/identity-review")
def review_assessment_identity(assessment_id: str, payload: ReviewIdentity, request: Request,
                               user: User = Depends(current_user), db: Session = Depends(get_db)):
    staff_assessment(db, user, assessment_id)
    evidence = db.get(AssessmentDistanceEvidence, assessment_id)
    if not evidence or not evidence.start_photo or not evidence.reference_photo_id:
        raise HTTPException(409, "Both the approved reference and examination capture are required for comparison.")
    evidence.identity_status = payload.status
    evidence.identity_note = payload.note.strip()
    evidence.identity_reviewer_id = user.id
    audit(db, request, "assessment_identity_reviewed", user=user, resource_type="assessment", resource_id=assessment_id,
        detail={"status": payload.status, "note": payload.note})
    db.commit()
    return {"status": evidence.identity_status}


@router.get("/api/lecturer/assessments/{assessment_id}/oral-audio/{response_id}")
def staff_oral_audio(assessment_id: str, response_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    staff_assessment(db, user, assessment_id)
    row = db.get(StudentOralResponse, response_id)
    if not row or row.assessment_id != assessment_id or not row.audio_data:
        raise HTTPException(404, "Audio answer not found.")
    return Response(row.audio_data, media_type=row.mime_type, headers={"Cache-Control": "no-store"})
