from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d).+$")


def validate_password(value: str, label: str = "Password") -> str:
    if not PASSWORD_PATTERN.match(value):
        raise ValueError(f"{label} must include uppercase, lowercase, and a number.")
    return value


class GeneratedQuestion(BaseModel):
    stem: str = Field(min_length=12, max_length=600)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3)
    difficulty: Literal["recall", "understanding", "application"]
    category: Literal[
        "document_knowledge",
        "decision_defence",
        "interpretation",
        "counterfactual",
        "error_correction",
        "transfer_application",
    ]
    seconds: int = Field(ge=30, le=30)
    source_quote: str = Field(min_length=8, max_length=500)
    source_location: str = Field(min_length=2, max_length=255)
    explanation: str = Field(min_length=8, max_length=700)

    @field_validator("options")
    @classmethod
    def options_must_be_unique(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if len({item.casefold() for item in cleaned}) != 4:
            raise ValueError("Options must be unique")
        if any(not item for item in cleaned):
            raise ValueError("Options cannot be blank")
        return cleaned


class GeneratedQuestionBank(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1)


class StartAssessmentRequest(BaseModel):
    document_id: str
    client_instance_id: str = Field(min_length=16, max_length=120)


class StartStandaloneExamRequest(BaseModel):
    course_code: str = Field(min_length=6, max_length=24)
    task_id: str
    candidate_name: str = Field(min_length=2, max_length=180)
    candidate_id: str = Field(min_length=2, max_length=100)
    client_instance_id: str = Field(min_length=16, max_length=120)


class AssessmentHeartbeatRequest(BaseModel):
    client_instance_id: str = Field(min_length=16, max_length=120)
    camera_verified: bool = False
    reason: Literal["periodic", "start", "reconnect", "resume", "page_load", "manual_retry"] = "periodic"


class AssessmentInterruptRequest(BaseModel):
    client_instance_id: str = Field(min_length=16, max_length=120)
    reason: Literal[
        "offline",
        "pagehide",
        "browser_exit",
        "machine_sleep",
        "camera_failure",
        "network_failure",
    ] = "browser_exit"


class AssessmentTerminateRequest(BaseModel):
    client_instance_id: str = Field(min_length=16, max_length=120)
    reason: Literal[
        "page_hidden",
        "window_blur",
        "fullscreen_exited",
        "frame_detected",
        "browser_minimized",
    ] = "page_hidden"


class InterruptionNoteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class AnswerRequest(BaseModel):
    selected_index: int = Field(ge=0, le=3)


class FocusEventRequest(BaseModel):
    event: Literal["blur", "hidden"] = "hidden"


class LecturerRegisterRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=180)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    institution_name: str = Field(min_length=3, max_length=240)
    department: str = Field(min_length=2, max_length=180)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return validate_password(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ActivateAccountRequest(BaseModel):
    email: EmailStr
    setup_code: str = Field(min_length=8, max_length=40)
    new_password: str = Field(min_length=10, max_length=128)
    recovery_pin: str = Field(pattern=r"^\d{6}$")

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class SelfServicePasswordResetRequest(BaseModel):
    email: EmailStr
    recovery_pin: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, value: str) -> str:
        return validate_password(value, "New password")


