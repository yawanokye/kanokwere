from __future__ import annotations

import difflib
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import (
    Assessment,
    Course,
    Document,
    MarkerAnomalyAlert,
    MarkAuditLog,
    ModerationRecord,
    OralDefence,
    PracticalTask,
    RubricMark,
    SupervisorMeeting,
    User,
    VersionAnalysis,
)


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")
HEADING_RE = re.compile(
    r"(?im)^(?:chapter|section|part|unit|appendix|abstract|introduction|background|"
    r"literature review|methodology|methods|results|findings|discussion|conclusion|"
    r"recommendations?|references?)\b[^\n]{0,120}$"
)
STOPWORDS = {
    "about", "after", "again", "against", "between", "could", "during", "first",
    "found", "from", "have", "into", "more", "other", "should", "study", "their",
    "there", "these", "they", "this", "those", "through", "using", "were", "which",
    "while", "with", "would", "research", "student", "document", "work", "result",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def course_phase2_settings(course: Course | None) -> dict[str, Any]:
    if not course:
        return {
            "oral_defence_enabled": False,
            "practical_tasks_enabled": False,
            "supervisor_meetings_enabled": False,
            "cross_version_analysis_enabled": False,
            "random_moderation_enabled": False,
            "marker_anomaly_detection_enabled": False,
        }
    return {
        "oral_defence_enabled": bool(course.oral_defence_enabled),
        "oral_defence_question_count": int(course.oral_defence_question_count or 5),
        "practical_tasks_enabled": bool(course.practical_tasks_enabled),
        "practical_task_count": int(course.practical_task_count or 1),
        "supervisor_meetings_enabled": bool(course.supervisor_meetings_enabled),
        "cross_version_analysis_enabled": bool(course.cross_version_analysis_enabled),
        "random_moderation_enabled": bool(course.random_moderation_enabled),
        "moderation_sample_percent": int(course.moderation_sample_percent or 10),
        "moderation_variance_threshold": float(course.moderation_variance_threshold or 10.0),
        "marker_anomaly_detection_enabled": bool(course.marker_anomaly_detection_enabled),
    }


def _oral_question_bank(assessment: Assessment) -> list[dict[str, Any]]:
    document = assessment.document
    evidence_stems = [
        item.question.stem for item in sorted(assessment.items, key=lambda row: row.position)
        if item.question and item.question.category in {
            "decision_defence", "interpretation", "counterfactual", "error_correction", "transfer_application"
        }
    ]
    questions = [
        {
            "id": "oral-1",
            "prompt": f"In your own words, explain the central purpose of ‘{document.title}’ and the problem it addresses.",
            "focus": "understanding",
            "max_score": 5.0,
        },
        {
            "id": "oral-2",
            "prompt": f"Defend an important decision you made in this work. Your submitted justification stated: {_clean(document.key_decisions)[:500]}",
            "focus": "decision_defence",
            "max_score": 5.0,
        },
        {
            "id": "oral-3",
            "prompt": f"Explain the strongest evidence for the main contribution you identified: {_clean(document.main_contribution)[:500]}",
            "focus": "interpretation",
            "max_score": 5.0,
        },
        {
            "id": "oral-4",
            "prompt": "Describe what would need to change if the work were applied to a substantially different population, institution or context.",
            "focus": "counterfactual",
            "max_score": 5.0,
        },
        {
            "id": "oral-5",
            "prompt": f"You identified this limitation: {_clean(document.stated_limitation)[:500]}. Explain its effect and how a later study or improved submission could address it.",
            "focus": "reflection",
            "max_score": 5.0,
        },
        {
            "id": "oral-6",
            "prompt": "Identify one claim in the work that could be misunderstood. State the incorrect interpretation and then correct it using evidence from the document.",
            "focus": "error_correction",
            "max_score": 5.0,
        },
        {
            "id": "oral-7",
            "prompt": "Explain how you verified the accuracy of the sources, data, calculations or AI-assisted material used in the work.",
            "focus": "evidence_integrity",
            "max_score": 5.0,
        },
        {
            "id": "oral-8",
            "prompt": "Apply the main insight of the work to a new practical case and explain what outcome you would expect.",
            "focus": "transfer_application",
            "max_score": 5.0,
        },
        {
            "id": "oral-9",
            "prompt": evidence_stems[0] if evidence_stems else "Which part of the work required the most reasoning, and how did you resolve it?",
            "focus": "document_specific_follow_up",
            "max_score": 5.0,
        },
        {
            "id": "oral-10",
            "prompt": evidence_stems[1] if len(evidence_stems) > 1 else "What is the most important conclusion that should not be drawn from this work, and why?",
            "focus": "document_specific_follow_up",
            "max_score": 5.0,
        },
    ]
    return questions


def prepare_oral_defence(
    db: Session,
    assessment: Assessment,
    course: Course,
    examiner_id: str | None,
    question_count: int | None = None,
) -> OralDefence:
    existing = db.scalar(select(OralDefence).where(OralDefence.assessment_id == assessment.id))
    if existing:
        return existing
    count = max(1, int(question_count or course.oral_defence_question_count or 5))
    bank = _oral_question_bank(assessment)
    items = sorted(assessment.items, key=lambda item: item.position)
    questions = []
    for index in range(count):
        if assessment.document.submission_stage == "exam_source" or index >= len(bank):
            item = items[index % len(items)] if items else None
            excerpt = _clean(item.question.source_quote)[:700] if item else _clean(assessment.document.extracted_text)[:700]
            actions = ["Explain the reasoning behind this passage", "Apply the idea to a different practical case", "State a limitation or assumption and explain its implications", "Describe a plausible incorrect interpretation and correct it"]
            question = {"id": f"oral-{index + 1}", "prompt": f"Oral question {index + 1}. {actions[(index // max(1, len(items))) % len(actions)]}: {excerpt}", "focus": "document_specific_application", "max_score": 5.0}
        else:
            question = dict(bank[index])
        questions.append(question)
    record = OralDefence(
        assessment_id=assessment.id,
        course_id=course.id,
        document_id=assessment.document_id,
        examiner_id=examiner_id,
        status="prepared",
        questions_json=json.dumps(questions, ensure_ascii=False),
        scores_json="[]",
    )
    db.add(record)
    db.flush()
    return record


def oral_defence_payload(record: OralDefence | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "questions": _json(record.questions_json, []),
        "scores": _json(record.scores_json, []),
        "total_score": record.total_score,
        "outcome": record.outcome,
        "notes": record.notes,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


def complete_oral_defence(
    record: OralDefence,
    examiner_id: str,
    scores: list[dict[str, Any]],
    outcome: str,
    notes: str | None,
) -> OralDefence:
    questions = _json(record.questions_json, [])
    question_by_id = {item["id"]: item for item in questions}
    submitted = {str(item["question_id"]): item for item in scores}
    if set(submitted) != set(question_by_id):
        raise ValueError("A score is required for every oral-defence question.")
    scored: list[dict[str, Any]] = []
    earned = 0.0
    maximum = 0.0
    for question_id, question in question_by_id.items():
        row = submitted[question_id]
        max_score = float(question.get("max_score", 5))
        score = float(row.get("score", 0))
        if score < 0 or score > max_score:
            raise ValueError(f"The score for {question_id} must be between 0 and {max_score:g}.")
        earned += score
        maximum += max_score
        scored.append({
            "question_id": question_id,
            "score": round(score, 2),
            "max_score": max_score,
            "comment": _clean(row.get("comment")) or None,
        })
    record.examiner_id = examiner_id
    record.scores_json = json.dumps(scored, ensure_ascii=False)
    record.total_score = round((earned / maximum) * 100, 2) if maximum else 0.0
    record.outcome = outcome
    record.notes = _clean(notes) or None
    record.status = "completed"
    record.completed_at = utcnow()
    return record


def _practical_templates(document: Document) -> list[dict[str, str]]:
    return [
        {
            "task_type": "result_reproduction",
            "prompt": f"Reproduce or reconstruct one important result, calculation, table, figure or analytical step from ‘{document.title}’, then explain each step without referring to prepared notes.",
        },
        {
            "task_type": "error_correction",
            "prompt": "The lecturer will alter one interpretation, calculation or methodological statement from the submitted work. Identify the error, correct it and justify the correction with evidence.",
        },
        {
            "task_type": "application_transfer",
            "prompt": f"Apply the main contribution described by the student to a new institution, population or practical setting: {_clean(document.main_contribution)[:450]}",
        },
        {
            "task_type": "method_reconstruction",
            "prompt": f"Reconstruct the main method or decision process used in the work and explain why the stated key decisions were necessary: {_clean(document.key_decisions)[:450]}",
        },
        {
            "task_type": "evidence_trace",
            "prompt": "Select one major conclusion and trace it backwards to the exact evidence, source, data or reasoning that supports it. Identify any point where the evidence is weaker than the claim.",
        },
    ]


def prepare_practical_tasks(
    db: Session,
    assessment: Assessment,
    course: Course,
    count: int | None = None,
) -> list[PracticalTask]:
    existing = db.scalars(
        select(PracticalTask).where(PracticalTask.assessment_id == assessment.id).order_by(PracticalTask.sequence)
    ).all()
    if existing:
        return list(existing)
    selected_count = max(1, min(5, int(count or course.practical_task_count or 1)))
    records: list[PracticalTask] = []
    for sequence, template in enumerate(_practical_templates(assessment.document)[:selected_count], start=1):
        row = PracticalTask(
            assessment_id=assessment.id,
            course_id=course.id,
            document_id=assessment.document_id,
            sequence=sequence,
            task_type=template["task_type"],
            prompt=template["prompt"],
            max_score=20.0,
            status="prepared",
        )
        db.add(row)
        records.append(row)
    db.flush()
    return records


def practical_task_payload(task: PracticalTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "sequence": task.sequence,
        "task_type": task.task_type,
        "prompt": task.prompt,
        "student_response": task.student_response,
        "score": task.score,
        "max_score": task.max_score,
        "status": task.status,
        "notes": task.notes,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def supervisor_meeting_payload(meeting: SupervisorMeeting) -> dict[str, Any]:
    return {
        "id": meeting.id,
        "document_id": meeting.document_id,
        "student_id": meeting.student_id,
        "meeting_at": meeting.meeting_at.isoformat() if meeting.meeting_at else None,
        "meeting_type": meeting.meeting_type,
        "summary": meeting.summary,
        "decisions": meeting.decisions,
        "action_items": meeting.action_items,
        "next_meeting_at": meeting.next_meeting_at.isoformat() if meeting.next_meeting_at else None,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
    }


def _headings(text: str) -> list[str]:
    matches = [_clean(item) for item in HEADING_RE.findall(text)]
    if matches:
        return matches[:100]
    lines = []
    for line in text.splitlines():
        cleaned = _clean(line)
        words = cleaned.split()
        if 1 < len(words) <= 12 and len(cleaned) <= 110 and cleaned[:1].isupper():
            if cleaned.endswith(":") or sum(word[:1].isupper() for word in words) >= max(2, len(words) - 1):
                lines.append(cleaned.rstrip(":"))
    return lines[:100]


def _keywords(text: str, limit: int = 20) -> list[str]:
    counter = Counter(
        word.casefold() for word in WORD_RE.findall(text)
        if word.casefold() not in STOPWORDS and len(word) >= 5
    )
    return [word for word, _ in counter.most_common(limit)]


def build_cross_version_analysis(document: Document, previous: Document) -> dict[str, Any]:
    old_text = previous.extracted_text or ""
    new_text = document.extracted_text or ""
    old_tokens = old_text.split()[:50000]
    new_tokens = new_text.split()[:50000]
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    inserted = deleted = replaced_old = replaced_new = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            inserted += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "replace":
            replaced_old += i2 - i1
            replaced_new += j2 - j1
    old_headings = _headings(old_text)
    new_headings = _headings(new_text)
    old_heading_set = {item.casefold(): item for item in old_headings}
    new_heading_set = {item.casefold(): item for item in new_headings}
    added_headings = [new_heading_set[key] for key in new_heading_set.keys() - old_heading_set.keys()]
    removed_headings = [old_heading_set[key] for key in old_heading_set.keys() - new_heading_set.keys()]
    old_keywords = _keywords(old_text)
    new_keywords = _keywords(new_text)
    old_refs = len(YEAR_RE.findall(old_text))
    new_refs = len(YEAR_RE.findall(new_text))
    old_count = max(1, len(old_tokens))
    new_count = max(1, len(new_tokens))
    similarity = round(matcher.ratio() * 100, 1)
    insertion_percent = round(((inserted + replaced_new) / new_count) * 100, 1)
    deletion_percent = round(((deleted + replaced_old) / old_count) * 100, 1)
    growth = len(new_tokens) - len(old_tokens)
    flags: list[dict[str, str]] = []
    if similarity < 45:
        flags.append({"type": "low_similarity", "message": "The new version differs substantially from the previous version."})
    if growth >= max(1200, int(old_count * 0.6)):
        flags.append({"type": "rapid_growth", "message": f"The version increased by {growth:,} words."})
    if insertion_percent >= 55:
        flags.append({"type": "large_insertion", "message": f"About {insertion_percent}% of the new version is inserted or replaced text."})
    if len(added_headings) >= 6:
        flags.append({"type": "structural_change", "message": f"{len(added_headings)} new headings or sections were detected."})
    if new_refs - old_refs >= 20:
        flags.append({"type": "reference_surge", "message": f"Reference-year mentions increased by {new_refs - old_refs}."})
    risk_level = "high" if len(flags) >= 2 else "medium" if flags else "low"
    return {
        "previous_document_id": previous.id,
        "current_document_id": document.id,
        "previous_version_number": previous.version_number,
        "current_version_number": document.version_number,
        "word_count_before": len(old_tokens),
        "word_count_after": len(new_tokens),
        "word_count_change": growth,
        "similarity_percent": similarity,
        "inserted_or_replaced_percent": insertion_percent,
        "deleted_or_replaced_percent": deletion_percent,
        "added_headings": added_headings[:20],
        "removed_headings": removed_headings[:20],
        "new_keywords": [item for item in new_keywords if item not in old_keywords][:15],
        "removed_keywords": [item for item in old_keywords if item not in new_keywords][:15],
        "reference_year_mentions_before": old_refs,
        "reference_year_mentions_after": new_refs,
        "flags": flags,
        "risk_level": risk_level,
    }


def ensure_version_analysis(
    db: Session,
    document: Document,
    generated_by: str | None = None,
    *,
    refresh: bool = False,
) -> VersionAnalysis | None:
    if not document.previous_version_id or not document.course_id:
        return None
    existing = db.scalar(select(VersionAnalysis).where(VersionAnalysis.document_id == document.id))
    if existing and not refresh:
        return existing
    previous = db.get(Document, document.previous_version_id)
    if not previous:
        return None
    analysis = build_cross_version_analysis(document, previous)
    if existing:
        existing.previous_document_id = previous.id
        existing.generated_by = generated_by
        existing.risk_level = analysis["risk_level"]
        existing.analysis_json = json.dumps(analysis, ensure_ascii=False)
        existing.created_at = utcnow()
        return existing
    record = VersionAnalysis(
        course_id=document.course_id,
        document_id=document.id,
        previous_document_id=previous.id,
        generated_by=generated_by,
        risk_level=analysis["risk_level"],
        analysis_json=json.dumps(analysis, ensure_ascii=False),
    )
    db.add(record)
    db.flush()
    return record


def version_analysis_payload(record: VersionAnalysis | None) -> dict[str, Any] | None:
    if not record:
        return None
    payload = _json(record.analysis_json, {})
    payload.update({
        "id": record.id,
        "risk_level": record.risk_level,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    })
    return payload


def moderation_payload(record: ModerationRecord | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "selected_reason": record.selected_reason,
        "original_mark": record.original_mark,
        "moderated_mark": record.moderated_mark,
        "variance": record.variance,
        "decision": record.decision,
        "notes": record.notes,
        "selected_at": record.selected_at.isoformat() if record.selected_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


def ensure_random_moderation(
    db: Session,
    assessment: Assessment,
    course: Course,
    original_mark: float,
) -> ModerationRecord | None:
    existing = db.scalar(select(ModerationRecord).where(ModerationRecord.assessment_id == assessment.id))
    if existing:
        return existing
    percent = max(1, min(100, int(course.moderation_sample_percent or 10)))
    bucket = int(hashlib.sha256(assessment.id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket >= percent:
        return None
    record = ModerationRecord(
        assessment_id=assessment.id,
        course_id=course.id,
        status="selected",
        selected_reason=f"Deterministic random {percent}% moderation sample",
        original_mark=float(original_mark),
    )
    db.add(record)
    db.flush()
    return record


def anomaly_payload(alert: MarkerAnomalyAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "course_id": alert.course_id,
        "marker_id": alert.marker_id,
        "anomaly_type": alert.anomaly_type,
        "severity": alert.severity,
        "metric_value": alert.metric_value,
        "threshold_value": alert.threshold_value,
        "details": alert.details,
        "status": alert.status,
        "generated_at": alert.generated_at.isoformat() if alert.generated_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolution_note": alert.resolution_note,
    }


def scan_marker_anomalies(db: Session, course: Course) -> list[MarkerAnomalyAlert]:
    db.execute(delete(MarkerAnomalyAlert).where(MarkerAnomalyAlert.course_id == course.id))
    rows = db.execute(
        select(RubricMark, Assessment)
        .join(Assessment, Assessment.id == RubricMark.assessment_id)
        .where(Assessment.course_id == course.id, RubricMark.graded_by.is_not(None))
    ).all()
    if not rows:
        return []
    all_scores = [float(mark.total_score) for mark, _ in rows]
    course_mean = statistics.mean(all_scores)
    grouped: dict[str, list[tuple[RubricMark, Assessment]]] = defaultdict(list)
    for mark, assessment in rows:
        if mark.graded_by:
            grouped[mark.graded_by].append((mark, assessment))
    alerts: list[MarkerAnomalyAlert] = []
    for marker_id, marker_rows in grouped.items():
        count = len(marker_rows)
        if count < 4:
            continue
        scores = [float(mark.total_score) for mark, _ in marker_rows]
        marker_mean = statistics.mean(scores)
        mean_difference = abs(marker_mean - course_mean)
        marker = db.get(User, marker_id)
        marker_name = marker.full_name if marker else "Unknown marker"
        if mean_difference >= 15:
            alerts.append(MarkerAnomalyAlert(
                course_id=course.id,
                marker_id=marker_id,
                anomaly_type="marker_mean_difference",
                severity="warning",
                metric_value=round(mean_difference, 2),
                threshold_value=15.0,
                details=(
                    f"{marker_name}'s average mark is {marker_mean:.1f}, compared with the course mean "
                    f"of {course_mean:.1f}, across {count} marked assessments."
                ),
            ))
        most_common_count = Counter(round(score, 1) for score in scores).most_common(1)[0][1]
        identical_ratio = most_common_count / count
        if count >= 5 and identical_ratio >= 0.6:
            alerts.append(MarkerAnomalyAlert(
                course_id=course.id,
                marker_id=marker_id,
                anomaly_type="repeated_identical_totals",
                severity="warning",
                metric_value=round(identical_ratio * 100, 1),
                threshold_value=60.0,
                details=f"{marker_name} assigned the same total mark to {most_common_count} of {count} assessments.",
            ))
        assessment_ids = [assessment.id for _, assessment in marker_rows]
        audits = db.scalars(
            select(MarkAuditLog).where(
                MarkAuditLog.assessment_id.in_(assessment_ids),
                MarkAuditLog.previous_total.is_not(None),
            )
        ).all()
        changes = [abs(float(row.new_total) - float(row.previous_total or 0)) for row in audits]
        if len(changes) >= 3 and statistics.mean(changes) >= 10:
            alerts.append(MarkerAnomalyAlert(
                course_id=course.id,
                marker_id=marker_id,
                anomaly_type="large_mark_changes",
                severity="critical",
                metric_value=round(statistics.mean(changes), 2),
                threshold_value=10.0,
                details=f"{marker_name} made {len(changes)} mark changes averaging {statistics.mean(changes):.1f} points.",
            ))
        turnaround_seconds = []
        for mark, assessment in marker_rows:
            if assessment.completed_at and mark.created_at:
                delta = (mark.created_at - assessment.completed_at).total_seconds()
                if delta >= 0:
                    turnaround_seconds.append(delta)
        if len(turnaround_seconds) >= 5 and statistics.median(turnaround_seconds) < 120:
            alerts.append(MarkerAnomalyAlert(
                course_id=course.id,
                marker_id=marker_id,
                anomaly_type="unusually_fast_marking",
                severity="info",
                metric_value=round(statistics.median(turnaround_seconds), 1),
                threshold_value=120.0,
                details=f"{marker_name}'s median time from assessment completion to saved rubric mark was under two minutes.",
            ))
    for alert in alerts:
        db.add(alert)
    db.flush()
    return alerts
