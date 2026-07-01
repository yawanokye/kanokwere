# Kanokware MVP

**Know your work. Prove your work.**

Kanokware generates 20 questions from a student's uploaded document and administers a timed ownership-confidence assessment. Every question has 30 seconds. The default threshold is 80%, which requires at least 16 correct answers.

## Multi-lecturer controls

This release replaces the shared lecturer `ADMIN_KEY` workflow with individual lecturer accounts and course-based access.

- Lecturers register using an institutional email, institution, department, and staff ID.
- New accounts remain pending until approved by the platform administrator.
- The first approved account for an institution can be assigned the `institution_admin` role.
- Lecturers sign in using secure HTTP-only cookies.
- Each lecturer creates courses and receives a unique student enrolment code.
- Students enter the enrolment code when uploading their work.
- A lecturer sees only submissions linked to courses assigned to that account.
- Course owners can add approved co-lecturers or view-only collaborators.
- Institution administrators can access all courses belonging to their institution.
- Reviews, webcam photos, PDF reports, resets, and deletions are protected by server-side course access checks.
- Security-sensitive actions are written to the audit log.
- The original `ADMIN_KEY` remains only for platform-level lecturer approval and emergency administration.

## Assessment features

- PDF, DOCX, and TXT upload
- Original file processed in memory and not retained
- Exactly 20 grounded questions
- Six recall, eight understanding, and six application questions
- Randomised question and answer order
- Correct answers never sent to the student browser
- Server-enforced 30-second timing
- Focus-loss logging
- Webcam active during the assessment with audio disabled
- No video recording
- One randomly timed still image stored with the assessment
- One attempt per document by default
- Lecturer-controlled reset
- Question-level evidence review
- PDF report
- PostgreSQL production support and SQLite local support

## Roles

### Platform administrator

Uses the Render-generated `ADMIN_KEY` to approve or suspend lecturer accounts. This key should not be shared with lecturers.

### Institution administrator

Can access all courses and submissions within the institution and can create courses or add lecturers.

### Lecturer

Can access courses explicitly assigned to the account. A course owner can add co-lecturers.

### Co-lecturer

Can review and manage submissions for the assigned course.

### Viewer

Can review evidence but cannot reset or delete submissions.

## Lecturer onboarding workflow

1. Lecturer opens the **Lecturer** tab and registers.
2. Platform administrator opens the approval section and enters `ADMIN_KEY`.
3. Administrator approves the lecturer as either `lecturer` or `institution_admin`.
4. Lecturer signs in and creates a course.
5. Kanokware generates an enrolment code such as `KANO-A7K92Q`.
6. Lecturer shares the code with the relevant students.
7. Students use the code when uploading documents.
8. Submissions appear only in the dashboards of lecturers assigned to that course.

Approval currently serves as manual institutional-email verification. An automated email-verification provider can be added later.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.prestart
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`.

## Render deployment

The supplied `render.yaml` runs:

```bash
python -m app.prestart && uvicorn main:app --host 0.0.0.0 --port $PORT
```

`app.prestart` creates new tables and applies the Alembic schema revision. Existing document and assessment data are retained while the new institution, lecturer, course, session, and audit structures are added.

Required production variables include:

```env
ENVIRONMENT=production
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.5
ADMIN_KEY=generated-by-render
PASS_THRESHOLD=80
QUESTION_TIME_SECONDS=30
LECTURER_SESSION_HOURS=12
LOGIN_MAX_FAILURES=5
LOGIN_LOCK_MINUTES=15
LECTURER_REGISTRATION_ENABLED=true
WEBCAM_REQUIRED=true
```

`DATABASE_URL` is supplied automatically by the PostgreSQL database defined in `render.yaml`.

## Main account and course routes

```text
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/change-password

GET    /api/platform/pending
POST   /api/platform/users/{user_id}/approve
POST   /api/platform/users/{user_id}/suspend

GET    /api/lecturer/courses
POST   /api/lecturer/courses
POST   /api/lecturer/courses/{course_id}/collaborators
POST   /api/lecturer/courses/{course_id}/regenerate-code

