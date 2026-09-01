# Phase 3 Assessment Evidence Profile

## Purpose

Phase 3 changes Kanokware from a document-ownership score into a broader assessment-assurance workflow. Automated results remain indicators. Academic decisions remain human, reasoned, recorded, and subject to institutional due process.

## Course AI-use rules

Lecturers select one rule for each course:

1. AI use prohibited.
2. Restricted AI support.
3. AI permitted with disclosure.
4. AI use required as part of learning.

Lecturers may add student-facing guidance. Students see the rule before upload. The system accepts honest declarations even when they conflict with the rule and creates a review alert instead of an automatic misconduct finding.

## Assessment evidence profile

The lecturer review brings together:

- document-specific knowledge;
- independent reasoning through oral defence when enabled;
- application and transfer through practical tasks when enabled;
- AI-policy alignment;
- contextual session and interruption indicators.

The profile recommends one of four system-level next steps: evidence consistent, evidence incomplete, additional verification, or human review. The recommendation is advisory.

## Human verification decision

The lecturer records one final verification decision with a reasoned note:

- available evidence is consistent;
- require an additional viva;
- require a supplementary task;
- refer for academic-integrity review;
- unable to determine.

The decision and note are retained in the assessment evidence profile and PDF report.

## Optional live examination monitoring

A lecturer may enable live examination monitoring for a course and provide a candidate-facing examination title and instructions. The dashboard refreshes every five seconds and shows:

- connected and interrupted candidates;
- current question progress and completion status;
- camera snapshot state;
- total, unresolved, and critical monitoring indicators;
- interruption count and recorded offline time;
- a direct link to the full assessment review and recovery controls.

The live dashboard uses existing secure heartbeats and monitoring events. It does not make an automatic misconduct decision.

## Database

Alembic revision: `20260831_09_assessment_evidence_profile.py`.

The standard prestart command applies the migration and preserves existing data.
