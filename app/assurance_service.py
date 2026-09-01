from __future__ import annotations

from typing import Any

from .config import settings
from .models import Assessment, Course, Document, OralDefence, PracticalTask


AI_POLICY_LABELS = {
    "prohibited": "AI use prohibited",
    "restricted": "Restricted AI support",
    "permitted_with_disclosure": "AI permitted with disclosure",
    "required": "AI use required",
}


def ai_policy_payload(course: Course | None) -> dict[str, Any]:
    policy = (getattr(course, "ai_use_policy", None) or "permitted_with_disclosure").strip()
    if policy not in AI_POLICY_LABELS:
        policy = "permitted_with_disclosure"
    return {
        "level": policy,
        "label": AI_POLICY_LABELS[policy],
        "guidance": (getattr(course, "ai_policy_guidance", None) or "").strip() or None,
    }


def ai_policy_alignment(course: Course | None, document: Document) -> dict[str, Any]:
    policy = ai_policy_payload(course)
    declared = (document.ai_use_level or "none").strip().lower()
    details_present = bool((document.ai_use_details or "").strip())

    if policy["level"] == "prohibited":
        aligned = declared == "none"
        reason = (
            "The declaration is consistent with the course rule."
            if aligned
            else "The student declared AI use in a course where AI use is prohibited. Human review is required."
        )
    elif policy["level"] == "restricted":
        aligned = declared in {"none", "limited"}
        reason = (
            "The declaration is within the restricted-use boundary."
            if aligned
            else "The declared level exceeds the course's restricted-use boundary. Human review is required."
        )
    elif policy["level"] == "required":
        aligned = declared == "required" and details_present
        reason = (
            "The student declared the required AI use and supplied an explanation."
            if aligned
            else "The course requires documented AI use, but the declaration does not yet provide that evidence."
        )
    else:
        aligned = declared == "none" or details_present
        reason = (
            "The declaration meets the disclosure requirement."
            if aligned
            else "AI use was declared without the required explanation of use and checking."
        )

    return {
        "status": "aligned" if aligned else "needs_review",
        "declared_level": declared,
        "policy_level": policy["level"],
        "policy_label": policy["label"],
        "reason": reason,
    }


def _practical_score(tasks: list[PracticalTask]) -> tuple[float | None, str]:
    completed = [
        task for task in tasks
        if task.status == "completed" and task.score is not None and float(task.max_score or 0) > 0
    ]
    if not tasks:
        return None, "not_configured"
    if len(completed) != len(tasks):
        return None, "pending"
    earned = sum(float(task.score or 0) for task in completed)
    possible = sum(float(task.max_score or 0) for task in completed)
    return (round((earned / possible) * 100, 1) if possible else None), "completed"


def assessment_evidence_profile(
    assessment: Assessment,
    course: Course | None,
    *,
    oral_defence: OralDefence | None = None,
    practical_tasks: list[PracticalTask] | None = None,
) -> dict[str, Any]:
    document = assessment.document
    tasks = practical_tasks if practical_tasks is not None else list(assessment.practical_tasks or [])
    oral = oral_defence if oral_defence is not None else assessment.oral_defence
    policy = ai_policy_alignment(course, document)

    knowledge_status = "completed" if assessment.status == "completed" else "pending"
    knowledge_score = float(assessment.score) if assessment.score is not None else None

    if course and course.oral_defence_enabled:
        oral_status = oral.status if oral else "pending"
        oral_score = float(oral.total_score) if oral and oral.total_score is not None else None
    else:
        oral_status = "not_configured"
        oral_score = None

    if course and course.practical_tasks_enabled:
        practical_score, practical_status = _practical_score(tasks)
    else:
        practical_score, practical_status = None, "not_configured"

    events = list(assessment.monitoring_events or [])
    unresolved = [event for event in events if not event.corrected]
    critical = [event for event in unresolved if event.severity == "critical"]
    if critical:
        session_status = "human_review_required"
        session_message = f"{len(critical)} unresolved critical session indicator(s) require contextual review."
    elif unresolved:
        session_status = "attention"
        session_message = f"{len(unresolved)} unresolved session indicator(s) require contextual review."
    else:
        session_status = "no_unresolved_indicators"
        session_message = "No unresolved session indicators are recorded."

    recommendation = "evidence_incomplete"
    recommendation_label = "Complete the remaining assessment evidence"
    reasons: list[str] = []

    if assessment.status == "completed" and knowledge_score is not None:
        if knowledge_score < settings.pass_threshold:
            recommendation = "additional_verification"
            recommendation_label = "Additional verification required"
            reasons.append("The document-specific understanding score is below the course threshold.")
        else:
            recommendation = "evidence_consistent"
            recommendation_label = "Available learning evidence is consistent"

    if oral_status in {"prepared", "pending"} or practical_status == "pending":
        recommendation = "evidence_incomplete"
        recommendation_label = "Complete the remaining assessment evidence"
        reasons.append("An enabled oral or practical verification component is not complete.")
    if oral and oral.status == "completed" and (
        (oral.total_score is not None and oral.total_score < 60)
        or oral.outcome in {"further_verification", "unsatisfactory"}
    ):
        recommendation = "additional_verification"
        recommendation_label = "Additional verification required"
        reasons.append("The oral-defence evidence requires further verification.")
    if practical_status == "completed" and practical_score is not None and practical_score < 60:
        recommendation = "additional_verification"
        recommendation_label = "Additional verification required"
        reasons.append("The practical-application evidence requires further verification.")
    if policy["status"] != "aligned":
        recommendation = "human_review"
        recommendation_label = "Human review of AI-policy alignment required"
        reasons.append(policy["reason"])
    if session_status == "human_review_required":
        recommendation = "human_review"
        recommendation_label = "Human review of session evidence required"
        reasons.append(session_message)

    if not reasons and recommendation == "evidence_consistent":
        reasons.append(
            "The completed evidence meets the configured threshold and no unresolved critical indicator is recorded."
        )
    elif not reasons:
        reasons.append("The assessment evidence is still being assembled.")

    return {
        "document_knowledge": {"status": knowledge_status, "score": knowledge_score},
        "independent_reasoning": {"status": oral_status, "score": oral_score},
        "application_transfer": {"status": practical_status, "score": practical_score},
        "ai_policy_alignment": policy,
        "session_evidence": {
            "status": session_status,
            "unresolved_count": len(unresolved),
            "critical_count": len(critical),
            "message": session_message,
        },
        "recommendation": {
            "code": recommendation,
            "label": recommendation_label,
            "reasons": reasons,
            "disclaimer": (
                "This profile supports academic judgement. It does not prove authorship, cheating, "
                "or academic misconduct and must be interpreted through human review and due process."
            ),
        },
        "lecturer_decision": {
            "code": assessment.assurance_decision,
            "note": assessment.assurance_decision_note,
            "decided_at": (
                assessment.assurance_decided_at.isoformat()
                if assessment.assurance_decided_at else None
            ),
        },
    }
