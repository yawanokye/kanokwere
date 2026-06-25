from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from .assessment_service import (
    current_question,
    get_assessment,
    result_payload,
    start_assessment,
    submit_answer,
)
from .config import BASE_DIR, settings
from .database import Base, SessionLocal, engine, get_db
from .document_service import read_and_extract
from .models import Assessment, AssessmentItem, Document, Question
from .question_service import generate_question_bank, question_to_record
from .report_service import build_pdf_report
from .schemas import AnswerRequest, FocusEventRequest, StartAssessmentRequest
from .security import bearer_token, require_admin


logger = logging.getLogger("kanokwere.generation")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Background tasks are not durable across service restarts. Mark any
    # interrupted generation clearly so the browser does not poll forever.
    with SessionLocal() as db:
        interrupted = db.scalars(
            select(Document).where(Document.status.in_(["queued", "generating"]))
        ).all()
        for document in interrupted:
            document.status = "failed"
            document.processing_error = (
                "Question generation was interrupted by a service restart. "
                "Use Retry generation or upload the document again."
            )
        if interrupted:
            db.commit()
    yield


app = FastAPI(title="Kanokwere", version="0.1.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "app": settings.app_name}


def _generate_questions_job(document_id: str) -> None:
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return
        logger.info("Question generation started for document %s", document_id)
        document.status = "generating"
        document.processing_error = None
        db.commit()

        bank, mode = generate_question_bank(document.extracted_text, document.title)
        db.execute(delete(Question).where(Question.document_id == document.id))
        for generated in bank.questions:
            db.add(Question(document_id=document.id, **question_to_record(generated)))
        document.generation_mode = mode
        document.status = "ready"
        db.commit()
        logger.info("Question generation completed for document %s", document_id)
    except Exception as exc:
        logger.exception("Question generation failed for document %s", document_id)
        db.rollback()
        document = db.get(Document, document_id)
        if document:
            document.status = "failed"
            detail = getattr(exc, "detail", None)
            document.processing_error = str(detail or exc)[:1500]
            db.commit()
    finally:
        db.close()


@app.post("/api/documents", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    student_name: str = Form(..., min_length=2, max_length=180),
    student_id: str = Form(..., min_length=2, max_length=100),
    title: str = Form(..., min_length=3, max_length=300),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _, extracted_text, filename, digest = await read_and_extract(file)
    document = Document(
        student_name=student_name.strip(),
        student_id=student_id.strip(),
        title=title.strip(),
        original_filename=filename,
        file_hash=digest,
        extracted_text=extracted_text,
        word_count=len(extracted_text.split()),
        status="queued",
        generation_mode="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    background_tasks.add_task(_generate_questions_job, document.id)
    return {
        "document_id": document.id,
        "status": document.status,
        "word_count": document.word_count,
        "message": "The document was accepted and question generation has started.",
    }


@app.get("/api/documents/{document_id}/status")
def document_status(document_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    created_at = document.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed_seconds = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
    stale_seconds = max(1, settings.generation_stale_minutes) * 60
    if document.status in {"queued", "generating"} and elapsed_seconds >= stale_seconds:
        document.status = "failed"
        document.processing_error = (
            f"Question generation exceeded {settings.generation_stale_minutes} minutes and was stopped. "
            "Retry generation. If it happens again, reduce MAX_CONTEXT_CHARS or check OpenAI billing and logs."
        )
        db.commit()

    question_count = db.scalar(
        select(func.count()).select_from(Question).where(Question.document_id == document.id)
    )
    return {
        "document_id": document.id,
        "status": document.status,
        "question_count": int(question_count or 0),
        "generation_mode": document.generation_mode,
        "error": document.processing_error,
        "elapsed_seconds": elapsed_seconds,
        "stale_after_seconds": stale_seconds,
    }


@app.post("/api/documents/{document_id}/retry", status_code=202)
def retry_document_generation(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.status not in {"failed"}:
        raise HTTPException(
            status_code=409,
            detail="Only a failed or timed-out generation can be retried.",
        )

    db.execute(delete(Question).where(Question.document_id == document.id))
    document.status = "queued"
    document.generation_mode = "pending"
    document.processing_error = None
    document.created_at = datetime.now(timezone.utc)
    db.commit()
    background_tasks.add_task(_generate_questions_job, document.id)
    return {
        "document_id": document.id,
        "status": document.status,
        "message": "Question generation restarted.",
    }


@app.post("/api/assessments/start")
def begin_assessment(
    payload: StartAssessmentRequest, db: Session = Depends(get_db)
) -> dict[str, object]:
    assessment, token = start_assessment(db, payload.document_id)
    return {
        "assessment_id": assessment.id,
        "session_token": token,
        "question_count": 20,
        "pass_threshold": settings.pass_threshold,
        "instructions": (
            f"Questions appear one at a time. Each question allows {settings.question_time_seconds} seconds. "
            "You cannot return to an earlier question."
        ),
    }


@app.get("/api/assessments/{assessment_id}/question")
def assessment_question(
    assessment_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    token = bearer_token(authorization)
    assessment = get_assessment(db, assessment_id, token)
    return current_question(db, assessment)


@app.post("/api/assessments/{assessment_id}/answer")
def assessment_answer(
    assessment_id: str,
    payload: AnswerRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    token = bearer_token(authorization)
    assessment = get_assessment(db, assessment_id, token)
    return submit_answer(db, assessment, payload.selected_index)


@app.post("/api/assessments/{assessment_id}/focus-event")
def focus_event(
    assessment_id: str,
    payload: FocusEventRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    token = bearer_token(authorization)
    assessment = get_assessment(db, assessment_id, token)
    if assessment.status == "in_progress":
        assessment.focus_loss_count += 1
        db.commit()
    return {"recorded": True, "count": assessment.focus_loss_count}


@app.get("/api/assessments/{assessment_id}/result")
def assessment_result(
    assessment_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    token = bearer_token(authorization)
    assessment = get_assessment(db, assessment_id, token)
    return result_payload(assessment)


@app.get("/api/admin/submissions", dependencies=[Depends(require_admin)])
def admin_submissions(db: Session = Depends(get_db)) -> dict[str, object]:
    documents = db.scalars(
        select(Document).order_by(Document.created_at.desc()).limit(100)
    ).all()
    rows = []
    for document in documents:
        latest = db.scalar(
            select(Assessment)
            .where(Assessment.document_id == document.id)
            .order_by(Assessment.started_at.desc())
            .limit(1)
        )
        rows.append(
            {
                "document_id": document.id,
                "student_name": document.student_name,
                "student_id": document.student_id,
                "title": document.title,
                "status": document.status,
                "generation_mode": document.generation_mode,
                "word_count": document.word_count,
                "created_at": document.created_at.isoformat(),
                "assessment_id": latest.id if latest else None,
                "assessment_status": latest.status if latest else None,
                "score": latest.score if latest else None,
                "decision": latest.decision if latest else None,
            }
        )
    return {"submissions": rows}


@app.get("/api/admin/assessments/{assessment_id}", dependencies=[Depends(require_admin)])
def admin_assessment_detail(
    assessment_id: str, db: Session = Depends(get_db)
) -> dict[str, object]:
    assessment = db.scalar(
        select(Assessment)
        .options(
            selectinload(Assessment.document),
            selectinload(Assessment.items).selectinload(AssessmentItem.question),
        )
        .where(Assessment.id == assessment_id)
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    details = []
    for item in sorted(assessment.items, key=lambda value: value.position):
        options = json.loads(item.shuffled_options_json)
        details.append(
            {
                "position": item.position,
                "stem": item.question.stem,
                "options": options,
                "selected_index": item.selected_index,
                "correct_index": item.correct_shuffled_index,
                "is_correct": item.is_correct,
                "timed_out": item.timed_out,
                "response_ms": item.response_ms,
                "difficulty": item.question.difficulty,
                "time_limit_seconds": settings.question_time_seconds,
                "source_location": item.question.source_location,
                "source_quote": item.question.source_quote,
                "explanation": item.question.explanation,
            }
        )
    summary = result_payload(assessment) if assessment.status == "completed" else {
        "assessment_id": assessment.id,
        "status": assessment.status,
        "student_name": assessment.document.student_name,
        "student_id": assessment.document.student_id,
        "document_title": assessment.document.title,
    }
    return {"summary": summary, "questions": details}


@app.get("/api/admin/assessments/{assessment_id}/report.pdf", dependencies=[Depends(require_admin)])
def admin_pdf_report(
    assessment_id: str, db: Session = Depends(get_db)
) -> Response:
    assessment = db.scalar(
        select(Assessment)
        .options(
            selectinload(Assessment.document),
            selectinload(Assessment.items).selectinload(AssessmentItem.question),
        )
        .where(Assessment.id == assessment_id)
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if assessment.status != "completed":
        raise HTTPException(status_code=409, detail="The assessment is not complete.")
    pdf = build_pdf_report(assessment)
    safe_id = assessment.document.student_id.replace("/", "-").replace("\\", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="kanokwere-{safe_id}.pdf"'},
    )


@app.delete("/api/admin/assessments/{assessment_id}", dependencies=[Depends(require_admin)])
def admin_reset_assessment(assessment_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    assessment = db.get(Assessment, assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    db.delete(assessment)
    db.commit()
    return {"deleted": True, "retake_enabled": True}


@app.delete("/api/admin/documents/{document_id}", dependencies=[Depends(require_admin)])
def admin_delete_document(document_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.delete(document)
    db.commit()
    return {"deleted": True}
