from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .models import (
    Assessment,
    AssessmentItem,
    Course,
    Document,
    MonitoringEvent,
    Question,
    WebcamSnapshot,
)
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
    if not started_at:
        return
    expiry = started_at + timedelta(minutes=settings.session_token_ttl_minutes)
    resume_deadline = _aware(assessment.resume_deadline_at)
    if resume_deadline and resume_deadline + timedelta(minutes=5) > expiry:
        expiry = resume_deadline + timedelta(minutes=5)
    if utcnow() > expiry and assessment.status != "completed":
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


def _current_offline_seconds(assessment: Assessment, now: datetime | None = None) -> int:
    started = _aware(assessment.interruption_started_at)
    if not started:
        return 0
    current = now or utcnow()
    return max(0, int((current - started).total_seconds()))


def _unresolved_connection_event(db: Session, assessment_id: str) -> MonitoringEvent | None:
    return db.scalar(
        select(MonitoringEvent)
        .where(
            MonitoringEvent.assessment_id == assessment_id,
            MonitoringEvent.event_type == "connection_interrupted",
            MonitoringEvent.corrected.is_(False),
        )
        .order_by(MonitoringEvent.created_at.desc())
        .limit(1)
    )


def _record_connection_interruption(
    db: Session,
    assessment: Assessment,
    *,
    reason: str,
    started_at: datetime,
) -> None:
    event = _unresolved_connection_event(db, assessment.id)
    message = {
        "offline": "The browser reported that the internet connection was lost.",
        "pagehide": "The assessment page was closed, reloaded, or left.",
        "browser_exit": "The assessment browser session ended unexpectedly.",
        "machine_sleep": "The assessment device stopped sending heartbeats.",
        "camera_failure": "The assessment was interrupted after a camera failure.",
        "network_failure": "The assessment stopped communicating with the server.",
        "heartbeat_timeout": "The server stopped receiving assessment heartbeats.",
    }.get(reason, "The assessment connection was interrupted.")
    if event:
        event.message = message
        event.question_position = assessment.current_position
        return
    db.add(
        MonitoringEvent(
            assessment_id=assessment.id,
            event_type="connection_interrupted",
            severity="critical",
            duration_ms=0,
            question_position=assessment.current_position,
            message=message,
            corrected=False,
            created_at=started_at,
        )
    )


def _resolve_connection_interruption(
    db: Session,
    assessment: Assessment,
    *,
    duration_seconds: int,
    now: datetime,
) -> None:
    event = _unresolved_connection_event(db, assessment.id)
    if event:
        event.corrected = True
        event.resolved_at = now
        event.duration_ms = max(event.duration_ms, duration_seconds * 1000)
    else:
        db.add(
            MonitoringEvent(
                assessment_id=assessment.id,
                event_type="assessment_resumed",
                severity="warning",
                duration_ms=duration_seconds * 1000,
                question_position=assessment.current_position,
                message="The assessment resumed after an interruption.",
                corrected=True,
                resolved_at=now,
            )
        )


def _lock_assessment(
    db: Session,
    assessment: Assessment,
    *,
    reason: str,
    now: datetime | None = None,
) -> None:
    current = now or utcnow()
    if assessment.status == "completed":
        return
    assessment.status = "locked"
    assessment.locked_at = current
    assessment.lock_reason = reason
    assessment.camera_reverification_required = True
    messages = {
        "resume_window_expired": "The permitted resume window expired before the student returned.",
        "too_many_interruptions": "The assessment exceeded the permitted number of interruptions.",
        "offline_limit_exceeded": "The assessment exceeded the permitted total offline time.",
        "different_browser": "A resume attempt was made from a different browser profile.",
        "lecturer_locked": "The assessment was locked for lecturer review.",
    }
    db.add(
        MonitoringEvent(
            assessment_id=assessment.id,
            event_type="assessment_locked",
            severity="critical",
            duration_ms=_current_offline_seconds(assessment, current) * 1000,
            question_position=assessment.current_position,
            message=messages.get(reason, "The assessment was locked for lecturer review."),
            corrected=False,
        )
    )


