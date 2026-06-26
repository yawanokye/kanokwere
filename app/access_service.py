from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditLog, Course, CourseLecturer, Document, User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def email_domain(value: str) -> str:
    return normalize_email(value).split("@", 1)[-1]


def generate_enrollment_code(db: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "KANO-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.scalar(select(Course.id).where(Course.enrollment_code == code)):
            return code
    raise HTTPException(status_code=500, detail="Could not generate a unique course code.")


def course_ids_for_user(db: Session, user: User) -> list[str]:
    if user.role == "institution_admin":
        return list(db.scalars(select(Course.id).where(Course.institution_id == user.institution_id)).all())
    return list(
        db.scalars(
            select(CourseLecturer.course_id).where(CourseLecturer.lecturer_id == user.id)
        ).all()
    )


def require_course_access(
    db: Session,
    user: User,
    course_id: str,
    *,
    owner_only: bool = False,
    write: bool = False,
) -> Course:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found.")
    if user.role == "institution_admin" and course.institution_id == user.institution_id:
        return course
    link = db.scalar(
        select(CourseLecturer).where(
            CourseLecturer.course_id == course_id,
            CourseLecturer.lecturer_id == user.id,
        )
    )
    if not link:
        raise HTTPException(status_code=403, detail="You do not have access to this course.")
    if owner_only and link.access_level != "owner":
        raise HTTPException(status_code=403, detail="Only the course owner can perform this action.")
    if write and link.access_level == "viewer":
        raise HTTPException(status_code=403, detail="Your course access is view-only.")
    return course


def require_document_access(
    db: Session, user: User, document_id: str, *, write: bool = False
) -> Document:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not document.course_id:
        raise HTTPException(status_code=403, detail="This legacy submission is available only to the platform administrator.")
    require_course_access(db, user, document.course_id, write=write)
    return document


def audit(
    db: Session,
    request: Request | None,
    action: str,
    *,
    user: User | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | str | None = None,
) -> None:
    if isinstance(detail, dict):
        detail_value = json.dumps(detail, ensure_ascii=False)
    else:
        detail_value = detail
    ip = request.client.host if request and request.client else None
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail_value,
            ip_address=ip,
        )
    )
