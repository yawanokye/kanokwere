# Phase 4: task governance and standalone examinations

Kanokware 1.2.0 separates the permanent basic workflow from optional assessment controls.

## Basic workflow

The following capabilities are always available and are not feature flags:

- course enrolment and task selection
- document upload and extraction
- understanding justification
- AI-use declaration
- document-grounded questions
- result and PDF report

## Optional feature governance

The platform administrator assigns every optional feature one activation mode:

| Mode | Effect |
|---|---|
| `disabled` | No task can use the feature. |
| `admin_only` | A system administrator must activate it for each task. |
| `lecturer_allowed` | An authorised lecturer or facilitator may activate it per task. |
| `always_on` | It is effective for every new task. |

Task settings affect new submissions and attempts. Each document and assessment stores an immutable JSON snapshot of its effective feature set.

## Task modes

`submission_assurance` retains the student-document workflow. `standalone_exam` lets the lecturer prepare one reusable examination source and lets candidates start with the course code, task, name, and candidate ID.

The standalone source can be a PDF, DOCX, or TXT file processed by Kanokware, or a lecturer-authored JSON bank. A new source record is created when a bank is replaced, preserving earlier candidates' question-level evidence.

## Attempt limits and reset

The lecturer sets `max_attempts` as any positive whole number when creating a course or task and can change a task limit later. Task attempts are counted case-insensitively by candidate ID across all document uploads and exam-source replacements for that task. This prevents a new upload or question bank from bypassing the configured limit.

After the limit is reached, the API returns `409` and the candidate interface releases camera, screen and audio capture, exits fullscreen, clears the unavailable assessment session, and returns to the start panel. A grading-authorised lecturer can reset all attempts for one candidate and task. Reset is destructive: answers and associated monitoring or recording evidence for those attempts are deleted, an audit entry is retained, and the candidate may then start afresh.

### Lecturer-authored bank format

The JSON root contains `questions`. Its length must equal the task's lecturer-selected question count; Kanokware does not impose an upper application limit.

```json
{
  "questions": [
    {
      "stem": "Which approved interpretation best matches the supplied examination evidence?",
      "options": ["Approved answer", "Distractor B", "Distractor C", "Distractor D"],
      "correct_option_index": 0,
      "difficulty": "understanding",
      "category": "interpretation",
      "seconds": 30,
      "source_quote": "A retained source reference or lecturer-approved evidence statement.",
      "source_location": "Section 2",
      "explanation": "Why the designated answer is correct."
    }
  ]
}
```

Allowed difficulties are `recall`, `understanding`, and `application`. Allowed categories are `document_knowledge`, `decision_defence`, `interpretation`, `counterfactual`, `error_correction`, and `transfer_application`.

## Secure browser behavior

Kanokware blocks framing with response headers. When `secure_browser_enforcement` is effective, the candidate must use fullscreen. Page hiding, minimizing, window focus loss, or fullscreen exit locks the assessment for lecturer review.

Web code cannot prevent operating-system minimization. Institutions that require true kiosk prevention must combine Kanokware with managed devices or a dedicated lockdown browser.

## Screen and environment recordings

`screen_activity_recording` and `environment_audio_recording` are independent task features. The browser requests the candidate's explicit screen-share or microphone permission before the attempt starts. MediaRecorder produces 30-second chunks, each limited to 12 MB, and sends them to an authenticated assessment endpoint. Only course-authorised lecturers can download the chunks.

Recordings must be governed by institutional consent, retention, accommodation, security, and due-process policies. They support contextual review and do not prove cheating.

## Main endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/platform/features` | List feature policies. |
| `PATCH /api/platform/features/{key}` | Change a platform activation mode. |
| `PATCH /api/platform/assessment-tasks/{id}/features` | Apply administrator-controlled task features. |
| `POST /api/lecturer/courses/{id}/tasks` | Create a task and select lecturer-permitted features. |
| `PATCH /api/lecturer/courses/{id}/tasks/{task_id}` | Update a task or its feature selections. |
| `DELETE /api/lecturer/courses/{id}/tasks/{task_id}/attempts?candidate_id=...` | Reset one candidate's complete attempt history for a task. |
| `POST /api/lecturer/courses/{id}/tasks/{task_id}/exam-source` | Upload a source for generated exam questions. |
| `POST /api/lecturer/courses/{id}/tasks/{task_id}/question-bank` | Upload lecturer-authored JSON questions. |
| `POST /api/examinations/start` | Start a standalone candidate attempt. |
| `POST /api/assessments/{id}/terminate` | Lock a secure attempt after a browser-rule breach. |
| `POST /api/assessments/{id}/recording-chunk` | Store consented screen or audio evidence. |

Database revision: `20260901_10_task_feature_governance.py`.