class CourseCreateRequest(BaseModel):
    course_code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=3, max_length=240)
    academic_year: str = Field(min_length=4, max_length=40)
    semester: str = Field(min_length=2, max_length=80)
    assessment_question_count: int = Field(default=20, ge=1)
    max_attempts: int = Field(default=1, ge=1)
    checkpoint_question_count: int = Field(default=3, ge=1)
    milestones_enabled: bool = False
    version_history_enabled: bool = False
    ai_use_policy: Literal[
        "prohibited", "restricted", "permitted_with_disclosure", "required"
    ] = "permitted_with_disclosure"
    ai_policy_guidance: str | None = Field(default=None, max_length=2000)
    live_exam_monitoring_enabled: bool = False
    live_exam_title: str | None = Field(default=None, max_length=240)
    live_exam_instructions: str | None = Field(default=None, max_length=2000)

    # Optional Phase 2 modules.
    oral_defence_enabled: bool = False
    oral_defence_question_count: int = Field(default=5, ge=1)
    practical_tasks_enabled: bool = False
    practical_task_count: int = Field(default=1, ge=1, le=5)
    supervisor_meetings_enabled: bool = False
    cross_version_analysis_enabled: bool = False
    random_moderation_enabled: bool = False
    moderation_sample_percent: int = Field(default=10, ge=1, le=100)
    moderation_variance_threshold: float = Field(default=10.0, ge=1, le=50)
    marker_anomaly_detection_enabled: bool = False

    milestone_titles: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("milestone_titles")
    @classmethod
    def clean_milestone_titles(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len({item.casefold() for item in cleaned}) != len(cleaned):
            raise ValueError("Milestone titles must be unique.")
        if any(len(item) > 180 for item in cleaned):
            raise ValueError("Each milestone title must be 180 characters or fewer.")
        return cleaned


class CourseSettingsRequest(BaseModel):
    assessment_question_count: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)
    checkpoint_question_count: int | None = Field(default=None, ge=1)
    milestones_enabled: bool | None = None
    version_history_enabled: bool | None = None
    ai_use_policy: Literal[
        "prohibited", "restricted", "permitted_with_disclosure", "required"
    ] | None = None
    ai_policy_guidance: str | None = Field(default=None, max_length=2000)
    live_exam_monitoring_enabled: bool | None = None
    live_exam_title: str | None = Field(default=None, max_length=240)
    live_exam_instructions: str | None = Field(default=None, max_length=2000)
    oral_defence_enabled: bool | None = None
    oral_defence_question_count: int | None = Field(default=None, ge=1)
    practical_tasks_enabled: bool | None = None
    practical_task_count: int | None = Field(default=None, ge=1, le=5)
    supervisor_meetings_enabled: bool | None = None
    cross_version_analysis_enabled: bool | None = None
    random_moderation_enabled: bool | None = None
    moderation_sample_percent: int | None = Field(default=None, ge=1, le=100)
    moderation_variance_threshold: float | None = Field(default=None, ge=1, le=50)
    marker_anomaly_detection_enabled: bool | None = None


class FeatureSettingUpdateRequest(BaseModel):
    activation_mode: Literal["disabled", "admin_only", "lecturer_allowed", "always_on"]


class AssessmentTaskCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    instructions: str | None = Field(default=None, max_length=4000)
    mode: Literal["submission_assurance", "standalone_exam"] = "submission_assurance"
    assessment_question_count: int = Field(default=20, ge=1)
    max_attempts: int = Field(default=1, ge=1)
    features: dict[str, bool] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def limit_features(cls, value: dict[str, bool]) -> dict[str, bool]:
        if len(value) > 30:
            raise ValueError("Too many optional feature selections.")
        return value


class AssessmentTaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=240)
    instructions: str | None = Field(default=None, max_length=4000)
    mode: Literal["submission_assurance", "standalone_exam"] | None = None
    assessment_question_count: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)
    status: Literal["draft", "active", "closed"] | None = None
    features: dict[str, bool] | None = None

    @field_validator("features")
    @classmethod
    def limit_optional_features(cls, value: dict[str, bool] | None) -> dict[str, bool] | None:
        if value is not None and len(value) > 30:
            raise ValueError("Too many optional feature selections.")
        return value


class TaskFeatureUpdateRequest(BaseModel):
    features: dict[str, bool]

    @field_validator("features")
    @classmethod
    def require_feature_selection(cls, value: dict[str, bool]) -> dict[str, bool]:
        if not value:
            raise ValueError("Select at least one feature to update.")
        if len(value) > 30:
            raise ValueError("Too many optional feature selections.")
        return value


class CourseMilestoneCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=1000)
    required: bool = True
    checkpoint_question_count: int = Field(default=3, ge=1)