GET    /api/lecturer/submissions
GET    /api/lecturer/assessments/{assessment_id}
GET    /api/lecturer/assessments/{assessment_id}/snapshot
GET    /api/lecturer/assessments/{assessment_id}/report.pdf
DELETE /api/lecturer/assessments/{assessment_id}
DELETE /api/lecturer/documents/{document_id}
```

## Data and privacy

Kanokware stores account details, course assignments, extracted document text, generated questions, assessment responses, timing, scores, audit events, and one webcam still image. It does not retain the original uploaded file or record webcam video or audio.

Institutions should publish retention periods, identify who may access still images, and establish a clear appeal and due-process procedure. The ownership-confidence score is evidence of demonstrated document knowledge. It is not conclusive proof of authorship or misconduct.

## Tests

```bash
pytest -q
```

The tests cover lecturer registration and approval, secure login, course creation, course-level submission isolation, document assessment, webcam capture, evidence review, and PDF generation.

## Lecturer account administration

Lecturers do not create their own accounts. Open the **Admin** tab, enter the platform `ADMIN_KEY`, and create the account using the lecturer's name, login email, institution, department, and role. Staff ID is not collected.

Kanokware displays a one-time setup code. Give the lecturer the login email and setup code directly. The lecturer opens the **Lecturer** tab, activates the account, creates a private password, and creates a six-digit recovery PIN. The administrator never creates or sees the lecturer's password or recovery PIN.

A lecturer who forgets a password can reset it automatically with the login email and recovery PIN. Administrator approval and email delivery are not required. If the recovery PIN is also forgotten, the administrator can issue a new one-time setup code. The administrator can also suspend, reactivate, or delete an account. Course ownership is transferred safely before deletion.


## Live webcam warnings and configurable assessment length

- Lecturers choose between 5 and 20 assessment questions when creating a course.
- Course owners and institution administrators can change the number later from **My courses → Set questions**.
- Kanokware still generates a validated 20-question bank and randomly selects the course-defined number for each attempt.
- The browser analyses approximately one webcam frame per second for face presence, multiple faces, sustained off-centre or turned-away position, low lighting, and camera interruption.
- Video and audio are not recorded. Only one existing random still image is retained.
- Warning events are stored with the question number, duration, severity, and whether the condition was corrected.
- Monitoring warnings never automatically fail a student. They are lecturer review indicators only.
- The browser loads MediaPipe Face Detection from jsDelivr. If it cannot load, camera continuity and tab-switch monitoring still remain active.


### Webcam movement monitoring

The browser-side monitor records warnings for sustained looking away and excessive face movement, alongside no-face, multiple-face, low-light, camera interruption, and tab-switch events. These are review indicators and do not automatically determine misconduct.

## Advanced camera monitoring reliability

The assessment now performs a mandatory camera-monitoring preflight before an attempt starts. The preflight must receive several consecutive single-face detections from a live, adequately lit camera feed. During the assessment, Kanokware combines face presence, multiple-face checks, sustained head position, rapid head movement, lighting, camera obstruction, frozen-feed detection, camera interruption, and browser-focus monitoring. A watchdog detects a stalled face engine, and failed monitoring-event uploads are queued and retried so warnings are not silently lost.

Students receive a concise consent notice that camera monitoring is active and that monitoring evidence may be retained for authorised lecturer review. The interface does not disclose the random verification-image capture time.

## Assessment continuity

Kanokware sends a heartbeat during active assessments. Abrupt browser closure, network loss, sleep, or power loss is detected from missing heartbeats even when the browser cannot report the event. Submitted answers remain stored. The current server-side question timer continues, and secure resume requires the same browser profile plus camera reverification.

## Bundled face-monitoring assets

The repository includes `app/vendor/mediapipe-tasks-vision-assets.zip`. During startup, `app.prestart` extracts the required model and WebAssembly files to `/tmp/kanokware-mediapipe-tasks-vision`. This avoids missing nested binary files in GitHub deployments.
