from __future__ import annotations

import difflib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .assurance_service import ai_policy_alignment
from .config import settings
from .feature_service import document_feature_enabled
from .models import Assessment, Course, Document, LearningAlert, MonitoringEvent


DEFAULT_RUBRIC: list[dict[str, Any]] = [
    {
        "key": "understanding_defence",
        "label": "Understanding and defence",
        "description": "Explains the central ideas, decisions and claims in the submitted work.",
        "max_score": 30.0,
    },
    {
        "key": "reasoning_method",
        "label": "Methodological or analytical reasoning",
        "description": "Justifies the approach, method, evidence or analytical choices used.",
        "max_score": 25.0,
    },
    {
        "key": "application_transfer",
        "label": "Application and transfer",
        "description": "Applies the work to a new case, identifies implications and responds to counterfactual questions.",
        "max_score": 20.0,
    },
    {
        "key": "accuracy_evidence",
        "label": "Accuracy and use of evidence",
        "description": "Uses the document accurately and distinguishes supported claims from unsupported claims.",
        "max_score": 15.0,
    },
    {
        "key": "reflection_ai_integrity",
        "label": "Reflection and AI-use integrity",
        "description": "Identifies limitations, explains personal contribution and declares AI assistance responsibly.",
        "max_score": 10.0,
    },
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_rubric_json() -> str:
    return json.dumps(DEFAULT_RUBRIC, ensure_ascii=False)


def parse_rubric(course: Course) -> list[dict[str, Any]]:
    try:
        value = json.loads(course.rubric_json or "[]")
    except (TypeError, json.JSONDecodeError):
        value = []
    if not isinstance(value, list) or not value:
        return [dict(item) for item in DEFAULT_RUBRIC]
    return value


def _normalise_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


def version_comparison(document: Document, previous: Document | None) -> dict[str, Any] | None:
    if not previous:
        return None
    old_text = previous.extracted_text or ""
    new_text = document.extracted_text or ""
    old_words = len(old_text.split())
    new_words = len(new_text.split())
    ratio = difflib.SequenceMatcher(
        None,
        _normalise_text(old_text)[:120000],
        _normalise_text(new_text)[:120000],
    ).ratio()
    return {
        "previous_document_id": previous.id,
        "previous_version_number": previous.version_number,
        "word_count_before": old_words,
        "word_count_after": new_words,
        "word_count_change": new_words - old_words,
        "similarity_percent": round(ratio * 100, 1),
        "created_at_before": previous.created_at.isoformat() if previous.created_at else None,
        "created_at_after": document.created_at.isoformat() if document.created_at else None,
    }


def _upsert_alert(
    db: Session,
    document: Document,
    *,
    alert_key: str,
    alert_type: str,
    severity: str,
    message: str,
    assessment_id: str | None = None,
) -> LearningAlert:
    alert = db.scalar(
        select(LearningAlert).where(
            LearningAlert.document_id == document.id,
            LearningAlert.alert_key == alert_key,
        )
    )
    if alert:
        alert.alert_type = alert_type
        alert.severity = severity
        alert.message = message
        alert.assessment_id = assessment_id or alert.assessment_id
        if alert.status == "resolved":
            alert.status = "open"
            alert.resolved_at = None
        return alert
    alert = LearningAlert(
        course_id=document.course_id,
        document_id=document.id,
        assessment_id=assessment_id,
        student_id=document.student_id,
        alert_key=alert_key,
        alert_type=alert_type,
        severity=severity,
        message=message,
        status="open",
    )
    db.add(alert)
    return alert


def refresh_document_alerts(db: Session, document: Document) -> None:
    course = db.get(Course, document.course_id) if document.course_id else None
    previous = db.get(Document, document.previous_version_id) if document.previous_version_id else None

    if course and document_feature_enabled(document, "learning_milestones", course) and document.submission_stage == "final":
        prior_milestones = db.scalar(
            select(func.count(Document.id)).where(
                Document.course_id == document.course_id,
                Document.student_id == document.student_id,
                Document.submission_stage == "milestone",
                Document.created_at < document.created_at,
            )
        ) or 0
        if prior_milestones == 0:
            _upsert_alert(
                db,
                document,
                alert_key="final_without_recorded_milestones",
                alert_type="process_gap",
                severity="warning",
                message=(
                    "A final submission was received without an earlier Kanokware milestone record. "
                    "The lecturer may verify the learning process manually."
                ),
            )

    if previous and document.word_count >= max(1200, int(previous.word_count * 1.75)):
        _upsert_alert(
            db,
            document,
            alert_key="large_version_change",
            alert_type="version_change",
            severity="warning",
            message=(
                f"The document increased from {previous.word_count:,} to {document.word_count:,} words "
                "between consecutive versions. Review the student's explanation of the changes."
            ),
        )

    if document.ai_use_level in {"substantial", "required"}:
        _upsert_alert(
            db,
            document,
            alert_key="declared_high_ai_use",
            alert_type="ai_declaration",
            severity="info",
            message=(
                "The student declared substantial or required AI assistance. Review the declaration "
                "together with the mastery assessment and justification."
            ),
        )

    if course:
        policy_alignment = ai_policy_alignment(course, document)
        if policy_alignment["status"] != "aligned":
            _upsert_alert(
                db,
                document,
                alert_key="ai_policy_alignment_review",
                alert_type="ai_policy",
                severity="warning",
                message=policy_alignment["reason"],
            )


def refresh_assessment_alerts(db: Session, assessment: Assessment) -> None:
    document = assessment.document or db.get(Document, assessment.document_id)
    if not document:
        return
    if assessment.score is not None and assessment.score < settings.pass_threshold:
        _upsert_alert(
            db,
            document,
            alert_key=f"mastery_below_threshold_{assessment.id}",
            alert_type="mastery_gap",
            severity="critical",
            message=(
                f"The student obtained {assessment.score:.1f}% in the understanding assessment, "
                f"below the {settings.pass_threshold}% threshold. Further verification is required."
            ),
            assessment_id=assessment.id,
        )
    critical_count = db.scalar(
        select(func.count(MonitoringEvent.id)).where(
            MonitoringEvent.assessment_id == assessment.id,
            MonitoringEvent.severity == "critical",
        )
    ) or 0
    if critical_count:
        _upsert_alert(
            db,
            document,
            alert_key=f"critical_monitoring_{assessment.id}",
            alert_type="monitoring",
            severity="warning",
            message=f"The assessment recorded {critical_count} critical monitoring event(s) for lecturer review.",
            assessment_id=assessment.id,
        )
    if assessment.interruption_count >= 2 or assessment.status == "locked":
        _upsert_alert(
            db,
            document,
            alert_key=f"assessment_interruptions_{assessment.id}",
            alert_type="continuity",
            severity="warning",
            message=(
                f"The assessment recorded {assessment.interruption_count} interruption(s) and "
                f"{assessment.total_offline_seconds} seconds offline."
            ),
            assessment_id=assessment.id,
        )


def alert_payload(alert: LearningAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "message": alert.message,
        "status": alert.status,
        "resolution_note": alert.resolution_note,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


def course_learning_progress(db: Session, course: Course) -> list[dict[str, Any]]:
    documents = db.scalars(
        select(Document)
        .where(Document.course_id == course.id)
        .order_by(Document.student_id, Document.created_at)
    ).all()
    grouped: dict[str, list[Document]] = defaultdict(list)
    for document in documents:
        grouped[document.student_id].append(document)

    rows: list[dict[str, Any]] = []
    milestone_total = (
        len(course.milestones)
        if any(document_feature_enabled(item, "learning_milestones", course) for item in documents)
        else 0
    )
    for student_id, items in grouped.items():
        latest = items[-1]
        milestone_ids = {item.milestone_id for item in items if item.milestone_id}
        finals = [item for item in items if item.submission_stage == "final"]
        assessments = db.scalars(
            select(Assessment)
            .where(Assessment.document_id.in_([item.id for item in items]))
            .order_by(Assessment.completed_at.desc())
        ).all()
        completed = [item for item in assessments if item.status == "completed"]
        latest_score = completed[0].score if completed else None
        open_alert_count = db.scalar(
            select(func.count(LearningAlert.id)).where(
                LearningAlert.course_id == course.id,
                LearningAlert.student_id == student_id,
                LearningAlert.status != "resolved",
            )
        ) or 0
        rows.append({
            "student_id": student_id,
            "student_name": latest.student_name,
            "document_count": len(items),
            "version_count": max((item.version_number for item in items), default=1),
            "milestones_completed": len(milestone_ids),
            "milestones_total": milestone_total,
            "final_submitted": bool(finals),
            "latest_score": latest_score,
            "latest_ai_use_level": latest.ai_use_level,
            "open_alert_count": int(open_alert_count),
            "last_activity_at": latest.created_at.isoformat() if latest.created_at else None,
        })
    return sorted(rows, key=lambda row: (row["open_alert_count"] == 0, row["student_name"].casefold()))
