# Upgrade notes: multi-lecturer accounts

## Replace the existing repository

Commit the contents of this package to the same GitHub repository used by Render. Keep the existing PostgreSQL database attached.

The new Render start command is:

```bash
python -m app.prestart && uvicorn main:app --host 0.0.0.0 --port $PORT
```

`app.prestart` creates the new account and course tables, then runs the Alembic migration that adds nullable institution, course, and lecturer fields to existing submissions. Existing documents and assessments are not deleted.

## Required Render variables

```env
APP_NAME=Kanokwere
ENVIRONMENT=production
LECTURER_SESSION_HOURS=12
LOGIN_MAX_FAILURES=5
LOGIN_LOCK_MINUTES=15
LECTURER_REGISTRATION_ENABLED=true
```

Keep the existing variables, including `DATABASE_URL`, `OPENAI_API_KEY`, `ADMIN_KEY`, `PASS_THRESHOLD`, `QUESTION_TIME_SECONDS`, and webcam settings.

## First use

1. Deploy the updated project.
2. Open the Lecturer tab and register the first lecturer.
3. In the Platform administration section, enter the Render `ADMIN_KEY`.
4. Approve the first lecturer as `institution_admin`.
5. Sign in with that lecturer account.
6. Create a course and share its `KANO-...` enrolment code with students.
7. Students must enter the code when uploading their documents.

## Existing submissions

Submissions created before this update have no course assignment. They remain available through the legacy platform administrator API, but they are not displayed in lecturer dashboards. New submissions are always linked to a course.
