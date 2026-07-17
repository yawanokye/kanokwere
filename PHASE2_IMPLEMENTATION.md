# Phase 2 Learning Assurance Implementation

All Phase 2 modules are independent course options and are disabled by default for existing and newly created courses unless a lecturer enables them.

## Optional modules

1. **Oral defence**
   - Prepares document-specific viva questions.
   - Lets the lecturer score each response and record an overall outcome.
   - Question count is configurable per course.

2. **Practical reproduction tasks**
   - Creates applied tasks that require the student to reproduce, interpret, correct, or transfer part of the work.
   - The lecturer scores the task and records feedback.
   - Task count is configurable per course.

3. **Supervisor meeting records**
   - Records meeting date, mode, topics discussed, decisions, actions, and next meeting.
   - May be linked to a particular student submission.

4. **Cross-version analysis**
   - Compares consecutive submissions even when the standard version-history display is disabled.
   - Reports similarity, insertions, deletions, replacements, heading changes, reference indicators, and risk flags.

5. **Random moderation**
   - Selects a deterministic random percentage of marked assessments for moderation.
   - The sample percentage and acceptable marker–moderator variance are configurable.
   - Records the moderator score, decision, comments, and variance.

6. **Marker anomaly detection**
   - Identifies indicators such as unusual marker averages, repeated identical marks, rapid marking, and large mark changes.
   - Alerts require human review and do not establish wrongdoing automatically.

## Course flexibility

A lecturer can enable any combination of the six modules. Enabling one does not require enabling another. Final-only courses can keep every Phase 2 option disabled.

The Phase 1 requirement that students justify their understanding of a submitted work and declare AI use remains unchanged.

## Database

Alembic revision: `20260717_08_phase2_learning_assurance.py`.

The standard prestart command applies the migration automatically and preserves existing data.