def _lock_if_limits_exceeded(
    db: Session,
    assessment: Assessment,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or utcnow()
    current_offline = _current_offline_seconds(assessment, current)
    deadline = _aware(assessment.resume_deadline_at)
    if deadline and current > deadline:
        _lock_assessment(db, assessment, reason="resume_window_expired", now=current)
        return True
    lecturer_override = assessment.last_interruption_reason == "lecturer_authorized_resume"
    if not lecturer_override and assessment.interruption_count > settings.max_assessment_interruptions:
        _lock_assessment(db, assessment, reason="too_many_interruptions", now=current)
        return True
    if not lecturer_override and assessment.total_offline_seconds + current_offline > settings.max_assessment_offline_seconds:
        _lock_assessment(db, assessment, reason="offline_limit_exceeded", now=current)
        return True
    return False


def interrupt_assessment(
    db: Session,
    assessment: Assessment,
    *,
    reason: str,
    now: datetime | None = None,
) -> Assessment:
    current = now or utcnow()
    if assessment.status in {"completed", "locked", "interrupted"}:
        return assessment
    assessment.status = "interrupted"
    assessment.interruption_started_at = current
    assessment.interruption_count += 1
    assessment.resume_deadline_at = current + timedelta(
        minutes=settings.assessment_resume_window_minutes
    )
    assessment.last_interruption_reason = reason
    assessment.camera_reverification_required = True
    _record_connection_interruption(
        db, assessment, reason=reason, started_at=current
    )
    _lock_if_limits_exceeded(db, assessment, now=current)
    db.commit()
    return assessment


def refresh_interruption_state(
    db: Session,
    assessment: Assessment,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> Assessment:
    current = now or utcnow()
    changed = False
    if assessment.status == "in_progress":
        last_seen = _aware(assessment.last_seen_at) or _aware(assessment.started_at)
        if last_seen:
            gap_seconds = (current - last_seen).total_seconds()
            if gap_seconds > settings.heartbeat_stale_seconds:
                interrupted_at = last_seen + timedelta(seconds=settings.heartbeat_stale_seconds)
                assessment.status = "interrupted"
                assessment.interruption_started_at = interrupted_at
                assessment.interruption_count += 1
                assessment.resume_deadline_at = interrupted_at + timedelta(
                    minutes=settings.assessment_resume_window_minutes
                )
                assessment.last_interruption_reason = "heartbeat_timeout"
                assessment.camera_reverification_required = True
                _record_connection_interruption(
                    db,
                    assessment,
                    reason="heartbeat_timeout",
                    started_at=interrupted_at,
                )
                changed = True
    if assessment.status == "interrupted":
        if _lock_if_limits_exceeded(db, assessment, now=current):
            changed = True
    if changed and commit:
        db.commit()
    return assessment


def assessment_state_payload(assessment: Assessment) -> dict[str, object]:
    now = utcnow()
    deadline = _aware(assessment.resume_deadline_at)
    remaining = max(0, int((deadline - now).total_seconds())) if deadline else None
    return {
        "assessment_id": assessment.id,
        "status": assessment.status,
        "connection_status": (
            "online"
            if assessment.status == "in_progress"
            else assessment.status
        ),
        "current_position": assessment.current_position,
        "question_count": _question_total(assessment),
        "interruption_count": assessment.interruption_count,
        "total_offline_seconds": assessment.total_offline_seconds,
        "current_offline_seconds": _current_offline_seconds(assessment, now),
        "resume_count": assessment.resume_count,
        "resume_deadline_at": deadline.isoformat() if deadline else None,
        "resume_seconds_remaining": remaining,
        "last_interruption_reason": assessment.last_interruption_reason,
        "camera_reverification_required": assessment.camera_reverification_required,
        "locked_at": assessment.locked_at.isoformat() if assessment.locked_at else None,
        "lock_reason": assessment.lock_reason,
        "interruption_excused": assessment.interruption_excused,
        "interruption_note": assessment.interruption_note,
        "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
        "heartbeat_stale_seconds": settings.heartbeat_stale_seconds,
        "max_interruptions": settings.max_assessment_interruptions,
        "max_offline_seconds": settings.max_assessment_offline_seconds,
    }


def heartbeat_assessment(
    db: Session,
    assessment: Assessment,
    *,
    client_instance_id: str,
    camera_verified: bool,
    reason: str,
) -> dict[str, object]:
    now = utcnow()
    refresh_interruption_state(db, assessment, now=now, commit=False)

    if assessment.client_instance_id and assessment.client_instance_id != client_instance_id:
        _lock_assessment(db, assessment, reason="different_browser", now=now)
        db.add(
            MonitoringEvent(
                assessment_id=assessment.id,
                event_type="resume_device_changed",
                severity="critical",
                duration_ms=0,
                question_position=assessment.current_position,
                message="A resume attempt used a different browser profile.",
                corrected=False,
            )
        )
        db.commit()
        return assessment_state_payload(assessment)

    if not assessment.client_instance_id:
        assessment.client_instance_id = client_instance_id

    if assessment.status == "completed":
        db.commit()
        return assessment_state_payload(assessment)

    if assessment.status == "locked":
        db.commit()
        return assessment_state_payload(assessment)

    if assessment.status == "interrupted":
        if _lock_if_limits_exceeded(db, assessment, now=now):
            db.commit()
            return assessment_state_payload(assessment)
        if not camera_verified:
            assessment.camera_reverification_required = True
            db.commit()
            return assessment_state_payload(assessment)

        offline_seconds = _current_offline_seconds(assessment, now)
        lecturer_override = assessment.last_interruption_reason == "lecturer_authorized_resume"
        if (
            not lecturer_override
            and assessment.total_offline_seconds + offline_seconds > settings.max_assessment_offline_seconds
        ):
            _lock_assessment(db, assessment, reason="offline_limit_exceeded", now=now)
            db.commit()
            return assessment_state_payload(assessment)

        assessment.total_offline_seconds += offline_seconds
        assessment.resume_count += 1
        assessment.last_resumed_at = now
        assessment.status = "in_progress"
        assessment.interruption_started_at = None
        assessment.resume_deadline_at = None
        assessment.locked_at = None
        assessment.lock_reason = None
        assessment.camera_reverification_required = False
        assessment.last_seen_at = now
        _resolve_connection_interruption(
            db,
            assessment,
            duration_seconds=offline_seconds,
            now=now,
        )
        db.commit()
        return assessment_state_payload(assessment)

    assessment.last_seen_at = now
    if camera_verified:
        assessment.camera_reverification_required = False
    db.commit()
    return assessment_state_payload(assessment)


def allow_assessment_resume(db: Session, assessment: Assessment, note: str | None = None) -> Assessment:
    if assessment.status == "completed":
        raise HTTPException(status_code=409, detail="A completed assessment cannot be resumed.")
    now = utcnow()
    assessment.status = "interrupted"
    assessment.interruption_started_at = now
    assessment.resume_deadline_at = now + timedelta(
        minutes=settings.assessment_resume_window_minutes
    )
    assessment.locked_at = None
    assessment.lock_reason = None
    assessment.last_interruption_reason = "lecturer_authorized_resume"
    assessment.camera_reverification_required = True
    if note:
        assessment.interruption_note = note.strip()
    db.commit()
    return assessment


def finish_interrupted_assessment(db: Session, assessment: Assessment) -> Assessment:
    if assessment.status == "completed":
        return assessment
    for item in assessment.items:
        if item.answered_at is None:
            _mark_timeout(item)
    assessment.current_position = _question_total(assessment) + 1
    _complete(db, assessment)
    return assessment


def set_interruption_excused(
    db: Session,
    assessment: Assessment,
    *,
    excused: bool,
    note: str | None,
) -> Assessment:
    assessment.interruption_excused = excused
    assessment.interruption_note = note.strip() if note else None
    db.commit()
    return assessment


def start_assessment(
    db: Session,
    document_id: str,
    client_instance_id: str,
) -> tuple[Assessment, str]:
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
    now = utcnow()
    assessment = Assessment(
        document_id=document.id,
        course_id=document.course_id,
        lecturer_id=document.submitted_to_lecturer_id,
        token_hash=token_hash,
        question_count=question_count,
        last_seen_at=now,
        client_instance_id=client_instance_id,
        camera_reverification_required=False,
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
    assessment.interruption_started_at = None
    assessment.resume_deadline_at = None
    assessment.camera_reverification_required = False
    assessment.decision = (
        "Ownership knowledge demonstrated"
        if score >= settings.pass_threshold
        else "Further verification required"
    )
    db.commit()


def current_question(db: Session, assessment: Assessment) -> dict[str, object]:
    refresh_interruption_state(db, assessment)
    if assessment.status == "completed":
        return {"status": "completed"}
    if assessment.status in {"interrupted", "locked"}:
        return assessment_state_payload(assessment)

    assessment.last_seen_at = utcnow()
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
        db.commit()
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
            "interruption_count": assessment.interruption_count,
            "total_offline_seconds": assessment.total_offline_seconds,
        }

    _complete(db, assessment)
    return {"status": "completed"}


def submit_answer(
    db: Session, assessment: Assessment, selected_index: int
) -> dict[str, object]:
    refresh_interruption_state(db, assessment)
    if assessment.status == "completed":
        raise HTTPException(status_code=409, detail="The assessment is already complete.")
    if assessment.status == "interrupted":
        raise HTTPException(
            status_code=409,
            detail="The assessment connection was interrupted. Reconnect and reverify the camera before continuing.",
        )
    if assessment.status == "locked":
        raise HTTPException(
            status_code=423,
            detail="The assessment is locked for lecturer review.",
        )

    assessment.last_seen_at = utcnow()
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
        "interruption_count": assessment.interruption_count,
        "total_offline_seconds": assessment.total_offline_seconds,
        "resume_count": assessment.resume_count,
        "interruption_excused": assessment.interruption_excused,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "disclaimer": (
            "This score measures demonstrated knowledge of the submitted document. "
            "Monitoring and interruption records are review indicators only. They are not "
            "conclusive proof of authorship, cheating, or academic misconduct."
        ),
    }
