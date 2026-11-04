# Kanokware 1.3.0

**Verify learning in the age of AI.**

Kanokware links student submissions to lecturer-owned courses and tests whether students can explain, defend, apply, and reflect on the work submitted under their names. The platform supports final-only assignments as well as optional learning-process monitoring.

## Phase 1 learning assurance

### Optional learning milestones

A lecturer chooses whether a task will use learning milestones. When enabled, the lecturer can create checkpoints such as problem definition, methodology, analysis, or draft development. The lecturer sets any positive whole-number question count. When disabled, students submit only the final work.

### Optional version history

A lecturer independently chooses whether Kanokware should link consecutive submissions as versions. When enabled, the lecturer sees version numbers, word-count changes, similarity, and major changes. When disabled, each upload is treated as a standalone submission.

### Understanding justification required for every submission

Whether milestones and version history are enabled or not, every student must explain:

- their understanding of the work
- the main decisions they made
- the contribution or value of the work
- an important limitation
- whether and how AI was used

This ensures that final-only assignments still contain evidence of the student's own understanding.

### Higher-order and adaptive questions

Question banks now cover document knowledge, decision defence, interpretation, counterfactual reasoning, error correction, and transfer or application. The assessment sequence adapts after each response by moving suitable higher-order questions forward.

### Lecturer intervention alerts

Kanokware identifies matters that may require lecturer attention, including weak mastery, missing expected milestones, major version changes, substantial declared AI use, critical camera-monitoring events, and repeated assessment interruptions. Alerts support review and do not automatically establish misconduct.

### Rubric-controlled marking

Each course receives a 100-point learning-assurance rubric. Lecturers can edit the rubric, score every criterion, add comments, and save an overall mark. A mark cannot be entered without completing all criteria.

### Mark-change audit trail

Every saved or changed rubric mark creates an audit record. Any change to an existing mark requires a reason and retains the previous and new totals.

### Student learning progress

The lecturer dashboard summarises each student's milestones, versions, assessment performance, unresolved alerts, and final-submission status.

## Existing assessment controls

- Course enrolment codes link students to the correct lecturer or supervision group.
- Lecturers choose any positive whole-number question count for courses, tasks, milestones, standalone exams, and oral defences. Kanokware does not impose an application ceiling.
- Every question has 30 seconds.
- Questions and options are randomised.
- Correct answers remain server-side.
- Webcam monitoring, monitoring warnings, snapshots, and interruption recovery are supported.
- Lecturer and platform-administrator accounts use role-based access.
- PDF learning-assurance reports are available.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python prestart.py
uvicorn main:app --reload
```

On Windows, activate the environment with `.venv\Scripts\activate`.

## Render deployment

Use the supplied Blueprint or this Start Command:

```bash
python prestart.py && uvicorn main:app --host 0.0.0.0 --port $PORT
```

The prestart step verifies face-monitoring assets, repairs known legacy schema gaps, and applies Alembic migrations. The Phase 1 migration preserves existing lecturer accounts, courses, submissions, assessments, reports, and monitoring evidence.

Key production variables include:

```env
ENVIRONMENT=production
DATABASE_URL=provided-by-render-postgres
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.5
ADMIN_KEY=your-private-platform-key
PASS_THRESHOLD=80
QUESTION_TIME_SECONDS=30
WEBCAM_REQUIRED=true
HEARTBEAT_INTERVAL_SECONDS=5
HEARTBEAT_STALE_SECONDS=20
ASSESSMENT_RESUME_WINDOW_MINUTES=15
MAX_ASSESSMENT_INTERRUPTION_COUNT=2
MAX_ASSESSMENT_OFFLINE_SECONDS=300
```

## Tests

```bash
pytest -q
python -m compileall -q app alembic
node --check app/static/app.js
```

## Phase 2 optional assurance modules

Every Phase 2 module is independently configurable per course and defaults to **Off**:

- **Oral defence:** prepare and score a document-specific viva.
- **Practical reproduction tasks:** require the student to reproduce, interpret, correct, or apply part of the work.
- **Supervisor meeting records:** document guidance, decisions, agreed actions, and follow-up dates.
- **Cross-version analysis:** compare consecutive submissions and identify material changes or risk indicators.
- **Random moderation:** select a configurable percentage of marked assessments for a second review.
- **Marker anomaly detection:** flag unusual marking patterns for authorised human review.

A lecturer may enable any combination, including none. Final-only assignments remain supported. The required student understanding justification and AI-use declaration introduced in Phase 1 remain in force.

Database revision: `20260717_08_phase2_learning_assurance.py`. No new environment variable is required.

## Phase 3 assessment evidence profile

Phase 3 addresses the assessment problem created by widespread generative-AI use without treating automated indicators as proof of misconduct.

- Every course states one student-facing AI rule: prohibited, restricted, permitted with disclosure, or required.
- Honest declarations that conflict with a course rule are accepted and routed to human review rather than blocked.
- The lecturer review combines document knowledge, independent reasoning, application, AI-policy alignment, and session evidence.
- The evidence profile recommends proportionate verification while retaining a clear due-process disclaimer.
- Lecturers record a reasoned human decision: evidence consistent, additional viva, supplementary task, academic-integrity review, or unable to determine.
- Courses may enable a live examination dashboard that refreshes active connection, question progress, camera state, interruption and monitoring indicators every five seconds.
- The PDF learning-assurance report includes the combined evidence profile and lecturer decision.

Database revision: `20260831_09_assessment_evidence_profile.py`. The normal prestart migration preserves existing records.

## Phase 4 task governance and standalone examinations

New assessment tasks start with Kanokware's basic workflow: enrolment, document upload, required understanding justification, AI declaration, grounded questions, result, and PDF report. Advanced controls are selected per task.

The platform administrator sets one rule for each advanced feature:

- `disabled`
- `admin_only`
- `lecturer_allowed`
- `always_on`

Lecturers and facilitators can then activate permitted features for a particular task. The effective feature profile is snapshotted onto each submission and assessment so later policy changes do not alter an attempt already in progress. Existing courses without task profiles retain their earlier course-level behaviour.

Standalone examination tasks support either:

- a lecturer-uploaded PDF, DOCX, or TXT source from which Kanokware generates the configured number of grounded questions; or
- a lecturer-authored JSON question bank containing exactly the task's configured number of questions.

Optional examination evidence includes webcam monitoring, a live dashboard, secure fullscreen enforcement, consented screen recording, and consented environment-audio recording. Browser framing is blocked with `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'`. A normal website cannot prevent the operating system from minimizing a browser; when secure enforcement is enabled Kanokware detects page hiding, focus loss, or fullscreen exit and locks the attempt for authorised review. Managed kiosk prevention still requires an institutional lockdown browser or device policy.

