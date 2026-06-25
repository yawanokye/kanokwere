from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DB = Path("/tmp/kanokwere-mvp-test.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ALLOW_DEMO_QUESTIONS"] = "true"
os.environ.pop("OPENAI_API_KEY", None)
os.environ["ADMIN_KEY"] = "test-admin-key"

from fastapi.testclient import TestClient

from app.main import app


def sample_document() -> str:
    sentences = []
    for number in range(1, 42):
        sentences.append(
            f"Section {number} explains the distinctive methodology and evidence used by the student to evaluate institutional learning outcomes carefully."
        )
    return " ".join(sentences)


def test_complete_twenty_question_assessment_and_pdf_report():
    with TestClient(app) as client:
        upload = client.post(
            "/api/documents",
            data={
                "student_name": "Test Student",
                "student_id": "TEST/001",
                "title": "A Test of Document Ownership",
            },
            files={"file": ("work.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        document_id = upload.json()["document_id"]

        status = client.get(f"/api/documents/{document_id}/status")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert status.json()["question_count"] == 20
        assert status.json()["generation_mode"] == "demo"

        started = client.post("/api/assessments/start", json={"document_id": document_id})
        assert started.status_code == 200, started.text
        assessment_id = started.json()["assessment_id"]
        token = started.json()["session_token"]
        student_headers = {"Authorization": f"Bearer {token}"}
        admin_headers = {"X-Admin-Key": "test-admin-key"}

        for position in range(1, 21):
            question = client.get(
                f"/api/assessments/{assessment_id}/question", headers=student_headers
            )
            assert question.status_code == 200, question.text
            assert question.json()["position"] == position
            assert "correct_index" not in question.json()

            detail = client.get(
                f"/api/admin/assessments/{assessment_id}", headers=admin_headers
            )
            assert detail.status_code == 200, detail.text
            correct_index = next(
                item["correct_index"]
                for item in detail.json()["questions"]
                if item["position"] == position
            )
            answered = client.post(
                f"/api/assessments/{assessment_id}/answer",
                headers=student_headers,
                json={"selected_index": correct_index},
            )
            assert answered.status_code == 200, answered.text

        result = client.get(
            f"/api/assessments/{assessment_id}/result", headers=student_headers
        )
        assert result.status_code == 200, result.text
        assert result.json()["correct_count"] == 20
        assert result.json()["score"] == 100.0
        assert result.json()["decision"] == "Ownership knowledge demonstrated"

        report = client.get(
            f"/api/admin/assessments/{assessment_id}/report.pdf",
            headers=admin_headers,
        )
        assert report.status_code == 200, report.text
        assert report.headers["content-type"].startswith("application/pdf")
        assert report.content.startswith(b"%PDF")

        blocked_retake = client.post("/api/assessments/start", json={"document_id": document_id})
        assert blocked_retake.status_code == 409

        reset = client.delete(
            f"/api/admin/assessments/{assessment_id}", headers=admin_headers
        )
        assert reset.status_code == 200
        allowed_retake = client.post("/api/assessments/start", json={"document_id": document_id})
        assert allowed_retake.status_code == 200
