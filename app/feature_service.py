from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Assessment,
    AssessmentTask,
    Course,
    Document,
    FeatureSetting,
    TaskFeatureActivation,
    TaskDistanceSettings,
)


ACTIVATION_MODES = {"disabled", "admin_only", "lecturer_allowed", "always_on"}

# Basic upload, justification, AI declaration, document-grounded questions and
# results deliberately do not appear here: they cannot be disabled.
FEATURE_CATALOG: dict[str, dict[str, str]] = {
    "student_authentication": {
        "label": "Verified student sign-in",
        "description": "Require an activated, institution-rostered student account for this task.",
        "group": "identity",
    },
    "student_identity_verification": {
        "label": "Staff-reviewed photograph verification",
        "description": "Require student sign-in, a staff-approved reference photo and an attempt-start capture. No automatic face matching.",
        "group": "identity",
    },
    "audio_questions": {
        "label": "Read questions aloud",
        "description": "Offer browser speech alongside the full question text. The MCQ timer remains unchanged.",
        "group": "assessment",
    },
    "recorded_oral_responses": {
        "label": "Recorded oral verification after MCQs",
        "description": "Collect question-linked spoken explanations, or permitted typed alternatives, for separate lecturer marking.",
        "group": "assurance",
    },
    "learning_milestones": {
        "label": "Learning milestones",
        "description": "Accept checkpoint submissions linked to lecturer-defined milestones.",
        "group": "learning_process",
    },
    "version_history": {
        "label": "Version history",
        "description": "Link successive submissions and show development over time.",
        "group": "learning_process",
    },
    "adaptive_questions": {
        "label": "Adaptive question sequencing",
        "description": "Adjust the remaining question order from the student's answers.",
        "group": "assessment",
    },
    "webcam_monitoring": {
        "label": "Webcam and continuity monitoring",
        "description": "Require camera verification, event monitoring and secure resume checks.",
        "group": "live_monitoring",
    },
    "secure_browser_enforcement": {
        "label": "Secure browser enforcement",
        "description": "Require top-level fullscreen use and lock the exam on page hide, focus loss or fullscreen exit.",
        "group": "live_monitoring",
    },
    "screen_activity_recording": {
        "label": "Screen activity recording",
        "description": "Record the candidate-shared examination screen in consented chunks for authorised review.",
        "group": "recording",
    },
    "environment_audio_recording": {
        "label": "Environment audio recording",
        "description": "Record consented ambient microphone audio in chunks for authorised review.",
        "group": "recording",
    },
    "live_exam_monitoring": {
        "label": "Live examination dashboard",
        "description": "Show active task sessions and monitoring indicators to authorised staff.",
        "group": "live_monitoring",
    },
    "location_geofence": {
        "label": "Examination venue geofence",
        "description": "Allow examination access only when the candidate device reports an accurate location inside the lecturer-set radius.",
        "group": "live_monitoring",
    },
    "evidence_profile": {
        "label": "Assessment evidence profile",
        "description": "Combine assessment, declaration and monitoring evidence for human review.",
        "group": "assurance",
    },
    "oral_defence": {
        "label": "Oral defence",
        "description": "Generate document-specific viva questions and record examiner judgement.",
        "group": "assurance",
    },
    "practical_tasks": {
        "label": "Practical verification tasks",
        "description": "Ask the student to reproduce, correct or apply part of the work.",
        "group": "assurance",
    },
    "supervisor_meetings": {
        "label": "Supervisor meeting evidence",
        "description": "Record supervision checkpoints and agreed actions.",
        "group": "learning_process",
    },
    "cross_version_analysis": {
        "label": "Cross-version analysis",
        "description": "Compare linked drafts and flag changes that need academic review.",
        "group": "assurance",
    },
    "random_moderation": {
        "label": "Random moderation",
        "description": "Select a configured sample of marked assessments for second review.",
        "group": "quality_assurance",
    },
    "marker_anomaly_detection": {
        "label": "Marker anomaly indicators",
        "description": "Identify unusual marking patterns for contextual human review.",
        "group": "quality_assurance",
    },
}


