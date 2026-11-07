// k6 baseline for an AUTHORISED STAGING EXAM ONLY, never production.
// Pre-create a synthetic MCQ exam with auth, proctoring and roster admission off.
// Supply STAGING_URL, STAGING_HOST, COURSE_CODE, TASK_ID and RUN_ID through env.
// Default workload is ten virtual candidates. Large runs need separate approval.
import http from 'k6/http';
import exec from 'k6/execution';
import { check, sleep } from 'k6';

const base = (__ENV.STAGING_URL || '').replace(/\/$/, '');
const targetHost = base.replace(/^https?:\/\//, '').split('/')[0].toLowerCase();
if (__ENV.ALLOW_LOAD_TEST !== 'AUTHORIZED_STAGING_ONLY' || !base ||
    !__ENV.STAGING_HOST || targetHost !== __ENV.STAGING_HOST.toLowerCase() ||
    /(^|\.)kanokware\.app(?::\d+)?$/.test(targetHost) ||
    targetHost === 'kanokwere.onrender.com' || !__ENV.COURSE_CODE || !__ENV.TASK_ID || !__ENV.RUN_ID) {
  throw new Error('Use an explicitly authorised staging host and complete synthetic exam settings. Production is prohibited.');
}
const target = Number(__ENV.TARGET_VUS || 10);
if (!Number.isInteger(target) || target < 1 || target > 100000) throw new Error('Invalid candidate load.');
export const options = {
  stages: [{ duration: __ENV.RAMP || '30s', target }, { duration: __ENV.HOLD || '2m', target }, { duration: '30s', target: 0 }],
  thresholds: { http_req_failed: ['rate<0.01'], checks: ['rate>0.99'] },
};
const instance = () => `load-${__ENV.RUN_ID}-${exec.vu.idInTest}-${exec.vu.iterationInScenario}`;
export default function () {
  const client = instance();
  const started = http.post(`${base}/api/examinations/start`, JSON.stringify({
    course_code: __ENV.COURSE_CODE, task_id: __ENV.TASK_ID,
    candidate_name: 'Synthetic load candidate', candidate_id: client,
    readiness_confirmed: true, client_instance_id: client,
  }), { headers: { 'Content-Type': 'application/json' }, tags: { operation: 'admission' } });
  if (!check(started, { 'admitted': (r) => r.status === 200 })) { sleep(5); return; }
  const attempt = started.json();
  const headers = { Authorization: `Bearer ${attempt.session_token}`, 'Content-Type': 'application/json' };
  for (let index = 0; index < attempt.question_count; index += 1) {
    const question = http.get(`${base}/api/assessments/${attempt.assessment_id}/question`, { headers, tags: { operation: 'question' } });
    if (!check(question, { 'question received': (r) => r.status === 200 })) break;
    const item = question.json();
    if (item.status === 'completed') break;
    // Approximate a ten-second answer, with five-second continuity heartbeats.
    for (let tick = 0; tick < 2; tick += 1) {
      sleep(5);
      const beat = http.post(`${base}/api/assessments/${attempt.assessment_id}/heartbeat`, JSON.stringify({
        client_instance_id: client, camera_verified: false, reason: 'periodic',
      }), { headers, tags: { operation: 'heartbeat' } });
      check(beat, { 'heartbeat received': (r) => r.status === 200 });
    }
    const answer = http.post(`${base}/api/assessments/${attempt.assessment_id}/answer`, JSON.stringify({
      selected_index: 0, question_position: item.position,
    }), { headers, tags: { operation: 'answer' } });
    if (!check(answer, { 'answer acknowledged': (r) => r.status === 200 })) break;
  }
  const result = http.get(`${base}/api/assessments/${attempt.assessment_id}/result`, { headers, tags: { operation: 'result' } });
  check(result, { 'completed result received': (r) => r.status === 200 });
  sleep(5);
}
