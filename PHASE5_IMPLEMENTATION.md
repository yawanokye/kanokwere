# Phase 5 implementation: live proctored examination operations

Kanokware 1.3 adds operational controls for scheduled, live proctored examinations.

## Lecturer controls

- Set optional opening and closing times.
- Set an optional overall duration and late-entry window.
- Upload a UTF-8 CSV roster with `candidate_id`, `candidate_name`, `access_pin`, and optional `extra_time_minutes` and `monitor_group`.
- Manage eligibility after task creation from a dedicated exam-specific roster panel with a downloadable CSV template.
- Require roster admission for a task.
- Optionally require candidates to start within a named venue, latitude, longitude and radius, with a lecturer-set maximum GPS inaccuracy.
- Assign each roster group to an authorised remote monitor.
- Monitor active, interrupted, paused, locked, and completed sessions.
- Remotely monitor up to 30 active sessions in one numbered candidate tile wall. Owners see all groups, while assigned monitors see only their groups.
- See movement warnings on the affected tile and send a targeted candidate comment.
- Send a warning, pause or resume an attempt, add time, or end and lock the session.
- Review each invigilator intervention in the same incident trail as monitoring events.

## Candidate controls

- Choose a clear standalone-exam path from the student home page without creating a student account.
- Enter the lecturer-provided course or exam code before the candidate ID and PIN fields are enabled.
- Pass a device-readiness check before starting.
- Enter an approved candidate ID and access PIN when roster admission is active.
- Share precise browser location during readiness when the optional venue restriction is active.
- Receive invigilator messages without leaving the examination screen.
- Hear spoken warnings for detected movement and targeted invigilator comments when browser speech is available.
- See a blocking pause state while both the question clock and overall duration are suspended.
- Resume on a different unseen question after leaving, with the previously visible question moved to the end. If no replacement exists, it remains the final question.

## Security and fairness

- Access PINs are stored with the same recommended password hashing used for lecturer credentials.
- Candidate IDs are normalised before roster and attempt checks.
- Per-candidate extra time is applied at session creation and recorded on the attempt.
- The server calculates venue distance and rejects starts outside the configured radius or with insufficient GPS accuracy.
- Attempts retain the verified distance and reported accuracy without storing the candidate's raw live coordinates.
- Browser geolocation may be unavailable or spoofed. It is an admission control and review indicator, not standalone proof of misconduct.
- Server-side deadlines remain authoritative.
- Time already used on a question is retained when the remaining sequence is reshuffled after an interruption.
- Proctor actions require lecturer course access and are written to monitoring and platform audit records.
- Monitoring evidence remains an indicator for human review, not automatic proof of misconduct.

Database revision: `20261104_11_live_exam_operations.py`.
