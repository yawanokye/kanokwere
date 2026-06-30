from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DB = Path("/tmp/kanokware-mvp-test.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ALLOW_DEMO_QUESTIONS"] = "true"
os.environ.pop("OPENAI_API_KEY", None)
os.environ["ADMIN_KEY"] = "test-admin-key"
os.environ["QUESTION_TIME_SECONDS"] = "30"
os.environ["LECTURER_REGISTRATION_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app.main import app


ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


def sample_document() -> str:
    sentences = []
    for number in range(1, 42):
        sentences.append(
            f"Section {number} explains the distinctive methodology and evidence used by the student to evaluate institutional learning outcomes carefully."
        )
    return " ".join(sentences)


def create_lecturer(client: TestClient, email: str, full_name: str = "Test Lecturer") -> tuple[dict, str]:
    response = client.post(
        "/api/platform/users",
        headers=ADMIN_HEADERS,
        json={
            "full_name": full_name,
            "email": email,
            "institution_name": "Test University",
            "department": "Research Methods",
            "role": "lecturer",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"], response.json()["setup_code"]


def activate_lecturer(
    client: TestClient,
    email: str,
    setup_code: str,
    password: str = "PrivatePass123",
    recovery_pin: str = "482731",
) -> dict:
    response = client.post(
        "/api/auth/activate",
        json={
            "email": email,
            "setup_code": setup_code,
            "new_password": password,
            "recovery_pin": recovery_pin,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_admin_created_lecturer_activation_course_and_complete_assessment():
    with TestClient(app) as client:
        user, setup_code = create_lecturer(client, "lecturer@test.edu")
        assert user["account_status"] == "pending_activation"
        assert user["activation_required"] is True
        assert "staff_id" not in user

        blocked_login = client.post(
            "/api/auth/login",
            json={"email": "lecturer@test.edu", "password": "PrivatePass123"},
        )
        assert blocked_login.status_code in {401, 403}

        activated = activate_lecturer(client, "lecturer@test.edu", setup_code)
        assert activated["account_status"] == "active"
        assert activated["activation_required"] is False

        course = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 801",
                "title": "Research Methods",
                "academic_year": "2026/2027",
                "semester": "First Semester",
                "assessment_question_count": 10,
            },
        )
        assert course.status_code == 201, course.text
        enrollment_code = course.json()["course"]["enrollment_code"]

        upload = client.post(
            "/api/documents",
            data={
                "student_name": "Test Student",
                "student_id": "TEST/001",
                "title": "A Test of Document Ownership",
                "course_code": enrollment_code,
            },
            files={"file": ("work.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        document_id = upload.json()["document_id"]

        status = client.get(f"/api/documents/{document_id}/status")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert status.json()["question_count"] == 20
        assert status.json()["assessment_question_count"] == 10
        assert status.json()["generation_mode"] == "demo"

        started = client.post("/api/assessments/start", json={"document_id": document_id, "client_instance_id": "test-browser-instance-0001"})
        assert started.status_code == 200, started.text
        assert started.json()["webcam_required"] is True
        assert started.json()["question_count"] == 10
        assessment_id = started.json()["assessment_id"]
        token = started.json()["session_token"]
        student_headers = {"Authorization": f"Bearer {token}"}

        snapshot_sent = False
        tiny_jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
        )

        warning = client.post(
            f"/api/assessments/{assessment_id}/monitoring-event",
            headers=student_headers,
            json={
                "event_type": "no_face",
                "duration_ms": 4200,
                "question_position": 1,
                "severity": "warning",
                "corrected": False,
                "message": "Please position your face clearly inside the camera frame.",
            },
        )
        assert warning.status_code == 200, warning.text
        assert warning.json()["monitoring_event_count"] == 1

        corrected = client.post(
            f"/api/assessments/{assessment_id}/monitoring-event",
            headers=student_headers,
            json={
                "event_type": "no_face",
                "duration_ms": 5200,
                "question_position": 1,
                "severity": "warning",
                "corrected": True,
                "message": "Please position your face clearly inside the camera frame.",
            },
        )
        assert corrected.status_code == 200, corrected.text

        for position in range(1, 11):
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
                snapshot_sent = True

            detail = client.get(
                f"/api/admin/assessments/{assessment_id}", headers=ADMIN_HEADERS
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
        assert result.json()["score"] == 100.0
        assert result.json()["question_count"] == 10
        assert result.json()["monitoring_event_count"] == 1
        assert snapshot_sent is True

        lecturer_submissions = client.get("/api/lecturer/submissions")
        assert lecturer_submissions.status_code == 200, lecturer_submissions.text
        assert lecturer_submissions.json()["submissions"][0]["snapshot_available"] is True
        assert lecturer_submissions.json()["submissions"][0]["monitoring_event_count"] == 1

        report = client.get(f"/api/lecturer/assessments/{assessment_id}/report.pdf")
        assert report.status_code == 200, report.text
        assert report.content.startswith(b"%PDF")


def test_automatic_password_reset_reissue_setup_code_and_delete_account():
    with TestClient(app) as client:
        user, setup_code = create_lecturer(client, "resetme@test.edu", "Reset Lecturer")
        activate_lecturer(
            client,
            "resetme@test.edu",
            setup_code,
            password="OriginalPass123",
            recovery_pin="624819",
        )

        reset = client.post(
            "/api/auth/reset-password",
            json={
                "email": "resetme@test.edu",
                "recovery_pin": "624819",
                "new_password": "ReplacementPass123",
            },
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["user"]["account_status"] == "active"

        client.post("/api/auth/logout")
        login = client.post(
            "/api/auth/login",
            json={"email": "resetme@test.edu", "password": "ReplacementPass123"},
        )
        assert login.status_code == 200, login.text

        reissue = client.post(
            f"/api/platform/users/{user['id']}/reset-password",
            headers=ADMIN_HEADERS,
            json={},
        )
        assert reissue.status_code == 200, reissue.text
        replacement_code = reissue.json()["setup_code"]
        assert reissue.json()["user"]["account_status"] == "pending_activation"

        old_login = client.post(
            "/api/auth/login",
            json={"email": "resetme@test.edu", "password": "ReplacementPass123"},
        )
        assert old_login.status_code in {401, 403}

        reactivated = activate_lecturer(
            client,
            "resetme@test.edu",
            replacement_code,
            password="FreshPrivatePass123",
            recovery_pin="193746",
        )
        assert reactivated["account_status"] == "active"

        deleted = client.delete(
            f"/api/platform/users/{user['id']}", headers=ADMIN_HEADERS
        )
        assert deleted.status_code == 200, deleted.text

        users = client.get("/api/platform/users", headers=ADMIN_HEADERS)
        assert users.status_code == 200
        assert all(item["id"] != user["id"] for item in users.json()["users"])


def test_heartbeat_interruption_resume_lock_and_lecturer_override():
    with TestClient(app) as client:
        _, setup_code = create_lecturer(
            client, "continuity@test.edu", "Continuity Lecturer"
        )
        activate_lecturer(client, "continuity@test.edu", setup_code)

        course = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 902",
                "title": "Assessment Continuity",
                "academic_year": "2026/2027",
                "semester": "Second Semester",
                "assessment_question_count": 5,
            },
        )
        assert course.status_code == 201, course.text
        enrollment_code = course.json()["course"]["enrollment_code"]

        upload = client.post(
            "/api/documents",
            data={
                "student_name": "Continuity Student",
                "student_id": "TEST/CONT/001",
                "title": "Continuity Assessment Document",
                "course_code": enrollment_code,
            },
            files={"file": ("continuity.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        document_id = upload.json()["document_id"]

        started = client.post(
            "/api/assessments/start",
            json={
                "document_id": document_id,
                "client_instance_id": "continuity-browser-0001",
            },
        )
        assert started.status_code == 200, started.text
        assessment_id = started.json()["assessment_id"]
        headers = {"Authorization": f"Bearer {started.json()['session_token']}"}

        initial = client.post(
            f"/api/assessments/{assessment_id}/heartbeat",
            headers=headers,
            json={
                "client_instance_id": "continuity-browser-0001",
                "camera_verified": True,
                "reason": "start",
            },
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["status"] == "in_progress"

        for expected_count in (1, 2):
            interrupted = client.post(
                f"/api/assessments/{assessment_id}/interrupt",
                headers=headers,
                json={
                    "client_instance_id": "continuity-browser-0001",
                    "reason": "offline",
                },
            )
            assert interrupted.status_code == 200, interrupted.text
            assert interrupted.json()["status"] == "interrupted"
            assert interrupted.json()["interruption_count"] == expected_count

            unverified = client.post(
                f"/api/assessments/{assessment_id}/heartbeat",
                headers=headers,
                json={
                    "client_instance_id": "continuity-browser-0001",
                    "camera_verified": False,
                    "reason": "reconnect",
                },
            )
            assert unverified.json()["status"] == "interrupted"
            assert unverified.json()["camera_reverification_required"] is True

            resumed = client.post(
                f"/api/assessments/{assessment_id}/heartbeat",
                headers=headers,
                json={
                    "client_instance_id": "continuity-browser-0001",
                    "camera_verified": True,
                    "reason": "resume",
                },
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] == "in_progress"
            assert resumed.json()["resume_count"] == expected_count

        third = client.post(
            f"/api/assessments/{assessment_id}/interrupt",
            headers=headers,
            json={
                "client_instance_id": "continuity-browser-0001",
                "reason": "offline",
            },
        )
        assert third.status_code == 200, third.text
        assert third.json()["status"] == "locked"
        assert third.json()["lock_reason"] == "too_many_interruptions"

        detail = client.get(f"/api/lecturer/assessments/{assessment_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["summary"]["status"] == "locked"
        assert detail.json()["summary"]["interruption_count"] == 3

        allowed = client.post(
            f"/api/lecturer/assessments/{assessment_id}/allow-resume",
            json={"note": "Verified local power interruption."},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["state"]["status"] == "interrupted"

        resumed_after_override = client.post(
            f"/api/assessments/{assessment_id}/heartbeat",
            headers=headers,
            json={
                "client_instance_id": "continuity-browser-0001",
                "camera_verified": True,
                "reason": "resume",
            },
        )
        assert resumed_after_override.status_code == 200, resumed_after_override.text
        assert resumed_after_override.json()["status"] == "in_progress"