Each task has a lecturer-set positive whole-number attempt limit. Attempts are counted per candidate and task across replacement uploads or exam banks, so creating a new source does not bypass the limit. When the limit is reached, the candidate app returns to its start screen and explains that a lecturer must increase the limit or reset the candidate. Authorised lecturers can change a task limit or reset one candidate's complete task-attempt history so the candidate can start afresh.

Screen and audio recordings are uploaded as access-controlled chunks and are indicators for human review, not automatic misconduct findings. Institutions should configure consent, retention, accessibility accommodations, storage capacity, and due-process rules before enabling recording.

Database revision: `20260901_10_task_feature_governance.py`. No new environment variable is required.

## Phase 5 live proctored examination operations

Standalone exams can now use a scheduled opening and closing window, an overall duration, a late-entry window, and a lecturer-uploaded candidate roster. The reorganised lecturer workspace includes a separate Exam Eligibility Roster panel, where the lecturer selects a particular examination, downloads a CSV template, uploads eligible students, and reviews active or inactive records. Roster admission verifies the candidate ID and an individually hashed access PIN. The optional `extra_time_minutes` roster column applies a candidate accommodation without changing the task for everyone else. The optional `monitor_group` column places candidates into named groups that can be assigned to different remote monitors.

Candidates must pass a browser and device-readiness check before starting. The check covers top-level page use, network state, secure browser capabilities, and the APIs required by the task's enabled camera, screen, and audio controls.

The live invigilation dashboard now lets an authorised lecturer warn a candidate, pause and resume the clocks, add time, or end and lock an attempt. The candidate receives interventions through the assessment heartbeat even when webcam monitoring is off. Every action is added to the monitoring and audit trail for later review.

Remote invigilation supports up to 30 active candidate sessions for an examination. The lecturer sees all active candidates in a numbered tile wall, including connection, progress, camera state, monitor group and movement warnings, with direct Comment and Review actions. Course owners and institution administrators can see the full examination. When group assignments exist, other authorised monitors see only their assigned candidate groups. When a candidate leaves a resumable session, the visible question is moved to the end of the remaining sequence and unseen questions are reshuffled. The next question is different when a replacement exists. If no replacement remains, the visible question stays as the final question. Time already spent on it is retained, and the event is written to the monitoring trail.

Candidate-side movement and presence indicators create an on-screen and spoken warning. The same condition is highlighted on the candidate's remote-monitor tile. An invigilator comment is delivered on screen and by browser speech, and retained in the incident record.

An optional examination-venue geofence can require a candidate to be within a lecturer-set radius before starting. It uses the browser's high-accuracy device location, so no Google Maps API key is required. Kanokware checks the reported accuracy and stores the verified distance and accuracy on the attempt, not the candidate's raw live coordinates. Geolocation can be unavailable or spoofed, so use it as an admission control and review indicator, not as sole proof of identity or misconduct. Institutions should provide an alternative process for candidates whose devices, accessibility needs, or privacy rules prevent location use.

Database revision: `20261104_11_live_exam_operations.py`. No new environment variable is required.
