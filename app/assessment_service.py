from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .models import Assessment, AssessmentItem, Course, Document, Question, WebcamSnapshot
from .security import make_session_token, verify_token


DIFFICULTY_WEIGHTS = {
    "recall": 0.30,
    "understanding": 0.40,
    "application": 0.30,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _authorise(assessment: Assessment, token: str) -> None:
    if not verify_token(token, assessment.token_hash):
        raise HTTPException(status_code=401, detail="Invalid assessment session token.")
    started_at = _aware(assessment.started_at)
    if started_at and utcnow() - started_at > timedelta(minutes=settings.session_token_ttl_minutes):
        raise HTTPException(status_code=401, detail="This assessment session has expired.")


def _question_total(assessment: Assessment) -> int:
    if assessment.question_count:
        return int(assessment.question_count)
    return len(assessment.items)


def _difficulty_targets(total: int) -> dict[str, int]:
    raw = {name: total * weight for name, weight in DIFFICULTY_WEIGHTS.items()}
    targets = {name: math.floor(value) for name, value in raw.items()}
    remaining = total - sum(targets.values())
    ranked = sorted(
        raw,
        key=lambda name: (raw[name] - targets[name], DIFFICULTY_WEIGHTS[name]),
        reverse=True,
    )
    for name in ranked[:remaining]:
        targets[name] += 1
    return targets


def _select_questions(questions: list[Question], total: int) -> list[Question]:
    rng = random.SystemRandom()
    grouped: dict[str, list[Question]] = {
        "recall": [],
        "understanding": [],
        "application": [],
    }
    for question in questions:
        grouped.setdefault(question.difficulty, []).append(question)
    for values in grouped.values():
        rng.shuffle(values)

    selected: list[Question] = []
    targets = _difficulty_targets(total)
    for difficulty in ("recall", "understanding", "application"):
        take = min(targets[difficulty], len(grouped.get(difficulty, [])))
        selected.extend(grouped.get(difficulty, [])[:take])
        grouped[difficulty] = grouped.get(difficulty, [])[take:]

    if len(selected) < total:
        remainder = [
            question
            for difficulty in ("recall", "understanding", "application")
            for question in grouped.get(difficulty, [])
        ]
        rng.shuffle(remainder)
        selected.extend(remainder[: total - len(selected)])

    if len(selected) < total:
        raise HTTPException(
            status_code=409,
            detail="The document does not contain enough validated questions for this course setting.",
        )
    rng.shuffle(selected)
    return selected


def start_assessment(db: Session, document_id: str) -> tuple[Assessment, str]:
    document = db.scalar(
        select(Document)
        .options(selectinload(Document.questions))
        .where(Document.id == document_id)
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.status != "ready" or len(document.questions) < 20:
        raise HTTPException(status_code=409, detail="The document is not ready for assessment.")

    attempt_count = db.scalar(
        select(func.count(Assessment.id)).where(Assessment.document_id == document.id)
    ) or 0
    if attempt_count >= settings.max_attempts_per_document:
        raise HTTPException(
            status_code=409,
            detail=(
                "The maximum number of assessment attempts has been reached. "
                "A lecturer must reset the attempt before another can begin."
            ),
        )

    course = db.get(Course, document.course_id) if document.course_id else None
    requested_count = int(getattr(course, "assessment_question_count", 20) or 20)
    question_count = max(5, min(20, requested_count, len(document.questions)))

    token, token_hash = make_session_token()
    assessment = Assessment(
        document_id=document.id,
        course_id=document.course_id,
        lecturer_id=document.submitted_to_lecturer_id,
        token_hash=token_hash,
        question_count=question_count,
    )
    db.add(assessment)
    db.flush()

    questions = _select_questions(list(document.questions), question_count)
    for question in questions:
        question.time_limit_seconds = settings.question_time_seconds
    db.flush()

    rng = random.SystemRandom()
    for position, question in enumerate(questions, start=1):
        options = json.loads(question.options_json)
        indexed = list(enumerate(options))
        rng.shuffle(indexed)
        shuffled_options = [value for _, value in indexed]
        correct_shuffled_index = next(
            index
            for index, (original_index, _) in enumerate(indexed)
            if original_index == question.correct_index
        )
        db.add(
            AssessmentItem(
                assessment_id=assessment.id,
                question_id=question.id,
                position=position,
                shuffled_options_json=json.dumps(shuffled_options, ensure_ascii=False),
                correct_shuffled_index=correct_shuffled_index,
            )
        )

    earliest_capture = 2 if question_count >= 4 else 1
    latest_capture = max(earliest_capture, question_count - 1)
    db.add(
        WebcamSnapshot(
            assessment_id=assessment.id,
            scheduled_position=rng.randint(earliest_capture, latest_capture),
            scheduled_offset_ms=rng.randint(1500, 6500),
            status="pending",
        )
    )
    db.commit()
    db.refresh(assessment)
    return assessment, token


def get_assessment(db: Session, assessment_id: str, token: str) -> Assessment:
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
    _authorise(assessment, token)
    return assessment


def _get_item(assessment: Assessment, position: int) -> AssessmentItem | None:
    return next((item for item in assessment.items if item.position == position), None)


def _time_elapsed_ms(item: AssessmentItem) -> int:
    presented_at = _aware(item.presented_at)
    if not presented_at:
        return 0
    return max(0, int((utcnow() - presented_at).total_seconds() * 1000))


def _mark_timeout(item: AssessmentItem) -> None:
    item.answered_at = utcnow()
    item.selected_index = None
    item.is_correct = False
    item.timed_out = True
    item.response_ms = settings.question_time_seconds * 1000


def _complete(db: Session, assessment: Assessment) -> None:
    total = _question_total(assessment)
    correct = sum(1 for item in assessment.items if item.is_correct is True)
    score = round((correct / total) * 100, 1) if total else 0.0
    assessment.correct_count = correct
    assessment.score = score
    assessment.status = "completed"
    assessment.completed_at = utcnow()
    assessment.decision = (
        "Ownership knowledge demonstrated"
        if score >= settings.pass_threshold
        else "Further verification required"
    )
    db.commit()


def current_question(db: Session, assessment: Assessment) -> dict[str, object]:
    if assessment.status == "completed":
        return {"status": "completed"}

    total = _question_total(assessment)
    while assessment.current_position <= total:
        item = _get_item(assessment, assessment.current_position)
        if not item:
            raise HTTPException(status_code=500, detail="Assessment question sequence is incomplete.")

        if item.answered_at is not None:
            assessment.current_position += 1
            continue

        if item.presented_at is None:
            item.presented_at = utcnow()
            db.commit()
            db.refresh(item)

        elapsed_ms = _time_elapsed_ms(item)
        limit_ms = settings.question_time_seconds * 1000
        if elapsed_ms >= limit_ms:
            _mark_timeout(item)
            assessment.current_position += 1
            db.commit()
            continue

        snapshot = assessment.webcam_snapshot
        capture_requested = bool(
            settings.webcam_required
            and snapshot
            and snapshot.image_data is None
            and snapshot.status == "pending"
            and snapshot.scheduled_position == item.position
        )
        return {
            "status": "in_progress",
            "position": item.position,
            "total": total,
            "stem": item.question.stem,
            "options": json.loads(item.shuffled_options_json),
            "difficulty": item.question.difficulty,
            "time_limit_seconds": settings.question_time_seconds,
            "remaining_ms": max(0, limit_ms - elapsed_ms),
            "capture_requested": capture_requested,
            "capture_after_ms": snapshot.scheduled_offset_ms if capture_requested else None,
        }

    _complete(db, assessment)
    return {"status": "completed"}


def submit_answer(
    db: Session, assessment: Assessment, selected_index: int
) -> dict[str, object]:
    if assessment.status == "completed":
        raise HTTPException(status_code=409, detail="The assessment is already complete.")

    item = _get_item(assessment, assessment.current_position)
    if not item or item.presented_at is None:
        raise HTTPException(status_code=409, detail="No active question is available.")
    if item.answered_at is not None:
        raise HTTPException(status_code=409, detail="This question has already been answered.")

    elapsed_ms = _time_elapsed_ms(item)
    limit_ms = settings.question_time_seconds * 1000
    if elapsed_ms >= limit_ms:
        _mark_timeout(item)
    else:
        item.answered_at = utcnow()
        item.selected_index = selected_index
        item.is_correct = selected_index == item.correct_shuffled_index
        item.timed_out = False
        item.response_ms = elapsed_ms

    assessment.current_position += 1
    db.commit()

    total = _question_total(assessment)
    if assessment.current_position > total:
        _complete(db, assessment)
        return {"status": "completed"}
    return {"status": "accepted", "next_position": assessment.current_position}


def result_payload(assessment: Assessment) -> dict[str, object]:
    if assessment.status != "completed":
        raise HTTPException(status_code=409, detail="The assessment is not complete.")
    timed_out = sum(1 for item in assessment.items if item.timed_out)
    answered = sum(1 for item in assessment.items if item.answered_at and not item.timed_out)
    events = list(getattr(assessment, "monitoring_events", []) or [])
    unresolved = sum(1 for event in events if not event.corrected)
    critical = sum(1 for event in events if event.severity == "critical")
    return {
        "assessment_id": assessment.id,
        "student_name": assessment.document.student_name,
        "student_id": assessment.document.student_id,
        "document_title": assessment.document.title,
        "correct_count": assessment.correct_count,
        "question_count": _question_total(assessment),
        "answered_count": answered,
        "timed_out_count": timed_out,
        "score": assessment.score,
        "threshold": settings.pass_threshold,
        "decision": assessment.decision,
        "focus_loss_count": assessment.focus_loss_count,
        "monitoring_event_count": len(events),
        "monitoring_unresolved_count": unresolved,
        "monitoring_critical_count": critical,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "disclaimer": (
            "This score measures demonstrated knowledge of the submitted document. "
            "Webcam warnings are review indicators only. They are not conclusive proof "
            "of authorship, cheating, or academic misconduct."
        ),
    }
