# Phase 1 Learning Assurance Implementation

## Course configuration

Learning milestones and version history are independent optional settings.

- A lecturer may enable both.
- A lecturer may enable either one.
- A lecturer may disable both and use a final-only assessment.
- Final understanding justification and AI-use declaration remain compulsory.

## Phase 1 features

1. Optional learning milestones with lecturer-selected positive question counts.
2. Optional version history with version comparison.
3. Required student explanation of understanding, decisions, contribution, and limitation.
4. AI-use declaration and explanation.
5. Higher-order question categories and adaptive sequencing.
6. Lecturer intervention alerts.
7. Editable 100-point rubric-controlled marking.
8. Append-only mark-change audit history.
9. Course-level student learning progress dashboard.

## Database

Alembic revision: `20260717_07_phase1_learning_assurance.py`.

The normal prestart command applies the migration automatically.

## Verification completed

- Automated tests: 4 passed.
- Python compile check: passed.
- JavaScript syntax check: passed.
- Alembic upgrade from an empty database to the Phase 1 revision: passed.
