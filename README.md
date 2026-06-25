# Kanokwere MVP

**Know your work. Prove your work.**

Kanokwere is a document-ownership assessment application. A student uploads a PDF, DOCX, or TXT document. The app generates 20 questions grounded in that document, administers them one at a time with server-enforced 10–15 second limits, and calculates an ownership confidence score. The default threshold is 80%, which requires at least 16 correct answers.

## Included in this MVP

- PDF, DOCX, and TXT upload
- 15 MB default upload limit
- Original file processed in memory and not stored
- Extracted-text readability check
- AI-generated bank of exactly 20 questions
- Exact source-passage grounding validation
- Required mix of 6 recall, 8 understanding, and 6 application questions
- Randomised question order and answer-option order
- Correct answers never sent to the student browser
- Server-side timing enforcement
- Focus-loss logging
- One assessment attempt per document by default, with lecturer-controlled reset
- Student result screen
- Lecturer dashboard protected by an administrator key
- Question-level evidence review
- PDF ownership-assessment report
- PostgreSQL production support and SQLite local support
- Render Blueprint deployment file

## Important interpretation rule

Kanokwere reports whether a student demonstrated knowledge of a submitted document. It does not conclusively prove authorship and must not automatically determine academic misconduct. Scores below the threshold should trigger oral or manual verification and normal institutional due process.

## Run locally

1. Create and activate a virtual environment.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS or Linux:

```bash
source .venv/bin/activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env`, then set `OPENAI_API_KEY` and a strong `ADMIN_KEY`.

4. Export the variables or load them through your preferred environment manager. For a quick local run, variables can be set in the shell before starting the app.

5. Start the service.

```bash
uvicorn main:app --reload
```

6. Open `http://127.0.0.1:8000`.

## AI and demo modes

Production question generation uses the OpenAI Responses API with structured Pydantic output. Set:

```text
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.5
ALLOW_DEMO_QUESTIONS=false
```

When `ALLOW_DEMO_QUESTIONS=true` and no API key is present, the app can create deterministic fill-in-the-blank questions so the interface and workflow can be tested. Demo questions must not be used for real ownership decisions.

## Deploy on Render

1. Push this folder to a GitHub repository.
2. In Render, choose **New > Blueprint**.
3. Select the repository containing `render.yaml`.
4. Provide the `OPENAI_API_KEY` secret when prompted.
5. After deployment, copy the generated `ADMIN_KEY` from the web service environment settings and keep it secure.

The Blueprint creates a FastAPI web service and a PostgreSQL database. The health-check path is `/health`.

## Core API routes

```text
POST   /api/documents
GET    /api/documents/{document_id}/status
POST   /api/assessments/start
GET    /api/assessments/{assessment_id}/question
POST   /api/assessments/{assessment_id}/answer
POST   /api/assessments/{assessment_id}/focus-event
GET    /api/assessments/{assessment_id}/result
GET    /api/admin/submissions
GET    /api/admin/assessments/{assessment_id}
GET    /api/admin/assessments/{assessment_id}/report.pdf
DELETE /api/admin/assessments/{assessment_id}
DELETE /api/admin/documents/{document_id}
```

## Data retained

The MVP does not save the original uploaded file. It stores:

- Student name and identifier
- Document title and original filename
- SHA-256 file fingerprint
- Extracted document text
- Generated questions and source passages
- Assessment responses and timing
- Score, decision, and focus-loss count

The lecturer dashboard includes permanent deletion of the document record and all linked questions, attempts, and reports.

## Production hardening before institutional use

The MVP intentionally keeps deployment simple. Before wide institutional rollout, add:

- Institution, lecturer, and student accounts with role-based access control
- Email verification or university single sign-on
- Explicit privacy notice, consent text, retention periods, and data-processing agreements
- Encrypted object storage if original files are ever retained
- A durable queue and worker for question generation
- Rate limiting, audit logs, and security monitoring
- Database migrations using Alembic
- Accessibility accommodations authorised by lecturers
- Question-bank moderation and lecturer approval controls
- Independent legal and institutional ethics review

## Tests

```bash
pytest -q
```

The end-to-end test uploads a document, generates a 20-question demo bank, completes all questions, verifies a 100% score, and creates a PDF report.