def ensure_feature_settings(db: Session) -> None:
    existing = set(db.scalars(select(FeatureSetting.feature_key)).all())
    missing = [key for key in FEATURE_CATALOG if key not in existing]
    for key in missing:
        db.add(FeatureSetting(feature_key=key, activation_mode="lecturer_allowed"))
    if missing:
        db.commit()


def feature_settings_by_key(db: Session) -> dict[str, FeatureSetting]:
    ensure_feature_settings(db)
    return {
        item.feature_key: item
        for item in db.scalars(select(FeatureSetting).order_by(FeatureSetting.feature_key)).all()
    }


def feature_catalog_payload(db: Session) -> list[dict[str, object]]:
    configured = feature_settings_by_key(db)
    return [
        {
            "key": key,
            **metadata,
            "activation_mode": configured[key].activation_mode,
        }
        for key, metadata in FEATURE_CATALOG.items()
    ]


def update_feature_setting(db: Session, feature_key: str, activation_mode: str) -> FeatureSetting:
    if feature_key not in FEATURE_CATALOG:
        raise HTTPException(status_code=404, detail="Unknown optional feature.")
    if activation_mode not in ACTIVATION_MODES:
        raise HTTPException(status_code=422, detail="Invalid feature activation mode.")
    configured = feature_settings_by_key(db)
    item = configured[feature_key]
    item.activation_mode = activation_mode
    db.commit()
    db.refresh(item)
    return item


def _activations(task: AssessmentTask) -> dict[str, TaskFeatureActivation]:
    return {item.feature_key: item for item in task.feature_activations}


def effective_task_features(db: Session, task: AssessmentTask) -> dict[str, bool]:
    configured = feature_settings_by_key(db)
    activations = _activations(task)
    result: dict[str, bool] = {}
    for key in FEATURE_CATALOG:
        mode = configured[key].activation_mode
        activation = activations.get(key)
        if mode == "always_on":
            result[key] = True
        elif mode == "disabled":
            result[key] = False
        elif mode == "admin_only":
            result[key] = bool(
                activation and activation.enabled and activation.activated_by_role == "system_admin"
            )
        else:
            result[key] = bool(activation and activation.enabled)
    return result


def set_task_features(
    db: Session,
    task: AssessmentTask,
    requested: dict[str, bool],
    *,
    actor_role: str,
    actor_user_id: str | None,
) -> dict[str, bool]:
    unknown = sorted(set(requested) - set(FEATURE_CATALOG))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown optional feature: {unknown[0]}.")
    if actor_role not in {"lecturer", "system_admin"}:
        raise HTTPException(status_code=403, detail="This role cannot configure task features.")

    configured = feature_settings_by_key(db)
    activations = _activations(task)
    for key, enabled in requested.items():
        mode = configured[key].activation_mode
        if actor_role == "lecturer" and mode in {"disabled", "admin_only", "always_on"}:
            raise HTTPException(
                status_code=403,
                detail=f"{FEATURE_CATALOG[key]['label']} is controlled by the system administrator.",
            )
        activation = activations.get(key)
        if not activation:
            activation = TaskFeatureActivation(task_id=task.id, feature_key=key)
            db.add(activation)
            activations[key] = activation
        activation.enabled = bool(enabled)
        activation.activated_by_role = actor_role
        activation.activated_by_user_id = actor_user_id
    db.commit()
    db.refresh(task)
    return effective_task_features(db, task)


