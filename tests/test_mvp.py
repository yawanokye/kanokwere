from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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




def understanding_fields(**overrides: str) -> dict[str, str]:
    values = {
        "understanding_summary": (
            "This work evaluates institutional learning outcomes through a structured method, "
            "and the student can explain how the evidence supports the conclusions reached."
        ),
        "key_decisions": (
            "The approach was selected because it connects the evidence to the stated learning "
            "outcomes and permits consistent interpretation of the findings."
        ),
        "main_contribution": (
            "The work contributes a structured assessment of institutional learning outcomes and "
            "shows how the evidence can guide academic decisions."
        ),
        "stated_limitation": (
            "The evidence is limited by the scope and institutional context of the material used "
            "for the assessment."
        ),
        "ai_use_level": "none",
        "ai_use_details": "",
    }
    values.update(overrides)
    return values

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


def test_homepage_has_clear_coursework_and_standalone_exam_entry_paths():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200, response.text
        html = response.text
        assert 'data-student-path="coursework"' in html
        assert 'data-student-path="exam"' in html
        assert 'id="exam-candidate-id"' in html
        assert 'id="exam-access-pin"' in html
        assert "Students cannot add themselves to a secured exam roster" in html


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
                **understanding_fields(),
            },
            files={"file": ("work.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        document_id = upload.json()["document_id"]

        status = client.get(f"/api/documents/{document_id}/status")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert status.json()["question_count"] == 10
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


def test_phase3_ai_policy_evidence_profile_and_human_decision():
    with TestClient(app) as client:
        _, setup_code = create_lecturer(
            client, "assurance@test.edu", "Assurance Lecturer"
        )
        activate_lecturer(client, "assurance@test.edu", setup_code)

        course_response = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 910",
                "title": "Responsible AI Assessment",
                "academic_year": "2026/2027",
                "semester": "First Semester",
                "assessment_question_count": 5,
                "ai_use_policy": "prohibited",
                "ai_policy_guidance": "Students must complete this task without generative AI.",
                "live_exam_monitoring_enabled": True,
                "live_exam_title": "Responsible AI Live Examination",
                "live_exam_instructions": "Keep the camera active and contact the lecturer after a disconnection.",
            },
        )
        assert course_response.status_code == 201, course_response.text
        course = course_response.json()["course"]
        assert course["ai_policy"]["level"] == "prohibited"

        resolved = client.get(
            "/api/courses/resolve", params={"code": course["enrollment_code"]}
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["ai_policy"]["label"] == "AI use prohibited"

        upload = client.post(
            "/api/documents",
            data={
                "student_name": "Policy Review Student",
                "student_id": "TEST/AI/001",
                "title": "AI Policy Alignment Review",
                "course_code": course["enrollment_code"],
                **understanding_fields(
                    ai_use_level="limited",
                    ai_use_details=(
                        "AI was used to organise early ideas. Every claim was checked against "
                        "the submitted document before the final version was prepared."
                    ),
                ),
            },
            files={"file": ("policy.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        document_id = upload.json()["document_id"]

        started = client.post(
            "/api/assessments/start",
            json={
                "document_id": document_id,
                "client_instance_id": "phase3-browser-instance-0001",
            },
        )
        assert started.status_code == 200, started.text
        assessment_id = started.json()["assessment_id"]

        live_monitor = client.get(
            f"/api/lecturer/courses/{course['id']}/live-exam-monitor"
        )
        assert live_monitor.status_code == 200, live_monitor.text
        assert live_monitor.json()["exam"]["title"] == "Responsible AI Live Examination"
        assert live_monitor.json()["sessions"][0]["assessment_id"] == assessment_id
        assert "critical_event_count" in live_monitor.json()["sessions"][0]

        detail = client.get(f"/api/lecturer/assessments/{assessment_id}")
        assert detail.status_code == 200, detail.text
        profile = detail.json()["evidence_profile"]
        assert profile["ai_policy_alignment"]["status"] == "needs_review"
        assert profile["recommendation"]["code"] == "human_review"
        assert any(
            alert["alert_type"] == "ai_policy"
            for alert in detail.json()["learning_alerts"]
        )

        decision = client.post(
            f"/api/lecturer/assessments/{assessment_id}/assurance-decision",
            json={
                "decision": "additional_viva",
                "note": "The AI declaration conflicts with the course rule, so a short viva is required.",
            },
        )
        assert decision.status_code == 200, decision.text
        saved = decision.json()["evidence_profile"]["lecturer_decision"]
        assert saved["code"] == "additional_viva"
        assert saved["decided_at"] is not None


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
                **understanding_fields(),
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



def _complete_assessment(client: TestClient, document_id: str, client_id: str) -> tuple[str, dict]:
    started = client.post(
        "/api/assessments/start",
        json={"document_id": document_id, "client_instance_id": client_id},
    )
    assert started.status_code == 200, started.text
    assessment_id = started.json()["assessment_id"]
    headers = {"Authorization": f"Bearer {started.json()['session_token']}"}
    count = started.json()["question_count"]
    for position in range(1, count + 1):
        question = client.get(f"/api/assessments/{assessment_id}/question", headers=headers)
        assert question.status_code == 200, question.text
        assert question.json()["position"] == position
        detail = client.get(f"/api/admin/assessments/{assessment_id}", headers=ADMIN_HEADERS)
        assert detail.status_code == 200, detail.text
        correct_index = next(
            item["correct_index"]
            for item in detail.json()["questions"]
            if item["position"] == position
        )
        answered = client.post(
            f"/api/assessments/{assessment_id}/answer",
            headers=headers,
            json={"selected_index": correct_index},
        )
        assert answered.status_code == 200, answered.text
    result = client.get(f"/api/assessments/{assessment_id}/result", headers=headers)
    assert result.status_code == 200, result.text
    return assessment_id, result.json()


def test_optional_learning_process_version_history_adaptive_questions_and_rubric():
    with TestClient(app) as client:
        _, setup_code = create_lecturer(client, "phase1@test.edu", "Phase One Lecturer")
        activate_lecturer(client, "phase1@test.edu", setup_code)

        course_response = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 950",
                "title": "Learning Assurance Project",
                "academic_year": "2026/2027",
                "semester": "Second Semester",
                "assessment_question_count": 5,
                "checkpoint_question_count": 3,
                "milestones_enabled": True,
                "version_history_enabled": True,
                "milestone_titles": ["Proposal checkpoint", "Analysis checkpoint"],
            },
        )
        assert course_response.status_code == 201, course_response.text
        course = course_response.json()["course"]
        assert course["milestones_enabled"] is True
        assert course["version_history_enabled"] is True
        assert course["final_justification_required"] is True
        assert len(course["milestones"]) == 2
        enrollment_code = course["enrollment_code"]
        milestone_id = course["milestones"][0]["id"]

        resolved = client.get("/api/courses/resolve", params={"code": enrollment_code})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["milestones_enabled"] is True
        assert resolved.json()["version_history_enabled"] is True

        milestone_upload = client.post(
            "/api/documents",
            data={
                "student_name": "Learning Student",
                "student_id": "LEARN/001",
                "title": "Evidence of Learning Project",
                "course_code": enrollment_code,
                "submission_stage": "milestone",
                "milestone_id": milestone_id,
                **understanding_fields(
                    ai_use_level="limited",
                    ai_use_details=(
                        "AI was used only to suggest an outline. The student checked the outline "
                        "against the course material and rewrote every section independently."
                    ),
                ),
            },
            files={"file": ("milestone.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert milestone_upload.status_code == 202, milestone_upload.text
        assert milestone_upload.json()["submission_stage"] == "milestone"
        assert milestone_upload.json()["version_number"] == 1
        assert milestone_upload.json()["assessment_question_count"] == 3
        milestone_document_id = milestone_upload.json()["document_id"]

        milestone_status = client.get(f"/api/documents/{milestone_document_id}/status")
        assert milestone_status.status_code == 200
        assert milestone_status.json()["assessment_question_count"] == 3
        milestone_assessment_id, milestone_result = _complete_assessment(
            client, milestone_document_id, "phase1-browser-milestone"
        )
        assert milestone_result["question_count"] == 3
        assert milestone_result["score"] == 100.0

        final_upload = client.post(
            "/api/documents",
            data={
                "student_name": "Learning Student",
                "student_id": "LEARN/001",
                "title": "Evidence of Learning Project",
                "course_code": enrollment_code,
                "submission_stage": "final",
                **understanding_fields(),
            },
            files={"file": ("final.txt", (sample_document() + " Final evidence and reflection.").encode("utf-8"), "text/plain")},
        )
        assert final_upload.status_code == 202, final_upload.text
        assert final_upload.json()["version_number"] == 2
        assert final_upload.json()["assessment_question_count"] == 5
        final_document_id = final_upload.json()["document_id"]
        final_assessment_id, final_result = _complete_assessment(
            client, final_document_id, "phase1-browser-final"
        )
        assert final_result["question_count"] == 5

        detail = client.get(f"/api/lecturer/assessments/{final_assessment_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["summary"]["version_number"] == 2
        assert len(payload["version_history"]) == 2
        assert payload["version_comparison"] is not None
        assert payload["justification"]["understanding_summary"]
        assert all(item["category"] for item in payload["questions"])
        assert any(item.get("adaptive_reason") for item in payload["questions"][1:])

        scores = [
            {
                "criterion_key": criterion["key"],
                "score": criterion["max_score"],
                "comment": "Criterion fully demonstrated in the submitted work and defence.",
            }
            for criterion in payload["rubric"]
        ]
        mark = client.post(
            f"/api/lecturer/assessments/{final_assessment_id}/rubric-mark",
            json={"scores": scores, "overall_comment": "Learning assurance established."},
        )
        assert mark.status_code == 200, mark.text
        assert mark.json()["total_score"] == 100.0
        assert len(mark.json()["assessment"]["mark_audit"]) == 1

        changed_scores = list(scores)
        changed_scores[0] = {**changed_scores[0], "score": float(changed_scores[0]["score"]) - 1}
        changed = client.post(
            f"/api/lecturer/assessments/{final_assessment_id}/rubric-mark",
            json={
                "scores": changed_scores,
                "overall_comment": "Minor moderation adjustment.",
                "change_reason": "Moderator adjusted the first criterion after review.",
            },
        )
        assert changed.status_code == 200, changed.text
        assert len(changed.json()["assessment"]["mark_audit"]) == 2

        progress = client.get(f"/api/lecturer/courses/{course['id']}/learning-progress")
        assert progress.status_code == 200, progress.text
        assert any(student["student_id"] == "LEARN/001" for student in progress.json()["students"])

        # Optionality check: a final-only course accepts a final submission but rejects milestones.
        final_only = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 951",
                "title": "Final Submission Only",
                "academic_year": "2026/2027",
                "semester": "Second Semester",
                "assessment_question_count": 5,
                "milestones_enabled": False,
                "version_history_enabled": False,
            },
        )
        assert final_only.status_code == 201, final_only.text
        final_only_course = final_only.json()["course"]
        assert final_only_course["milestones_enabled"] is False
        assert final_only_course["version_history_enabled"] is False
        rejected = client.post(
            "/api/documents",
            data={
                "student_name": "Final Only Student",
                "student_id": "FINAL/001",
                "title": "Final Only Assignment",
                "course_code": final_only_course["enrollment_code"],
                "submission_stage": "milestone",
                "milestone_id": milestone_id,
                **understanding_fields(),
            },
            files={"file": ("final-only.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert rejected.status_code == 409


def test_phase2_optional_modules_end_to_end():
    with TestClient(app) as client:
        created = client.post(
            "/api/platform/users",
            headers=ADMIN_HEADERS,
            json={
                "full_name": "Phase Two Administrator",
                "email": "phase2@phase2.edu",
                "institution_name": "Phase Two University",
                "department": "Graduate Studies",
                "role": "institution_admin",
            },
        )
        assert created.status_code == 201, created.text
        activate_lecturer(client, "phase2@phase2.edu", created.json()["setup_code"])

        course_response = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "RES 950",
                "title": "Advanced Learning Assurance",
                "academic_year": "2026/2027",
                "semester": "Second Semester",
                "assessment_question_count": 5,
                "version_history_enabled": True,
                "oral_defence_enabled": True,
                "oral_defence_question_count": 5,
                "practical_tasks_enabled": True,
                "practical_task_count": 2,
                "supervisor_meetings_enabled": True,
                "cross_version_analysis_enabled": True,
                "random_moderation_enabled": True,
                "moderation_sample_percent": 100,
                "moderation_variance_threshold": 10,
                "marker_anomaly_detection_enabled": True,
            },
        )
        assert course_response.status_code == 201, course_response.text
        course = course_response.json()["course"]
        assert course["oral_defence_enabled"] is True
        assert course["practical_tasks_enabled"] is True
        assert course["supervisor_meetings_enabled"] is True
        assert course["cross_version_analysis_enabled"] is True
        assert course["random_moderation_enabled"] is True
        assert course["marker_anomaly_detection_enabled"] is True

        first_upload = client.post(
            "/api/documents",
            data={
                "student_name": "Phase Two Student",
                "student_id": "P2/001",
                "title": "Learning Assurance Project",
                "course_code": course["enrollment_code"],
                **understanding_fields(),
            },
            files={"file": ("version1.txt", sample_document().encode("utf-8"), "text/plain")},
        )
        assert first_upload.status_code == 202, first_upload.text

        second_text = sample_document() + " " + " ".join(
            f"Additional analysis {index} explains a revised practical implication and new evidence."
            for index in range(1, 30)
        )
        second_upload = client.post(
            "/api/documents",
            data={
                "student_name": "Phase Two Student",
                "student_id": "P2/001",
                "title": "Learning Assurance Project",
                "course_code": course["enrollment_code"],
                **understanding_fields(),
            },
            files={"file": ("version2.txt", second_text.encode("utf-8"), "text/plain")},
        )
        assert second_upload.status_code == 202, second_upload.text
        document_id = second_upload.json()["document_id"]
        assert second_upload.json()["version_number"] == 2

        started = client.post(
            "/api/assessments/start",
            json={"document_id": document_id, "client_instance_id": "phase2-browser-instance-0001"},
        )
        assert started.status_code == 200, started.text
        assessment_id = started.json()["assessment_id"]
        student_headers = {"Authorization": f"Bearer {started.json()['session_token']}"}

        for position in range(1, 6):
            question = client.get(f"/api/assessments/{assessment_id}/question", headers=student_headers)
            assert question.status_code == 200, question.text
            detail = client.get(f"/api/admin/assessments/{assessment_id}", headers=ADMIN_HEADERS)
            correct_index = next(
                item["correct_index"] for item in detail.json()["questions"] if item["position"] == position
            )
            answered = client.post(
                f"/api/assessments/{assessment_id}/answer",
                headers=student_headers,
                json={"selected_index": correct_index},
            )
            assert answered.status_code == 200, answered.text

        oral = client.post(
            f"/api/lecturer/assessments/{assessment_id}/oral-defence/start",
            json={"question_count": 5},
        )
        assert oral.status_code == 200, oral.text
        oral_questions = oral.json()["oral_defence"]["questions"]
        assert len(oral_questions) == 5
        oral_complete = client.post(
            f"/api/lecturer/assessments/{assessment_id}/oral-defence/complete",
            json={
                "scores": [
                    {"question_id": item["id"], "score": 4, "comment": "Adequate defence"}
                    for item in oral_questions
                ],
                "outcome": "satisfactory",
                "notes": "The student defended the main decisions.",
            },
        )
        assert oral_complete.status_code == 200, oral_complete.text
        assert oral_complete.json()["oral_defence"]["total_score"] == 80.0

        tasks = client.post(
            f"/api/lecturer/assessments/{assessment_id}/practical-tasks/generate",
            json={"count": 2},
        )
        assert tasks.status_code == 200, tasks.text
        assert len(tasks.json()["practical_tasks"]) == 2
        first_task = tasks.json()["practical_tasks"][0]
        task_score = client.post(
            f"/api/lecturer/practical-tasks/{first_task['id']}/score",
            json={
                "student_response": "The student reproduced the selected analytical step and explained each stage accurately.",
                "score": 18,
                "notes": "Minor prompting was required.",
            },
        )
        assert task_score.status_code == 200, task_score.text
        assert task_score.json()["practical_task"]["status"] == "completed"

        meeting = client.post(
            f"/api/lecturer/courses/{course['id']}/supervisor-meetings",
            json={
                "student_id": "P2/001",
                "document_id": document_id,
                "meeting_at": "2026-07-17T10:00:00Z",
                "meeting_type": "progress_review",
                "summary": "The student explained the revised analysis and responded to questions about the evidence.",
                "decisions": "Retain the revised structure.",
                "action_items": "Clarify the practical implication before final approval.",
            },
        )
        assert meeting.status_code == 201, meeting.text

        analysis = client.post(f"/api/lecturer/documents/{document_id}/version-analysis")
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["cross_version_analysis"]["current_version_number"] == 2

        rubric = course["rubric"]
        rubric_mark = client.post(
            f"/api/lecturer/assessments/{assessment_id}/rubric-mark",
            json={
                "scores": [
                    {
                        "criterion_key": item["key"],
                        "score": float(item["max_score"]) * 0.8,
                        "comment": "Meets the criterion.",
                    }
                    for item in rubric
                ],
                "overall_comment": "Strong evidence of understanding.",
            },
        )
        assert rubric_mark.status_code == 200, rubric_mark.text
        detail = rubric_mark.json()["assessment"]
        assert detail["moderation"]["status"] == "selected"

        moderation = client.post(
            f"/api/lecturer/assessments/{assessment_id}/moderation/complete",
            json={
                "moderated_mark": 82,
                "decision": "adjusted",
                "notes": "The moderated score reflects stronger oral and practical evidence.",
            },
        )
        assert moderation.status_code == 200, moderation.text
        assert moderation.json()["moderation"]["status"] == "completed"

        anomalies = client.get(f"/api/lecturer/courses/{course['id']}/marker-anomalies?refresh=true")
        assert anomalies.status_code == 200, anomalies.text
        assert anomalies.json()["anomalies"] == []

        final_detail = client.get(f"/api/lecturer/assessments/{assessment_id}")
        assert final_detail.status_code == 200, final_detail.text
        body = final_detail.json()
        assert body["oral_defence"]["status"] == "completed"
        assert len(body["practical_tasks"]) == 2
        assert len(body["supervisor_meetings"]) == 1
        assert body["cross_version_analysis"] is not None
        assert body["moderation"]["status"] == "completed"


def test_task_governance_standalone_exam_uploaded_bank_and_recordings():
    with TestClient(app) as client:
        _, setup_code = create_lecturer(client, "task-facilitator@test.edu", "Task Facilitator")
        activate_lecturer(client, "task-facilitator@test.edu", setup_code)

        course_response = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "EXM 900",
                "title": "Standalone Examination",
                "academic_year": "2026/2027",
                "semester": "First Semester",
                "assessment_question_count": 27,
            },
        )
        assert course_response.status_code == 201, course_response.text
        course = course_response.json()["course"]

        policy = client.patch(
            "/api/platform/features/secure_browser_enforcement",
            headers=ADMIN_HEADERS,
            json={"activation_mode": "admin_only"},
        )
        assert policy.status_code == 200, policy.text

        forbidden = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks",
            json={
                "title": "Lecturer cannot bypass policy",
                "mode": "standalone_exam",
                "assessment_question_count": 23,
                "features": {"secure_browser_enforcement": True},
            },
        )
        assert forbidden.status_code == 403

        task_response = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks",
            json={
                "title": "Open-count final examination",
                "instructions": "Answer every question without leaving the examination page.",
                "mode": "standalone_exam",
                "assessment_question_count": 23,
                "max_attempts": 2,
                "features": {
                    "screen_activity_recording": True,
                    "environment_audio_recording": True,
                },
            },
        )
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()["task"]
        assert task["assessment_question_count"] == 23
        assert task["max_attempts"] == 2
        assert task["effective_features"]["webcam_monitoring"] is False

        admin_activation = client.patch(
            f"/api/platform/assessment-tasks/{task['id']}/features",
            headers=ADMIN_HEADERS,
            json={"features": {"secure_browser_enforcement": True}},
        )
        assert admin_activation.status_code == 200, admin_activation.text
        assert admin_activation.json()["task"]["effective_features"]["secure_browser_enforcement"] is True

        questions = []
        categories = [
            "document_knowledge",
            "decision_defence",
            "interpretation",
            "counterfactual",
            "error_correction",
            "transfer_application",
        ]
        difficulties = ["recall", "understanding", "application"]
        for number in range(1, 24):
            questions.append(
                {
                    "stem": f"Which lecturer-approved answer is correct for examination item number {number}?",
                    "options": [
                        f"Approved answer {number}",
                        f"Distractor B {number}",
                        f"Distractor C {number}",
                        f"Distractor D {number}",
                    ],
                    "correct_option_index": 0,
                    "difficulty": difficulties[(number - 1) % len(difficulties)],
                    "category": categories[(number - 1) % len(categories)],
                    "seconds": 30,
                    "source_quote": f"Lecturer source reference for examination item {number}.",
                    "source_location": f"Lecturer bank item {number}",
                    "explanation": f"The lecturer designated option A as correct for item {number}.",
                }
            )
        bank_upload = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/question-bank",
            data={"title": "Lecturer-authored question bank"},
            files={
                "file": (
                    "question-bank.json",
                    json.dumps({"questions": questions}).encode("utf-8"),
                    "application/json",
                )
            },
        )
        assert bank_upload.status_code == 200, bank_upload.text
        assert bank_upload.json()["question_count"] == 23

        resolved = client.get(f"/api/courses/resolve?code={course['enrollment_code']}")
        assert resolved.status_code == 200, resolved.text
        resolved_task = next(item for item in resolved.json()["assessment_tasks"] if item["id"] == task["id"])
        assert resolved_task["ready"] is True

        started = client.post(
            "/api/examinations/start",
            json={
                "course_code": course["enrollment_code"],
                "task_id": task["id"],
                "candidate_name": "Standalone Candidate",
                "candidate_id": "EXM/001",
                "readiness_confirmed": True,
                "client_instance_id": "standalone-browser-instance-0001",
            },
        )
        assert started.status_code == 200, started.text
        assert started.json()["question_count"] == 23
        assert started.json()["secure_browser_enforcement"] is True
        assessment_id = started.json()["assessment_id"]
        headers = {"Authorization": f"Bearer {started.json()['session_token']}"}

        screen = client.post(
            f"/api/assessments/{assessment_id}/recording-chunk",
            headers=headers,
            data={"recording_type": "screen", "sequence": 0, "duration_ms": 30000},
            files={"media": ("screen.webm", b"screen-recording-evidence", "video/webm")},
        )
        assert screen.status_code == 201, screen.text
        audio = client.post(
            f"/api/assessments/{assessment_id}/recording-chunk",
            headers=headers,
            data={"recording_type": "environment_audio", "sequence": 0, "duration_ms": 30000},
            files={"media": ("audio.webm", b"environment-audio-evidence", "audio/webm")},
        )
        assert audio.status_code == 201, audio.text

        detail = client.get(f"/api/lecturer/assessments/{assessment_id}")
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["recordings"]) == 2
        download = client.get(detail.json()["recordings"][0]["download_url"])
        assert download.status_code == 200

        terminated = client.post(
            f"/api/assessments/{assessment_id}/terminate",
            headers=headers,
            json={
                "client_instance_id": "standalone-browser-instance-0001",
                "reason": "fullscreen_exited",
            },
        )
        assert terminated.status_code == 200, terminated.text
        assert terminated.json()["status"] == "locked"

        second = client.post(
            "/api/examinations/start",
            json={
                "course_code": course["enrollment_code"],
                "task_id": task["id"],
                "candidate_name": "Standalone Candidate",
                "candidate_id": "exm/001",
                "readiness_confirmed": True,
                "client_instance_id": "standalone-browser-instance-0002",
            },
        )
        assert second.status_code == 200, second.text

        blocked = client.post(
            "/api/examinations/start",
            json={
                "course_code": course["enrollment_code"],
                "task_id": task["id"],
                "candidate_name": "Standalone Candidate",
                "candidate_id": "EXM/001",
                "readiness_confirmed": True,
                "client_instance_id": "standalone-browser-instance-0003",
            },
        )
        assert blocked.status_code == 409, blocked.text
        assert "maximum of 2" in blocked.json()["detail"]

        reset = client.delete(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/attempts",
            params={"candidate_id": "EXM/001"},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["deleted_attempts"] == 2
        assert reset.json()["retake_enabled"] is True

        restarted = client.post(
            "/api/examinations/start",
            json={
                "course_code": course["enrollment_code"],
                "task_id": task["id"],
                "candidate_name": "Standalone Candidate",
                "candidate_id": "EXM/001",
                "readiness_confirmed": True,
                "client_instance_id": "standalone-browser-instance-0004",
            },
        )
        assert restarted.status_code == 200, restarted.text


def test_live_exam_roster_readiness_timing_and_proctor_controls():
    with TestClient(app) as client:
        owner, setup_code = create_lecturer(client, "live-proctor@test.edu", "Live Proctor")
        activate_lecturer(client, "live-proctor@test.edu", setup_code)
        course_response = client.post(
            "/api/lecturer/courses",
            json={
                "course_code": "LVE 910",
                "title": "Live Proctored Examination",
                "academic_year": "2026/2027",
                "semester": "First Semester",
                "assessment_question_count": 3,
            },
        )
        assert course_response.status_code == 201, course_response.text
        course = course_response.json()["course"]
        now = datetime.now(timezone.utc)
        task_response = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks",
            json={
                "title": "Scheduled proctored final",
                "mode": "standalone_exam",
                "assessment_question_count": 3,
                "max_attempts": 1,
                "exam_starts_at": (now - timedelta(minutes=5)).isoformat(),
                "exam_ends_at": (now + timedelta(hours=3)).isoformat(),
                "duration_minutes": 60,
                "late_entry_minutes": 30,
                "roster_required": True,
                "geofence_label": "UCC Main Examination Hall",
                "geofence_latitude": 5.115,
                "geofence_longitude": -1.290,
                "geofence_radius_meters": 500,
                "geofence_max_accuracy_meters": 100,
                "features": {
                    "live_exam_monitoring": True,
                    "webcam_monitoring": True,
                    "location_geofence": True,
                },
            },
        )
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()["task"]

        bank = {
            "questions": [
                {
                    "stem": f"Which lecturer-approved answer is correct for live examination item {number}?",
                    "options": [f"Approved {number}", f"Option B {number}", f"Option C {number}", f"Option D {number}"],
                    "correct_option_index": 0,
                    "difficulty": "understanding",
                    "category": "decision_defence",
                    "seconds": 30,
                    "source_quote": f"The lecturer approved the answer for live examination item {number}.",
                    "source_location": f"Live bank item {number}",
                    "explanation": f"The first option is the approved answer for item {number}.",
                }
                for number in range(1, 4)
            ]
        }
        upload = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/question-bank",
            data={"title": "Live proctored question bank"},
            files={"file": ("bank.json", json.dumps(bank).encode(), "application/json")},
        )
        assert upload.status_code == 200, upload.text
        roster = client.post(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/candidates/import",
            files={
                "file": (
                    "roster.csv",
                    b"candidate_id,candidate_name,access_pin,extra_time_minutes,monitor_group\nLVE/001,Approved Candidate,2468,15,Group A\n",
                    "text/csv",
                )
            },
        )
        assert roster.status_code == 200, roster.text
        assert roster.json()["candidate_count"] == 1
        eligible = client.get(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/candidates"
        )
        assert eligible.status_code == 200, eligible.text
        assert eligible.json()["candidates"][0]["candidate_name"] == "Approved Candidate"
        assert eligible.json()["candidates"][0]["monitor_group"] == "Group A"
        assert "access_pin" not in eligible.json()["candidates"][0]

        assignments = client.get(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/monitor-assignments"
        )
        assert assignments.status_code == 200, assignments.text
        assert assignments.json()["groups"][0]["group_label"] == "Group A"
        assigned = client.put(
            f"/api/lecturer/courses/{course['id']}/tasks/{task['id']}/monitor-assignments",
            json={"group_label": "Group A", "monitor_user_id": owner["id"]},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["groups"][0]["monitor_name"] == "Live Proctor"

        base_start = {
            "course_code": course["enrollment_code"],
            "task_id": task["id"],
            "candidate_name": "Unverified Name",
            "candidate_id": "LVE/001",
            "access_pin": "2468",
            "latitude": 5.115,
            "longitude": -1.290,
            "location_accuracy_meters": 25,
            "client_instance_id": "live-proctor-browser-instance-0001",
        }
        not_ready = client.post("/api/examinations/start", json=base_start)
        assert not_ready.status_code == 409, not_ready.text
        outside_venue = client.post(
            "/api/examinations/start",
            json={**base_start, "readiness_confirmed": True, "latitude": 6.115},
        )
        assert outside_venue.status_code == 403, outside_venue.text
        wrong_pin = client.post(
            "/api/examinations/start",
            json={**base_start, "readiness_confirmed": True, "access_pin": "9999"},
        )
        assert wrong_pin.status_code == 403, wrong_pin.text
        started = client.post(
            "/api/examinations/start",
            json={**base_start, "readiness_confirmed": True},
        )
        assert started.status_code == 200, started.text
        assert started.json()["extra_time_minutes"] == 15
        assert started.json()["exam_deadline_at"]
        assert started.json()["location_verified"] is True
        assessment_id = started.json()["assessment_id"]
        headers = {"Authorization": f"Bearer {started.json()['session_token']}"}

        first_question = client.get(
            f"/api/assessments/{assessment_id}/question", headers=headers
        )
        assert first_question.status_code == 200, first_question.text
        first_stem = first_question.json()["stem"]
        interrupted = client.post(
            f"/api/assessments/{assessment_id}/interrupt",
            headers=headers,
            json={
                "client_instance_id": "live-proctor-browser-instance-0001",
                "reason": "pagehide",
            },
        )
        assert interrupted.status_code == 200, interrupted.text
        resumed_heartbeat = client.post(
            f"/api/assessments/{assessment_id}/heartbeat",
            headers=headers,
            json={
                "client_instance_id": "live-proctor-browser-instance-0001",
                "camera_verified": True,
                "reason": "resume",
            },
        )
        assert resumed_heartbeat.status_code == 200, resumed_heartbeat.text
        assert resumed_heartbeat.json()["status"] == "in_progress"
        reshuffled_question = client.get(
            f"/api/assessments/{assessment_id}/question", headers=headers
        )
        assert reshuffled_question.status_code == 200, reshuffled_question.text
        assert reshuffled_question.json()["stem"] != first_stem
        assert reshuffled_question.json()["adaptive_reason"] == "reshuffled_after_resume"

        movement = client.post(
            f"/api/assessments/{assessment_id}/monitoring-event",
            headers=headers,
            json={
                "event_type": "excessive_movement",
                "duration_ms": 1600,
                "question_position": reshuffled_question.json()["position"],
                "severity": "warning",
                "corrected": False,
                "message": "Excessive head movement was detected.",
            },
        )
        assert movement.status_code == 200, movement.text

        remote_monitor = client.get(
            f"/api/lecturer/courses/{course['id']}/live-exam-monitor"
        )
        assert remote_monitor.status_code == 200, remote_monitor.text
        assert remote_monitor.json()["capacity"]["limit"] == 30
        assert remote_monitor.json()["capacity"]["active"] == 1
        assert remote_monitor.json()["sessions"][0]["monitor_group"] == "Group A"
        assert remote_monitor.json()["sessions"][0]["suspected_movement"]["event_type"] == "excessive_movement"

        warning = client.post(
            f"/api/lecturer/assessments/{assessment_id}/proctor-action",
            json={"action": "warning", "message": "Keep your face visible."},
        )
        assert warning.status_code == 200, warning.text
        heartbeat = client.post(
            f"/api/assessments/{assessment_id}/heartbeat",
            headers=headers,
            json={
                "client_instance_id": "live-proctor-browser-instance-0001",
                "camera_verified": False,
                "reason": "periodic",
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["proctor_message"] == "Keep your face visible."

        paused = client.post(
            f"/api/lecturer/assessments/{assessment_id}/proctor-action",
            json={"action": "pause", "message": "Wait for an identity check."},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["assessment"]["status"] == "proctor_paused"
        question_while_paused = client.get(
            f"/api/assessments/{assessment_id}/question", headers=headers
        )
        assert question_while_paused.status_code == 200
        assert question_while_paused.json()["status"] == "proctor_paused"

        resumed = client.post(
            f"/api/lecturer/assessments/{assessment_id}/proctor-action",
            json={"action": "resume"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["assessment"]["status"] == "in_progress"
        added = client.post(
            f"/api/lecturer/assessments/{assessment_id}/proctor-action",
            json={"action": "add_time", "minutes": 5},
        )
        assert added.status_code == 200, added.text
        assert added.json()["assessment"]["exam_seconds_remaining"] > 0

        ended = client.post(
            f"/api/lecturer/assessments/{assessment_id}/proctor-action",
            json={"action": "terminate", "message": "Session ended for review."},
        )
        assert ended.status_code == 200, ended.text
        assert ended.json()["assessment"]["status"] == "locked"
        detail = client.get(f"/api/lecturer/assessments/{assessment_id}")
        assert detail.status_code == 200, detail.text
        event_types = {item["event_type"] for item in detail.json()["monitoring_events"]}
        assert {"proctor_warning", "proctor_pause", "proctor_resume", "proctor_add_time", "proctor_terminate"}.issubset(event_types)
