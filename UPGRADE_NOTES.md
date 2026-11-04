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


## Version 0.9.0, Phase 1 learning assurance

This release introduces optional learning milestones and optional version history while keeping the final understanding justification compulsory for every submission. It adds milestone checkpoint assessments, higher-order question categories, adaptive sequencing, learning intervention alerts, rubric-controlled marking, an append-only mark-change audit trail, and course-level student learning progress.

Database revision: `20260717_07_phase1_learning_assurance.py`.

No new environment variable is required. Deploy with the normal prestart command so the migration is applied automatically.

## Version 1.0.0, Phase 2 optional learning assurance

This release adds six independently optional course modules:

- oral defence
- practical reproduction tasks
- supervisor meeting records
- cross-version analysis
- random moderation
- marker anomaly detection

All existing courses receive these features in the disabled state. Lecturers can turn on any combination and configure oral-question count, practical-task count, moderation sample percentage, and acceptable moderation variance.

Database revision: `20260717_08_phase2_learning_assurance.py`.

No new Render environment variable is required. Keep the standard start command:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

The migration preserves existing users, courses, submissions, assessments, reports, monitoring evidence, rubric marks, and Phase 1 learning records.

## Version 1.1.0, Phase 3 assessment evidence profile

This release adds course-level AI-use rules, student-facing policy guidance, AI-policy alignment review, a combined assessment evidence profile, a recorded lecturer verification decision, and an optional live examination monitoring dashboard.

The evidence profile combines document-specific understanding, completed oral-defence evidence, completed practical-task evidence, AI-policy alignment, and contextual session indicators. It does not claim to prove authorship, cheating, or misconduct.

Database revision: `20260831_09_assessment_evidence_profile.py`.

No new Render environment variable is required. Keep the standard start command so existing course and assessment records are migrated safely.

## Version 1.2.0: task feature governance and standalone examination mode

This release adds platform-wide feature policies, lecturer-configured task profiles, standalone examinations, lecturer-authored JSON question banks, secure fullscreen enforcement, and optional consented screen and environment-audio recordings.

The migration adds:

- `feature_settings`
- `assessment_tasks`
- `task_feature_activations`
- `assessment_recording_chunks`
- task and feature-snapshot fields on documents
- feature snapshots and candidate identity fields on assessments

Existing courses and submissions retain their legacy course-level behavior. New task profiles start with basic assessment only unless an optional feature is allowed and activated. Feature snapshots protect in-progress and historical attempts from later configuration changes.

Question counts are no longer capped by Kanokware. Lecturers may set any positive whole number for course assessments, task assessments, milestone checks, standalone exams, and oral defences. The full configured number of validated questions must be ready before an attempt can start. Very large AI-generated banks may require a higher OpenAI timeout and incur additional model usage.

Courses and tasks now also store lecturer-set positive whole-number attempt limits. Task limits are enforced per candidate across new document uploads and replacement standalone exam banks. On limit failure the candidate interface returns to its start panel. Lecturers and facilitators with grading access can change a task's limit or reset all attempts for one candidate and task; the reset also deletes the related answers and monitoring or recording evidence and records an audit event.

Screen and audio chunks are stored in the application database. Before enabling them in production, check database capacity and establish institutional consent, retention, access, accommodation, and deletion policies.

Database revision: `20260901_10_task_feature_governance.py`.

No new environment variable is required. Keep the standard start command:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Version 1.3.0: live proctored examination operations

This release adds examination schedules, overall duration, late-entry rules, candidate roster CSV import, hashed candidate access PINs, per-candidate extra-time accommodations, an optional venue geofence, candidate monitor groups, and a device-readiness gate.

The remote live monitor supports up to 30 active candidate sessions in a numbered tile wall. Each tile shows identity, assigned group, connection, progress, camera state and movement warnings. The roster's optional `monitor_group` value can be assigned to an authorised course monitor. Owners and institution administrators retain the full view, while other monitors see only assigned groups when assignments exist. Authorised lecturers can send a targeted comment, pause, resume, add time, or end and lock an attempt. Movement indicators produce on-screen and spoken candidate warnings. Invigilator comments are also shown and spoken when browser speech is available. Candidate heartbeats receive those actions even when webcam monitoring is not enabled.

The optional `location_geofence` task feature uses browser geolocation to check the candidate's reported position, accuracy and distance from a lecturer-set examination venue before the attempt starts. No Google Maps key or new environment variable is required. Treat this as an admission control and review indicator, not definitive evidence, and maintain an institutional accommodation and privacy process.

After a resumable exit, the visible question is moved to the end and unseen questions are reshuffled. A different question appears when one is available. If there is no replacement, the visible question remains the final question. Time already used is retained and the event is recorded.

Database revision: `20261104_11_live_exam_operations.py`.

No new environment variable is required. Deploy with the standard prestart command so the migration is applied before the web service starts.
