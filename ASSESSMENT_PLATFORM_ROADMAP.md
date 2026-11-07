# Kanokware assessment platform, delivery and capacity plan

## Agreed product scope

Two first-class student entry paths must remain available:

1. **Submit coursework**: assignments, projects, dissertations or other supported
   documents, with document-grounded understanding assessment and optional oral evidence.
2. **Take a standalone assessment**: lecturer-authored question banks or approved
   questions prepared from lecturer sources. Candidates do not upload coursework.

The capacity target is **100,000 candidates taking assessments simultaneously**.
Proctoring is selected per examination or candidate group, not required for every
candidate. This target is not a measured or certified capacity of v1.4 or the current hosting.

## Delivered in the v1.4 foundation

- Both existing assessment entry paths remain. MCQ-only remains available.
- Roster-provisioned student activation and password sign-in, separate student
  sessions, course eligibility checks and account-recovery setup codes.
- Versioned reference photographs, institutional staff approval and an assessment
  capture for side-by-side review. No automated face recognition or voiceprints.
- Optional question read-aloud and recorded oral responses after MCQs, with a
  lecturer-defined count, browser recording guide and submission window.
- Approved typed alternatives, question-linked recordings, local browser drafts,
  retry-safe submission and staff-only audio playback. No automatic transcription.
- MCQ and oral marks remain separate. Oral marks use existing staff judgement.
- Thirty tiles are a monitor page size, not a candidate-admission ceiling.
- Optional proctor-group selection requires a roster. Mandatory administrator
  policies take precedence over group-specific settings.
- Paged student rosters and monitor listings, per-candidate admission locks,
  immutable shared exam banks at admission, and position-aware answer retries.

## Explicit release limits

This is a functional foundation, not an enterprise/high-stakes readiness claim.
The current supported timed question format remains single-answer, four-option
MCQ. Recorded oral verification is an asynchronous supplementary component.
Essay, numerical, multiple-answer and other native question engines require a
separate versioned question/response model and marking/release workflow.

The present media implementation stores bounded photos and audio in the database.
This is suitable only for a controlled pilot. Large-scale media collection must
move to private object storage with retention, quota and deletion policies.
Local audio drafts are not encrypted and should not be left on a shared device.
Recording time guidance is enforced in the browser, not proof of unassisted work.
An interrupted recording may lose its final unsaved chunk.

The existing dashboard uses status and retained verification pictures. It is not
a streaming-media infrastructure for 100,000 candidates. Physical devices,
remote environments and outside assistance cannot be fully controlled by a website.

## Required enterprise architecture before the target exam

| Layer | Required production capability |
| --- | --- |
| Frontend | CDN delivery, cached static assets, accessible low-bandwidth modes |
| Edge | WAF, DDoS protection, admission pacing, tested authentication rate limits |
| Assessment API | Stateless instances, bounded pools, per-attempt coordination, no shared bank writes |
| Database | Managed PostgreSQL, indexed candidate lookups, pooling, backups, failover and tested recovery |
| Coordination | Shared session/rate-limit services and durable exam-event handling |
| Workers | Durable queued preparation, marking, exports and transcription, not in-process background tasks |
| Media | Private object storage, scoped uploads, resumable transfers, quotas and retention jobs |
| Proctoring | Separate group-scoped event/media services and realistic staffing capacity |
| Operations | Metrics, alerting, incident command, audit access, support and examination runbooks |

Question generation and paper approval must finish before the timed examination.
The answer path must not depend on an AI provider being available. Grading and
reports should run asynchronously after durable answer receipt.

At 100,000 active candidates a five-second heartbeat alone means approximately
20,000 requests/second. Testing must include synchronised starts and submissions,
not just a count of open browser connections. Prepare minimum capacity ahead of
scheduled exams. Reactive autoscaling alone is not an examination-start strategy.

## High-stakes workflow still required

- Paper authoring, independent review, approval and immutable publication.
- Versioned question types and scoring rules with accessibility accommodations.
- Explicit identity alternatives, incident review and appeal processes.
- Controlled results release and separation of provisional and final marks.
- Independently reviewed security, privacy, retention and supplier arrangements.
- Database/media recovery drills, invigilator training and tested support escalation.

## Acceptance and load-validation gates

Use a dedicated, authorised staging environment with synthetic data. Do not load
test production or copy real student photographs, passwords or examination papers.

1. Functional, authorisation, migration, accessibility and retry tests.
2. PostgreSQL concurrency tests: duplicate starts, retry races, answer ordering,
   timeouts, row locking, reconnections and attempt resets.
3. Representative staged load at 1,000, 10,000, 25,000, 50,000, then 100,000 candidates.
4. Long-running tests for an entire exam, burst starts, burst final submission,
   dependency failures, worker restarts, database failover and network loss.
5. Reconcile server receipts with retained answers. No acknowledged answer may be
   missing or applied to another question or candidate.
6. Set and approve latency/error budgets before testing. Record build SHA,
   infrastructure, workload mix, timings, costs, failures and recovery results.
7. Independent go/no-go review before each larger rollout. A small functional
   test or a configurable target value is not evidence of 100,000-user capacity.

`loadtests/assessment.js` is a guarded baseline workload for an explicitly
authorised staging exam. It does not provision infrastructure, seed identities,
validate proctoring, or establish production readiness by itself.

## Infrastructure approval boundary

No new paid services or plan changes are implied by this source update. Provider
quotas, compute/database/media sizing, monitoring coverage and a cost ceiling must
be approved before provisioning and large distributed tests.
