from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(String(180), nullable=True, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="institution")
    courses: Mapped[list["Course"]] = relationship(back_populates="institution")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(180), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="lecturer")
    department: Mapped[str | None] = mapped_column(String(180), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    account_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    setup_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    institution: Mapped[Institution] = relationship(back_populates="users")
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    course_links: Mapped[list["CourseLecturer"]] = relationship(
        back_populates="lecturer", cascade="all, delete-orphan"
    )
    password_reset_requests: Mapped[list["PasswordResetRequest"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class PasswordResetRequest(Base):
    __tablename__ = "password_reset_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="password_reset_requests")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint(
            "institution_id", "course_code", "academic_year", "semester",
            name="uq_course_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    course_code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(40), nullable=False)
    semester: Mapped[str] = mapped_column(String(80), nullable=False)
    enrollment_code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    assessment_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    checkpoint_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    milestones_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version_history_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    final_justification_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ai_declaration_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ai_use_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="permitted_with_disclosure"
    )
    ai_policy_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_exam_monitoring_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    live_exam_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    live_exam_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Phase 2 modules are independently optional at course level.
    oral_defence_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oral_defence_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    practical_tasks_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    practical_task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supervisor_meetings_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cross_version_analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    random_moderation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    moderation_sample_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    moderation_variance_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    marker_anomaly_detection_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    institution: Mapped[Institution] = relationship(back_populates="courses")
    lecturer_links: Mapped[list["CourseLecturer"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(back_populates="course")
    milestones: Mapped[list["CourseMilestone"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="CourseMilestone.sequence"
    )
    supervisor_meetings: Mapped[list["SupervisorMeeting"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="SupervisorMeeting.meeting_at"
    )


class CourseMilestone(Base):
    __tablename__ = "course_milestones"
    __table_args__ = (UniqueConstraint("course_id", "sequence", name="uq_course_milestone_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checkpoint_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="milestones")
    documents: Mapped[list["Document"]] = relationship(back_populates="milestone")


class CourseLecturer(Base):
    __tablename__ = "course_lecturers"
    __table_args__ = (
        UniqueConstraint("course_id", "lecturer_id", name="uq_course_lecturer"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lecturer_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_level: Mapped[str] = mapped_column(String(30), nullable=False, default="co_lecturer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="lecturer_links")
    lecturer: Mapped[User] = relationship(back_populates="course_links")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    student_name: Mapped[str] = mapped_column(String(180), nullable=False)
    student_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="processing")
    generation_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="ai")
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_id: Mapped[str | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    submitted_to_lecturer_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    milestone_id: Mapped[str | None] = mapped_column(
        ForeignKey("course_milestones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    previous_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    submission_stage: Mapped[str] = mapped_column(String(30), nullable=False, default="final")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assessment_question_count_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    understanding_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_decisions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    main_contribution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stated_limitation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_use_level: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    ai_use_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course | None] = relationship(back_populates="documents")
    milestone: Mapped[CourseMilestone | None] = relationship(back_populates="documents")
    previous_version: Mapped["Document | None"] = relationship(remote_side="Document.id", uselist=False)
    questions: Mapped[list["Question"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    learning_alerts: Mapped[list["LearningAlert"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    version_analysis: Mapped["VersionAnalysis | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False,
        foreign_keys="VersionAnalysis.document_id",
    )
    supervisor_meetings: Mapped[list["SupervisorMeeting"]] = relationship(
        back_populates="document", foreign_keys="SupervisorMeeting.document_id"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    correct_index: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="document_knowledge")
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_location: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="questions")
    assessment_items: Mapped[list["AssessmentItem"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lecturer_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="in_progress")
    current_position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assurance_decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    assurance_decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assurance_decided_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assurance_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    focus_loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    # Connection continuity and secure-resume state. Heartbeats are the source
    # of truth when the browser cannot report an abrupt exit or power loss.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interruption_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interruption_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_offline_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resume_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_interruption_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    interruption_excused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interruption_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_instance_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_reverification_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    document: Mapped[Document] = relationship(back_populates="assessments")
    items: Mapped[list["AssessmentItem"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="AssessmentItem.position",
    )
    webcam_snapshot: Mapped["WebcamSnapshot | None"] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", uselist=False
    )
    monitoring_events: Mapped[list["MonitoringEvent"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="MonitoringEvent.created_at",
    )
    rubric_mark: Mapped["RubricMark | None"] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", uselist=False
    )
    mark_audits: Mapped[list["MarkAuditLog"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="MarkAuditLog.created_at"
    )
    oral_defence: Mapped["OralDefence | None"] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", uselist=False
    )
    practical_tasks: Mapped[list["PracticalTask"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="PracticalTask.sequence"
    )
    moderation_record: Mapped["ModerationRecord | None"] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", uselist=False
    )


class AssessmentItem(Base):
    __tablename__ = "assessment_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    shuffled_options_json: Mapped[str] = mapped_column(Text, nullable=False)
    correct_shuffled_index: Mapped[int] = mapped_column(Integer, nullable=False)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adaptive_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="items")
    question: Mapped[Question] = relationship(back_populates="assessment_items")


class WebcamSnapshot(Base):
    __tablename__ = "webcam_snapshots"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_webcam_snapshot_assessment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_position: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capture_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="webcam_snapshot")


class MonitoringEvent(Base):
    __tablename__ = "monitoring_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="monitoring_events")


class LearningAlert(Base):
    __tablename__ = "learning_alerts"
    __table_args__ = (UniqueConstraint("document_id", "alert_key", name="uq_learning_alert_document_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    student_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    alert_key: Mapped[str] = mapped_column(String(120), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship(back_populates="learning_alerts")


class RubricMark(Base):
    __tablename__ = "rubric_marks"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_rubric_mark_assessment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    graded_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criteria_json: Mapped[str] = mapped_column(Text, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    overall_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="rubric_mark")


class MarkAuditLog(Base):
    __tablename__ = "mark_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    previous_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_total: Mapped[float] = mapped_column(Float, nullable=False)
    previous_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessment: Mapped[Assessment] = relationship(back_populates="mark_audits")


class OralDefence(Base):
    __tablename__ = "oral_defences"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_oral_defence_assessment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    examiner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="prepared")
    questions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    scores_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="oral_defence")


class PracticalTask(Base):
    __tablename__ = "practical_tasks"
    __table_args__ = (
        UniqueConstraint("assessment_id", "sequence", name="uq_practical_task_assessment_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    student_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="prepared")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="practical_tasks")


class SupervisorMeeting(Base):
    __tablename__ = "supervisor_meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    student_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    recorded_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    meeting_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meeting_type: Mapped[str] = mapped_column(String(40), nullable=False, default="supervision")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    decisions: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_items: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="supervisor_meetings")
    document: Mapped[Document | None] = relationship(
        back_populates="supervisor_meetings", foreign_keys=[document_id]
    )


class VersionAnalysis(Base):
    __tablename__ = "version_analyses"
    __table_args__ = (UniqueConstraint("document_id", name="uq_version_analysis_document"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(
        back_populates="version_analysis", foreign_keys=[document_id]
    )


class ModerationRecord(Base):
    __tablename__ = "moderation_records"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_moderation_record_assessment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    moderator_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="selected")
    selected_reason: Mapped[str] = mapped_column(String(180), nullable=False)
    original_mark: Mapped[float] = mapped_column(Float, nullable=False)
    moderated_mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    variance: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assessment: Mapped[Assessment] = relationship(back_populates="moderation_record")


class MarkerAnomalyAlert(Base):
    __tablename__ = "marker_anomaly_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marker_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anomaly_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)



class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