def task_payload(db: Session, task: AssessmentTask) -> dict[str, object]:
    configured = feature_settings_by_key(db)
    activations = _activations(task)
    effective = effective_task_features(db, task)
    distance = db.get(TaskDistanceSettings, task.id)
    features = []
    for key, metadata in FEATURE_CATALOG.items():
        activation = activations.get(key)
        mode = configured[key].activation_mode
        features.append(
            {
                "key": key,
                **metadata,
                "activation_mode": mode,
                "requested": bool(activation and activation.enabled),
                "effective": effective[key],
                "lecturer_can_change": mode == "lecturer_allowed",
                "activated_by_role": activation.activated_by_role if activation else None,
            }
        )
    return {
        "id": task.id,
        "course_id": task.course_id,
        "title": task.title,
        "instructions": task.instructions,
        "mode": task.mode,
        "status": task.status,
        "assessment_question_count": int(task.assessment_question_count or 20),
        "max_attempts": int(task.max_attempts or 1),
        "distance_settings": {
            "oral_question_count": distance.oral_question_count if distance else 3,
            "oral_response_seconds": distance.oral_response_seconds if distance else 120,
            "oral_window_hours": distance.oral_window_hours if distance else 48,
            "typed_alternative": distance.typed_alternative if distance else True,
            "proctor_groups": json.loads(distance.proctor_groups_json) if distance else [],
        },
        "exam_starts_at": task.exam_starts_at.isoformat() if task.exam_starts_at else None,
        "exam_ends_at": task.exam_ends_at.isoformat() if task.exam_ends_at else None,
        "duration_minutes": task.duration_minutes,
        "late_entry_minutes": int(task.late_entry_minutes or 0),
        "roster_required": bool(task.roster_required),
        "geofence_label": task.geofence_label,
        "geofence_radius_meters": task.geofence_radius_meters,
        "geofence_max_accuracy_meters": int(task.geofence_max_accuracy_meters or 100),
        "created_at": task.created_at.isoformat(),
        "effective_features": effective,
        "features": features,
    }


def snapshot_task_features(db: Session, task: AssessmentTask) -> str:
    return json.dumps(effective_task_features(db, task), sort_keys=True)


def _json_features(value: str | None) -> dict[str, bool]:
    try:
        decoded: Any = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {key: bool(item) for key, item in decoded.items() if key in FEATURE_CATALOG}


def legacy_course_features(course: Course | None) -> dict[str, bool]:
    return {
        "learning_milestones": bool(course and course.milestones_enabled),
        "version_history": bool(course and course.version_history_enabled),
        "adaptive_questions": True,
        "webcam_monitoring": bool(settings.webcam_required),
        "secure_browser_enforcement": False,
        "screen_activity_recording": False,
        "environment_audio_recording": False,
        "live_exam_monitoring": bool(course and course.live_exam_monitoring_enabled),
        "location_geofence": False,
        "evidence_profile": True,
        "oral_defence": bool(course and course.oral_defence_enabled),
        "practical_tasks": bool(course and course.practical_tasks_enabled),
        "supervisor_meetings": bool(course and course.supervisor_meetings_enabled),
        "cross_version_analysis": bool(course and course.cross_version_analysis_enabled),
        "random_moderation": bool(course and course.random_moderation_enabled),
        "marker_anomaly_detection": bool(course and course.marker_anomaly_detection_enabled),
    }


def document_features(document: Document, course: Course | None = None) -> dict[str, bool]:
    captured = _json_features(document.features_json)
    if document.task_id is not None:
        return {key: bool(captured.get(key, False)) for key in FEATURE_CATALOG}
    return legacy_course_features(course or document.course)


def assessment_features(assessment: Assessment, course: Course | None = None) -> dict[str, bool]:
    captured = _json_features(assessment.features_json)
    if captured:
        return {key: bool(captured.get(key, False)) for key in FEATURE_CATALOG}
    document = assessment.document
    if document:
        return document_features(document, course or document.course)
    return legacy_course_features(course)


def document_feature_enabled(
    document: Document, feature_key: str, course: Course | None = None
) -> bool:
    return bool(document_features(document, course).get(feature_key, False))


def assessment_feature_enabled(
    assessment: Assessment, feature_key: str, course: Course | None = None
) -> bool:
    return bool(assessment_features(assessment, course).get(feature_key, False))