class RubricCriterionRequest(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    label: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    max_score: float = Field(gt=0, le=100)


class CourseRubricRequest(BaseModel):
    criteria: list[RubricCriterionRequest] = Field(min_length=2, max_length=10)

    @field_validator("criteria")
    @classmethod
    def validate_rubric(cls, value: list[RubricCriterionRequest]) -> list[RubricCriterionRequest]:
        if len({item.key for item in value}) != len(value):
            raise ValueError("Rubric criterion keys must be unique.")
        total = round(sum(item.max_score for item in value), 2)
        if total != 100:
            raise ValueError("Rubric maximum scores must total 100.")
        return value


class RubricScoreRequest(BaseModel):
    criterion_key: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    score: float = Field(ge=0, le=100)
    comment: str | None = Field(default=None, max_length=1000)


class RubricMarkRequest(BaseModel):
    scores: list[RubricScoreRequest] = Field(min_length=2, max_length=10)
    overall_comment: str | None = Field(default=None, max_length=2000)
    change_reason: str | None = Field(default=None, max_length=1000)


class LearningAlertActionRequest(BaseModel):
    status: Literal["acknowledged", "resolved"]
    note: str | None = Field(default=None, max_length=1000)


class MonitoringEventRequest(BaseModel):
    event_type: Literal[
        "no_face",
        "multiple_faces",
        "looking_away",
        "excessive_movement",
        "low_light",
        "camera_interrupted",
        "camera_covered",
        "camera_frozen",
        "monitoring_unavailable",
        "window_blur",
        "tab_hidden",
        "connection_interrupted",
        "assessment_resumed",
        "resume_device_changed",
        "assessment_locked",
    ]
    duration_ms: int = Field(default=0, ge=0, le=300000)
    question_position: int | None = Field(default=None, ge=1, le=50)
    severity: Literal["warning", "critical"] = "warning"
    corrected: bool = False
    message: str | None = Field(default=None, max_length=300)


class OralDefenceStartRequest(BaseModel):
    question_count: int | None = Field(default=None, ge=1)


class OralDefenceQuestionScoreRequest(BaseModel):
    question_id: str = Field(min_length=3, max_length=80)
    score: float = Field(ge=0, le=10)
    comment: str | None = Field(default=None, max_length=1000)


class OralDefenceCompleteRequest(BaseModel):
    scores: list[OralDefenceQuestionScoreRequest] = Field(min_length=3, max_length=10)
    outcome: Literal["satisfactory", "satisfactory_with_conditions", "further_verification", "unsatisfactory"]
    notes: str | None = Field(default=None, max_length=3000)


class PracticalTaskGenerateRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=5)


class PracticalTaskScoreRequest(BaseModel):
    student_response: str = Field(min_length=20, max_length=6000)
    score: float = Field(ge=0, le=100)
    notes: str | None = Field(default=None, max_length=2000)


class SupervisorMeetingRequest(BaseModel):
    student_id: str = Field(min_length=2, max_length=100)
    document_id: str | None = None
    meeting_at: datetime
    meeting_type: Literal["supervision", "progress_review", "oral_checkpoint", "remedial", "other"] = "supervision"
    summary: str = Field(min_length=20, max_length=5000)
    decisions: str | None = Field(default=None, max_length=3000)
    action_items: str | None = Field(default=None, max_length=3000)
    next_meeting_at: datetime | None = None


class ModerationCompleteRequest(BaseModel):
    moderated_mark: float = Field(ge=0, le=100)
    decision: Literal["confirmed", "adjusted", "return_to_marker", "escalated"]
    notes: str = Field(min_length=10, max_length=3000)


class MarkerAnomalyActionRequest(BaseModel):
    status: Literal["acknowledged", "resolved"]
    note: str | None = Field(default=None, max_length=2000)


class AssuranceDecisionRequest(BaseModel):
    decision: Literal[
        "evidence_consistent",
        "additional_viva",
        "supplementary_task",
        "academic_integrity_review",
        "unable_to_determine",
    ]
    note: str = Field(min_length=10, max_length=3000)


class CourseCollaboratorRequest(BaseModel):
    email: EmailStr
    access_level: Literal["co_lecturer", "viewer"] = "co_lecturer"


class UserApprovalRequest(BaseModel):
    role: Literal["lecturer", "institution_admin"] = "lecturer"


class UserSuspensionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PasswordResetRequestCreate(BaseModel):
    email: EmailStr


class AdminUserCreateRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=180)
    email: EmailStr
    institution_name: str = Field(min_length=3, max_length=240)
    department: str = Field(min_length=2, max_length=180)
    role: Literal["lecturer", "institution_admin"] = "lecturer"


class AdminPasswordResetRequest(BaseModel):
    pass


class AdminUserStatusRequest(BaseModel):
    status: Literal["active", "suspended"]
