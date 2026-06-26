from __future__ import annotations

import base64
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
os.environ["QUESTION_TIME_SECONDS"] = "30"

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
        assert started.json()["webcam_required"] is True
        assessment_id = started.json()["assessment_id"]
        token = started.json()["session_token"]
        student_headers = {"Authorization": f"Bearer {token}"}
        admin_headers = {"X-Admin-Key": "test-admin-key"}

        snapshot_sent = False
        tiny_jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
        )

        for position in range(1, 21):
            question = client.get(
                f"/api/assessments/{assessment_id}/question", headers=student_headers
            )
            assert question.status_code == 200, question.text
            assert question.json()["position"] == position
            assert question.json()["time_limit_seconds"] == 30
            assert "correct_index" not in question.json()

            if question.json().get("capture_requested") and not snapshot_sent:
                snapshot = client.post(
                    f"/api/assessments/{assessment_id}/snapshot",
                    headers=student_headers,
                    data={"capture_reason": "random"},
                    files={"image": ("webcam.jpg", tiny_jpeg, "image/jpeg")},
                )
                assert snapshot.status_code == 200, snapshot.text
                assert snapshot.json()["captured"] is True
                snapshot_sent = True

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
        assert snapshot_sent is True

        submissions = client.get("/api/admin/submissions", headers=admin_headers)
        assert submissions.status_code == 200, submissions.text
        row = submissions.json()["submissions"][0]
        assert row["snapshot_available"] is True

        snapshot_image = client.get(
            f"/api/admin/assessments/{assessment_id}/snapshot",
            headers=admin_headers,
        )
        assert snapshot_image.status_code == 200, snapshot_image.text
        assert snapshot_image.headers["content-type"].startswith("image/jpeg")
        assert snapshot_image.content == tiny_jpeg

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
