from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    assessments: Mapped[list["Assessment"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
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
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="in_progress")
    current_position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    focus_loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[Document] = relationship(back_populates="assessments")
    items: Mapped[list["AssessmentItem"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="AssessmentItem.position",
    )
    webcam_snapshot: Mapped["WebcamSnapshot | None"] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        uselist=False,
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
