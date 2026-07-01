# Upgrade notes: lecturer setup-code accounts

## Replace the existing repository

Commit the contents of this package to the same GitHub repository used by Render. Keep the existing PostgreSQL database attached.

Use this Render start command:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

The prestart step creates missing tables and applies Alembic migrations without deleting existing submissions.

## Required Render variables

```env
APP_NAME=Kanokware
ENVIRONMENT=production
LECTURER_SESSION_HOURS=12
LOGIN_MAX_FAILURES=5
LOGIN_LOCK_MINUTES=15
LECTURER_REGISTRATION_ENABLED=false
ACCOUNT_SETUP_CODE_HOURS=48
```

Keep the existing `DATABASE_URL`, `OPENAI_API_KEY`, `ADMIN_KEY`, assessment, and webcam variables.

## New account workflow

1. Open the Admin tab and enter `ADMIN_KEY`.
2. Create the lecturer account. Staff ID is not requested.
3. Copy the login email and one-time setup code.
4. Give those details directly to the lecturer.
5. The lecturer selects **Activate account** and creates a private password and six-digit recovery PIN.
6. The lecturer is signed in automatically after activation.
7. Forgotten passwords are reset automatically with the login email and recovery PIN.
8. If the recovery PIN is forgotten, the administrator issues a new setup code.

The new Alembic revision is `20260626_03`. The legacy `staff_id` database column is left untouched when it already exists, but the application no longer collects, displays, or uses staff IDs.

## Database schema repair, revision 20260626_04

This revision repairs deployments where Alembic was marked as current but the
`users` table was still missing one or more account activation fields. Render's
prestart step now verifies the complete user schema before Uvicorn starts.


## 2026-06-29: webcam warnings and course question counts

The deployment adds `courses.assessment_question_count`, `assessments.question_count`, and the `monitoring_events` table. The existing Render start command runs the migration automatically:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

No new Render environment variable is required. The Content Security Policy now permits the jsDelivr MediaPipe runtime used for browser-side face detection.

## 2026-06-30 advanced monitoring reliability update

- Added mandatory monitoring preflight before assessment creation.
- Added local MediaPipe detector health watchdog and native FaceDetector fallback where available.
- Reduced no-face, multiple-face, looking-away, and excessive-movement thresholds.
- Added camera-covered and camera-frozen detection.
- Added monitoring-unavailable and browser-window-blur events.
- Added queued retries for monitoring events that fail to reach the server.
- Kept a concise, transparent monitoring consent statement without exposing capture timing.
- No database migration or new environment variable is required.

## 2026-06-30: Assessment interruption and recovery

This release adds server heartbeats, secure same-browser resume, persistent monitoring-event queues, camera reverification, interruption limits, and lecturer controls.

New Render environment variables:

```env
HEARTBEAT_INTERVAL_SECONDS=5
HEARTBEAT_STALE_SECONDS=20
ASSESSMENT_RESUME_WINDOW_MINUTES=15
MAX_ASSESSMENT_INTERRUPTION_COUNT=2
MAX_ASSESSMENT_OFFLINE_SECONDS=300
```

The migration `20260630_06_assessment_continuity.py` adds the required assessment audit fields. Keep the start command as:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

Behaviour:

- The server-side question timer continues during a connection interruption.
- Answers are disabled while offline and are accepted only after server confirmation.
- The same browser can resume within 15 minutes after camera reverification.
- More than two interruptions or more than five minutes total offline locks the attempt.
- Lecturers can allow resume, end and score the attempt, mark the interruption excused, or reset the attempt.


## MediaPipe Tasks face-model metadata fix

The startup process now replaces the legacy Face Detection model with the official
MediaPipe Tasks-compatible BlazeFace short-range model. This fixes the
`NormalizationOptions metadata` initialisation failure. Render must have outbound
access to `storage.googleapis.com` during startup.
