from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .models import Assessment, AssessmentItem, Document, Question
from .security import make_session_token, verify_token


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


def start_assessment(db: Session, document_id: str) -> tuple[Assessment, str]:
    document = db.scalar(
        select(Document).options(selectinload(Document.questions)).where(Document.id == document_id)
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.status != "ready" or len(document.questions) != 20:
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

    token, token_hash = make_session_token()
    assessment = Assessment(document_id=document.id, token_hash=token_hash)
    db.add(assessment)
    db.flush()

    questions = list(document.questions)
    random.SystemRandom().shuffle(questions)
    for position, question in enumerate(questions, start=1):
        options = json.loads(question.options_json)
        indexed = list(enumerate(options))
        random.SystemRandom().shuffle(indexed)
        shuffled_options = [value for _, value in indexed]
        correct_shuffled_index = next(
            index for index, (original_index, _) in enumerate(indexed) if original_index == question.correct_index
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
    db.commit()
    db.refresh(assessment)
    return assessment, token


def get_assessment(db: Session, assessment_id: str, token: str) -> Assessment:
    assessment = db.scalar(
        select(Assessment)
        .options(selectinload(Assessment.document), selectinload(Assessment.items).selectinload(AssessmentItem.question))
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
    item.response_ms = item.question.time_limit_seconds * 1000


def _complete(db: Session, assessment: Assessment) -> None:
    correct = sum(1 for item in assessment.items if item.is_correct is True)
    score = round((correct / 20) * 100, 1)
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

    while assessment.current_position <= 20:
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
        limit_ms = item.question.time_limit_seconds * 1000
        if elapsed_ms >= limit_ms:
            _mark_timeout(item)
            assessment.current_position += 1
            db.commit()
            continue

        return {
            "status": "in_progress",
            "position": item.position,
            "total": 20,
            "stem": item.question.stem,
            "options": json.loads(item.shuffled_options_json),
            "difficulty": item.question.difficulty,
            "time_limit_seconds": item.question.time_limit_seconds,
            "remaining_ms": max(0, limit_ms - elapsed_ms),
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
    limit_ms = item.question.time_limit_seconds * 1000
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

    if assessment.current_position > 20:
        _complete(db, assessment)
        return {"status": "completed"}
    return {"status": "accepted", "next_position": assessment.current_position}


def result_payload(assessment: Assessment) -> dict[str, object]:
    if assessment.status != "completed":
        raise HTTPException(status_code=409, detail="The assessment is not complete.")
    timed_out = sum(1 for item in assessment.items if item.timed_out)
    answered = sum(1 for item in assessment.items if item.answered_at and not item.timed_out)
    return {
        "assessment_id": assessment.id,
        "student_name": assessment.document.student_name,
        "student_id": assessment.document.student_id,
        "document_title": assessment.document.title,
        "correct_count": assessment.correct_count,
        "question_count": 20,
        "answered_count": answered,
        "timed_out_count": timed_out,
        "score": assessment.score,
        "threshold": settings.pass_threshold,
        "decision": assessment.decision,
        "focus_loss_count": assessment.focus_loss_count,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "disclaimer": (
            "This score measures demonstrated knowledge of the submitted document. "
            "It is not conclusive proof of authorship or academic misconduct."
        ),
    }
