function getOrCreateClientInstanceId() {
  const key = "kanokware_client_instance_id";
  let value = localStorage.getItem(key);
  if (!value) {
    value = globalThis.crypto?.randomUUID?.() || `kano-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(key, value);
  }
  return value;
}

const state = {
  documentId: localStorage.getItem("kanokware_document_id"),
  assessmentId: localStorage.getItem("kanokware_assessment_id") || sessionStorage.getItem("kanokware_assessment_id"),
  token: localStorage.getItem("kanokware_session_token") || sessionStorage.getItem("kanokware_session_token"),
  clientInstanceId: getOrCreateClientInstanceId(),
  testActive: false,
  timerHandle: null,
  currentPosition: null,
  lecturerUser: null,
  courses: [],
  platformKey: sessionStorage.getItem("kanokware_platform_key") || "",
  platformAuthenticated: false,
  cameraStream: null,
  snapshotCaptured: false,
  snapshotPromise: null,
  snapshotTimer: null,
  captureScheduledForCurrentQuestion: false,
  snapshotUrls: [],
  assessmentQuestionCount: 20,
  faceDetector: null,
  faceDetectorPromise: null,
  tasksVisionModulePromise: null,
  lastDetectionTimestampMs: 0,
  monitoringTimer: null,
  monitoringBusy: false,
  monitoringStates: {},
  monitoringEventCount: 0,
  monitoringSupported: true,
  monitoringEngine: null,
  monitoringWatchdogTimer: null,
  monitoringFlushTimer: null,
  monitoringEventQueue: [],
  latestFaceDetections: [],
  monitoringResultVersion: 0,
  lastMonitoringResultAt: 0,
  detectionFailureCount: 0,
  lastVideoCurrentTime: null,
  videoFrozenSince: null,
  lastFrameSignature: null,
  lastFaceMotionSample: null,
  faceMotionHistory: [],
  heartbeatTimer: null,
  heartbeatBusy: false,
  heartbeatIntervalMs: 5000,
  connectionStatus: navigator.onLine ? "online" : "offline",
  offlineStartedAt: null,
  resumeInProgress: false,
  currentReviewAssessmentId: null,
  currentReviewSummary: null,
  recoveryDbPromise: null,
  resolvedCourse: null,
  selectedTask: null,
  monitoringEnabled: false,
  secureBrowserEnforcement: false,
  secureBrowserArmed: false,
  activeFeatures: {},
  screenCaptureStream: null,
  environmentAudioStream: null,
  evidenceRecorders: [],
  recordingSequences: { screen: 0, environment_audio: 0 },
  recordingUploads: new Set(),
  currentReviewData: null,
  currentAnomalyCourseId: null,
  liveMonitorCourseId: null,
  liveMonitorTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const MONITORING_ASSET_STATUS_URL = "/api/monitoring/assets-status";
const MEDIAPIPE_TASKS_BASES = [
  "/monitoring-assets",
  "/static/vendor/mediapipe-tasks-vision",
];
const FACE_DETECTION_MAX_FAILURES = 5;
const MONITOR_INTERVAL_MS = 350;
const MONITOR_RESULT_TIMEOUT_MS = 2600;
const MONITOR_PREFLIGHT_TIMEOUT_MS = 12000;
const MONITOR_QUEUE_LIMIT = 80;
const RECOVERY_DB_NAME = "kanokware-assessment-recovery";
const RECOVERY_DB_VERSION = 1;
const RECOVERY_STORE = "monitoring-events";

function setMessage(element, text = "", type = "") {
  element.textContent = text;
  element.className = "message";
  if (text) element.classList.add("visible");
  if (type) element.classList.add(type);
}

function showView(name) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$("[data-view-link]").forEach((button) => button.classList.toggle("active", button.dataset.viewLink === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showPanel(name, step) {
  $$(".workflow-shell .panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${name}`));
  $$(".step").forEach((item) => {
    const itemStep = Number(item.dataset.step);
    item.classList.toggle("active", itemStep === step);
    item.classList.toggle("complete", itemStep < step);
  });
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...options });
  } catch (cause) {
    const error = new Error("The server could not be reached. Check the internet connection.");
    error.networkError = true;
    error.cause = cause;
    throw error;
  }
  let payload = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) payload = await response.json();
  if (!response.ok) {
    const message = payload?.detail || payload?.message || `Request failed with status ${response.status}.`;
    const error = new Error(typeof message === "string" ? message : JSON.stringify(message));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function assessmentHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${state.token}`,
  };
}

function saveAssessmentSession() {
  if (!state.assessmentId || !state.token) return;
  localStorage.setItem("kanokware_assessment_id", state.assessmentId);
  localStorage.setItem("kanokware_session_token", state.token);
  sessionStorage.setItem("kanokware_assessment_id", state.assessmentId);
  sessionStorage.setItem("kanokware_session_token", state.token);
}

function clearAssessmentSession() {
  localStorage.removeItem("kanokware_assessment_id");
  localStorage.removeItem("kanokware_session_token");
  sessionStorage.removeItem("kanokware_assessment_id");
  sessionStorage.removeItem("kanokware_session_token");
  state.assessmentId = null;
  state.token = null;
}

function isAttemptLimitError(error) {
  return Boolean(
    error?.status === 409 && /maximum[\s\S]*attempt|attempt[\s\S]*reached/i.test(error.message || "")
  );
}

async function returnToStartModeAfterAttemptLimit(message) {
  state.testActive = false;
  state.secureBrowserArmed = false;
  state.connectionStatus = navigator.onLine ? "online" : "offline";
  clearQuestionTimer();
  clearHeartbeatTimer();
  clearSnapshotTimer();
  stopSuspiciousMonitoring();
  stopCamera();
  await stopEvidenceRecordings();
  clearAssessmentSession();
  state.documentId = null;
  state.currentPosition = null;
  localStorage.removeItem("kanokware_document_id");
  $("#consent-checkbox").checked = false;
  $("#start-button").disabled = true;
  showPanel("upload", 1);
  document.exitFullscreen?.().catch(() => {});
  setMessage($("#mode-warning"));
  if (state.selectedTask?.mode === "standalone_exam") {
    setMessage($("#standalone-exam-message"), message, "error");
  }
  setMessage(
    $("#upload-message"),
    `${message} Ask the lecturer to increase the permitted attempts or reset your task attempts before starting again.`,
    "error"
  );
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds || 0)));
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function setConnectionOverlay(mode, payload = {}) {
  const overlay = $("#connection-overlay");
  if (!overlay) return;
  if (mode === "online") {
    overlay.classList.add("hidden");
    overlay.classList.remove("locked", "reconnecting");
    return;
  }
  overlay.classList.remove("hidden", "locked", "reconnecting");
  if (mode === "locked") overlay.classList.add("locked");
  if (mode === "reconnecting") overlay.classList.add("reconnecting");
  const title = $("#connection-title");
  const text = $("#connection-text");
  const chip = $("#connection-status-chip");
  const retry = $("#retry-connection-button");
  const interruptionCount = Number(payload.interruption_count || 0);
  const totalOffline = Number(payload.total_offline_seconds || 0) + Number(payload.current_offline_seconds || 0);
  if ($("#connection-interruptions")) $("#connection-interruptions").textContent = interruptionCount;
  if ($("#connection-offline-time")) $("#connection-offline-time").textContent = formatDuration(totalOffline);
  if ($("#connection-resume-time")) {
    $("#connection-resume-time").textContent = payload.resume_seconds_remaining == null
      ? "—"
      : formatDuration(payload.resume_seconds_remaining);
  }
  if (mode === "locked") {
    chip.textContent = "Locked";
    title.textContent = "Assessment locked for lecturer review";
    text.textContent = payload.lock_reason
      ? payload.lock_reason.replaceAll("_", " ")
      : "The permitted interruption or offline limit was exceeded.";
    retry.classList.add("hidden");
  } else if (mode === "reconnecting") {
    chip.textContent = "Reconnecting";
    title.textContent = "Restoring the assessment";
    text.textContent = "Checking the connection and reverifying the camera before continuing.";
    retry.classList.add("hidden");
  } else {
    chip.textContent = "Interrupted";
    title.textContent = "Connection interrupted";
    text.textContent = "Answers are temporarily disabled. The server-side question clock continues while Kanokware attempts to reconnect.";
    retry.classList.remove("hidden");
  }
}

function openRecoveryDb() {
  if (state.recoveryDbPromise) return state.recoveryDbPromise;
  state.recoveryDbPromise = new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) {
      resolve(null);
      return;
    }
    const request = indexedDB.open(RECOVERY_DB_NAME, RECOVERY_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(RECOVERY_STORE)) {
        const store = db.createObjectStore(RECOVERY_STORE, { keyPath: "id" });
        store.createIndex("assessment_id", "assessment_id", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  }).catch(() => null);
  return state.recoveryDbPromise;
}

async function persistMonitoringPayload(payload) {
  const db = await openRecoveryDb();
  if (!db || !state.assessmentId) return;
  const record = {
    ...payload,
    id: payload._queue_id,
    assessment_id: state.assessmentId,
    created_at: Date.now(),
  };
  await new Promise((resolve) => {
    const tx = db.transaction(RECOVERY_STORE, "readwrite");
    tx.objectStore(RECOVERY_STORE).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

async function deletePersistedMonitoringPayload(id) {
  const db = await openRecoveryDb();
  if (!db || !id) return;
  await new Promise((resolve) => {
    const tx = db.transaction(RECOVERY_STORE, "readwrite");
    tx.objectStore(RECOVERY_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

async function loadPersistedMonitoringPayloads() {
  const db = await openRecoveryDb();
  if (!db || !state.assessmentId) return [];
  return new Promise((resolve) => {
    const tx = db.transaction(RECOVERY_STORE, "readonly");
    const index = tx.objectStore(RECOVERY_STORE).index("assessment_id");
    const request = index.getAll(state.assessmentId);
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => resolve([]);
  });
}

function cameraIsVerified() {
  const track = state.cameraStream?.getVideoTracks?.()[0];
  return Boolean(
    track?.readyState === "live" &&
    state.monitoringSupported &&
    state.lastMonitoringResultAt > 0 &&
    performance.now() - state.lastMonitoringResultAt < MONITOR_RESULT_TIMEOUT_MS
  );
}

function clearHeartbeatTimer() {
  if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
  state.heartbeatTimer = null;
}

async function sendHeartbeat({ cameraVerified = false, reason = "periodic" } = {}) {
  if (!state.assessmentId || !state.token || state.heartbeatBusy) return null;
  state.heartbeatBusy = true;
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/heartbeat`, {
      method: "POST",
      headers: assessmentHeaders(),
      body: JSON.stringify({
        client_instance_id: state.clientInstanceId,
        camera_verified: Boolean(cameraVerified),
        reason,
      }),
    });
    state.connectionStatus = result.status;
    const interval = Number(result.heartbeat_interval_seconds || 5);
    state.heartbeatIntervalMs = Math.max(3000, interval * 1000);
    return result;
  } finally {
    state.heartbeatBusy = false;
  }
}

function startHeartbeatLoop() {
  clearHeartbeatTimer();
  state.heartbeatTimer = window.setInterval(async () => {
    if (!state.testActive || !state.assessmentId || !state.token) return;
    try {
      const result = await sendHeartbeat({ cameraVerified: false, reason: "periodic" });
      if (!result) return;
      if (result.status === "locked") {
        disableOptions();
        setConnectionOverlay("locked", result);
      } else if (result.status === "interrupted") {
        disableOptions();
        setConnectionOverlay("interrupted", result);
      }
    } catch (error) {
      if (error.networkError) markConnectionInterrupted("network_failure");
    }
  }, state.heartbeatIntervalMs);
}

function markConnectionInterrupted(reason = "network_failure") {
  if (!state.testActive) return;
  if (!state.offlineStartedAt) state.offlineStartedAt = Date.now();
  state.connectionStatus = "offline";
  disableOptions();
  setConnectionOverlay("interrupted", {
    interruption_count: 0,
    current_offline_seconds: Math.floor((Date.now() - state.offlineStartedAt) / 1000),
  });
  queueMonitoringPayload({
    event_type: "connection_interrupted",
    duration_ms: 0,
    question_position: state.currentPosition || null,
    severity: "critical",
    corrected: false,
    message: reason === "offline"
      ? "The browser reported that the internet connection was lost."
      : "The assessment could not communicate with the server.",
  });
}

async function recoverAssessment(reason = "reconnect") {
  if (!state.assessmentId || !state.token || state.resumeInProgress) return;
  state.resumeInProgress = true;
  setConnectionOverlay("reconnecting");
  try {
    await startCamera();
    await runMonitoringPreflight();
    await startSuspiciousMonitoring();
    const result = await sendHeartbeat({ cameraVerified: true, reason });
    if (!result) throw new Error("The assessment state could not be restored.");
    if (result.status === "locked") {
      state.connectionStatus = "locked";
      disableOptions();
      setConnectionOverlay("locked", result);
      return;
    }
    if (result.status === "completed") {
      setConnectionOverlay("online");
      await loadResult();
      return;
    }
    if (result.status !== "in_progress") {
      setConnectionOverlay("interrupted", result);
      return;
    }
    state.connectionStatus = "online";
    state.offlineStartedAt = null;
    setConnectionOverlay("online");
    await flushMonitoringEventQueue();
    startHeartbeatLoop();
    await loadQuestion();
  } catch (error) {
    if (error.status === 401 || error.status === 404) {
      clearAssessmentSession();
      state.testActive = false;
      stopCamera();
      await stopEvidenceRecordings();
      showPanel("upload", 1);
      setMessage($("#upload-message"), "The saved assessment session is no longer available. Contact the lecturer if another attempt is required.", "error");
      return;
    }
    markConnectionInterrupted("network_failure");
    setConnectionOverlay("interrupted");
  } finally {
    state.resumeInProgress = false;
  }
}

async function reportExplicitInterruption(reason = "pagehide") {
  if (!state.testActive || !state.assessmentId || !state.token) return;
  try {
    await fetch(`/api/assessments/${state.assessmentId}/interrupt`, {
      method: "POST",
      headers: assessmentHeaders(),
      body: JSON.stringify({
        client_instance_id: state.clientInstanceId,
        reason,
      }),
      credentials: "same-origin",
      keepalive: true,
    });
  } catch (_) {
    // Missing heartbeats will still identify the interruption on the server.
  }
}

async function terminateForSecureBrowser(reason) {
  if (!state.testActive || !state.secureBrowserEnforcement || !state.secureBrowserArmed) return;
  state.secureBrowserArmed = false;
  disableOptions();
  try {
    const response = await fetch(`/api/assessments/${state.assessmentId}/terminate`, {
      method: "POST",
      headers: assessmentHeaders(),
      body: JSON.stringify({ client_instance_id: state.clientInstanceId, reason }),
      credentials: "same-origin",
      keepalive: true,
    });
    const payload = await response.json().catch(() => ({}));
    state.connectionStatus = "locked";
    setConnectionOverlay("locked", payload);
  } catch (_) {
    setConnectionOverlay("locked", { lock_reason: reason });
  }
}

function stopStream(stream) {
  stream?.getTracks?.().forEach((track) => track.stop());
}

async function prepareEvidenceCapture(features = {}) {
  if (features.screen_activity_recording && !state.screenCaptureStream) {
    if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("This browser does not support screen recording.");
    state.screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: 8, max: 12 } },
      audio: false,
    });
  }
  if (features.environment_audio_recording && !state.environmentAudioStream) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser does not support environment-audio recording.");
    state.environmentAudioStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
    });
  }
}

async function uploadRecordingChunk(recordingType, sequence, blob, durationMs = 30000) {
  if (!state.assessmentId || !state.token || !blob?.size) return;
  const form = new FormData();
  form.set("recording_type", recordingType);
  form.set("sequence", String(sequence));
  form.set("duration_ms", String(durationMs));
  const extension = blob.type.includes("mp4") ? "mp4" : blob.type.includes("ogg") ? "ogg" : "webm";
  form.set("media", blob, `${recordingType}-${String(sequence).padStart(4, "0")}.${extension}`);
  await api(`/api/assessments/${state.assessmentId}/recording-chunk`, {
    method: "POST",
    headers: { Authorization: `Bearer ${state.token}` },
    body: form,
  });
}

function startEvidenceRecorder(stream, recordingType) {
  if (!stream || typeof MediaRecorder === "undefined") throw new Error("This browser cannot record the enabled evidence stream.");
  const preferred = recordingType === "screen"
    ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]
    : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg", "audio/mp4"];
  const mimeType = preferred.find((value) => MediaRecorder.isTypeSupported?.(value)) || "";
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.ondataavailable = (event) => {
    if (!event.data?.size) return;
    const sequence = state.recordingSequences[recordingType]++;
    const upload = uploadRecordingChunk(recordingType, sequence, event.data).catch(() => {
      postMonitoringEvent("monitoring_unavailable", 0, false, {
        severity: "critical",
        message: `${recordingType.replaceAll("_", " ")} evidence could not be uploaded.`,
      });
    }).finally(() => state.recordingUploads.delete(upload));
    state.recordingUploads.add(upload);
  };
  recorder.start(30000);
  state.evidenceRecorders.push(recorder);
  stream.getTracks().forEach((track) => {
    track.addEventListener("ended", () => {
      if (!state.testActive) return;
      postMonitoringEvent("monitoring_unavailable", 0, false, {
        severity: "critical",
        message: `${recordingType.replaceAll("_", " ")} recording was stopped before the assessment ended.`,
      });
    }, { once: true });
  });
}

function startEvidenceRecordings(activeFeatures = {}) {
  state.recordingSequences = { screen: 0, environment_audio: 0 };
  if (activeFeatures.screen_activity_recording) startEvidenceRecorder(state.screenCaptureStream, "screen");
  if (activeFeatures.environment_audio_recording) startEvidenceRecorder(state.environmentAudioStream, "environment_audio");
}

async function stopEvidenceRecordings() {
  const stopped = state.evidenceRecorders.map((recorder) => new Promise((resolve) => {
    if (recorder.state === "inactive") return resolve();
    recorder.addEventListener("stop", resolve, { once: true });
    recorder.stop();
  }));
  await Promise.all(stopped);
  await Promise.allSettled([...state.recordingUploads]);
  state.evidenceRecorders = [];
  stopStream(state.screenCaptureStream);
  stopStream(state.environmentAudioStream);
  state.screenCaptureStream = null;
  state.environmentAudioStream = null;
}

function setWebcamStatus(message, mode = "active") {
  const monitor = $(".webcam-monitor");
  const status = $("#webcam-status");
  if (status) status.textContent = message;
  if (!monitor) return;
  monitor.classList.toggle("interrupted", mode === "interrupted");
  monitor.classList.toggle("captured", mode === "captured");
  monitor.classList.toggle("limited", mode === "limited");
}


const MONITORING_RULES = {
  no_face: {
    thresholdMs: 1000,
    severity: "critical",
    title: "Face not visible",
    message: "Return to the camera view and keep your face clearly visible.",
  },
  multiple_faces: {
    thresholdMs: 700,
    severity: "critical",
    title: "More than one face detected",
    message: "Only the student taking the assessment should remain visible.",
  },
  looking_away: {
    thresholdMs: 1200,
    severity: "warning",
    title: "Please face the assessment screen",
    message: "Your head position indicates sustained attention away from the assessment.",
  },
  excessive_movement: {
    thresholdMs: 1200,
    severity: "warning",
    title: "Excessive head movement detected",
    message: "Keep your head reasonably steady and remain focused on the assessment.",
  },
  low_light: {
    thresholdMs: 2500,
    severity: "warning",
    title: "Lighting is insufficient",
    message: "Improve the lighting so your face remains clearly visible.",
  },
  camera_covered: {
    thresholdMs: 1200,
    severity: "critical",
    title: "Camera view obstructed",
    message: "Remove anything blocking the camera and restore a clear view.",
  },
  camera_frozen: {
    thresholdMs: 1800,
    severity: "critical",
    title: "Camera feed appears frozen",
    message: "Restore a live camera feed to continue reliable monitoring.",
  },
  camera_interrupted: {
    thresholdMs: 0,
    severity: "critical",
    title: "Camera interrupted",
    message: "Restore camera access to continue reliable monitoring.",
  },
  monitoring_unavailable: {
    thresholdMs: 0,
    severity: "critical",
    title: "Automated monitoring interrupted",
    message: "The visual monitoring engine stopped responding. Restore the camera or reload the assessment.",
  },
};

function resetMonitoringStates() {
  state.monitoringStates = Object.fromEntries(
    Object.keys(MONITORING_RULES).map((name) => [
      name,
      { activeSince: null, warned: false, lastDurationMs: 0 },
    ])
  );
  state.monitoringEventCount = 0;
  state.latestFaceDetections = [];
  state.lastMonitoringResultAt = 0;
  state.detectionFailureCount = 0;
  state.lastVideoCurrentTime = null;
  state.videoFrozenSince = null;
  state.lastFrameSignature = null;
  state.lastFaceMotionSample = null;
  state.faceMotionHistory = [];
  hideMonitoringWarning();
}

function showMonitoringWarning(rule) {
  const box = $("#monitoring-warning");
  if (!box) return;
  $("#monitoring-warning-title").textContent = rule.title;
  $("#monitoring-warning-text").textContent = rule.message;
  box.classList.remove("hidden", "critical");
  if (rule.severity === "critical") box.classList.add("critical");
}

function hideMonitoringWarning() {
  const box = $("#monitoring-warning");
  if (box) box.classList.add("hidden");
}

async function sendMonitoringPayload(payload) {
  const result = await api(`/api/assessments/${state.assessmentId}/monitoring-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify(payload),
    keepalive: true,
  });
  if (Number.isFinite(Number(result?.monitoring_event_count))) {
    state.monitoringEventCount = Number(result.monitoring_event_count);
  }
  return result;
}

function queueMonitoringPayload(payload) {
  const existing = state.monitoringEventQueue.find(
    (item) => item.event_type === payload.event_type && Boolean(item.corrected) === Boolean(payload.corrected)
  );
  if (existing) {
    existing.duration_ms = Math.max(existing.duration_ms || 0, payload.duration_ms || 0);
    existing.question_position = payload.question_position || existing.question_position;
    existing.severity = new Set([existing.severity, payload.severity]).has("critical") ? "critical" : "warning";
    existing.message = payload.message || existing.message;
    persistMonitoringPayload(existing);
    return;
  }
  const queued = {
    ...payload,
    _queue_id: payload._queue_id || globalThis.crypto?.randomUUID?.() || `event-${Date.now()}-${Math.random()}`,
  };
  state.monitoringEventQueue.push(queued);
  persistMonitoringPayload(queued);
  if (state.monitoringEventQueue.length > MONITOR_QUEUE_LIMIT) {
    state.monitoringEventQueue.splice(0, state.monitoringEventQueue.length - MONITOR_QUEUE_LIMIT);
  }
}

async function flushMonitoringEventQueue() {
  if (!state.assessmentId || !state.token || !navigator.onLine) return;
  const persisted = await loadPersistedMonitoringPayloads();
  persisted.forEach((item) => {
    if (!state.monitoringEventQueue.some((queued) => queued._queue_id === item.id)) {
      const { id, assessment_id, created_at, ...payload } = item;
      state.monitoringEventQueue.push({ ...payload, _queue_id: id });
    }
  });
  while (state.monitoringEventQueue.length) {
    const item = state.monitoringEventQueue[0];
    try {
      const { _queue_id, ...payload } = item;
      await sendMonitoringPayload(payload);
      state.monitoringEventQueue.shift();
      await deletePersistedMonitoringPayload(_queue_id);
    } catch (_) {
      break;
    }
  }
}

async function postMonitoringEvent(eventType, durationMs, corrected = false, override = {}) {
  if (!state.assessmentId || !state.token) return;
  const rule = MONITORING_RULES[eventType] || {};
  const payload = {
    event_type: eventType,
    duration_ms: Math.max(0, Math.round(durationMs || 0)),
    question_position: state.currentPosition || null,
    severity: override.severity || rule.severity || "warning",
    corrected,
    message: override.message || rule.message || null,
  };
  if (!navigator.onLine || state.connectionStatus === "offline") {
    queueMonitoringPayload(payload);
    return;
  }
  try {
    await sendMonitoringPayload(payload);
  } catch (_) {
    queueMonitoringPayload(payload);
  }
}

function updateMonitoringCondition(eventType, active) {
  const rule = MONITORING_RULES[eventType];
  const condition = state.monitoringStates[eventType];
  if (!rule || !condition) return;
  const now = performance.now();

  if (active) {
    if (condition.activeSince == null) condition.activeSince = now;
    const duration = now - condition.activeSince;
    condition.lastDurationMs = duration;
    if (!condition.warned && duration >= rule.thresholdMs) {
      condition.warned = true;
      state.monitoringEventCount += 1;
      showMonitoringWarning(rule);
      setWebcamStatus(rule.message, rule.severity === "critical" ? "interrupted" : "active");
      postMonitoringEvent(eventType, duration, false);
    }
    return;
  }

  if (condition.activeSince != null && condition.warned) {
    postMonitoringEvent(eventType, condition.lastDurationMs, true);
  }
  condition.activeSince = null;
  condition.lastDurationMs = 0;
  condition.warned = false;

  const anotherWarning = Object.entries(state.monitoringStates).find(
    ([name, value]) => name !== eventType && value.warned
  );
  if (anotherWarning) {
    showMonitoringWarning(MONITORING_RULES[anotherWarning[0]]);
  } else {
    hideMonitoringWarning();
    setWebcamStatus("Camera monitoring active.");
  }
}

function faceBox(detection) {
  const box = detection?.boundingBox || detection?.locationData?.relativeBoundingBox;
  if (!box) return null;
  const width = Number(box.width || 0);
  const height = Number(box.height || 0);
  const xCenter = Number.isFinite(Number(box.xCenter))
    ? Number(box.xCenter)
    : Number(box.xmin || 0) + width / 2;
  const yCenter = Number.isFinite(Number(box.yCenter))
    ? Number(box.yCenter)
    : Number(box.ymin || 0) + height / 2;
  return { xCenter, yCenter, width, height };
}

function facePoseMetrics(detection) {
  const box = faceBox(detection);
  if (!box) return null;

  const metrics = {
    ...box,
    yawScore: 0,
    hasLandmarks: false,
  };
  const landmarks = detection?.landmarks || detection?.locationData?.relativeKeypoints || [];
  if (landmarks.length >= 3) {
    const firstEye = landmarks[0];
    const secondEye = landmarks[1];
    const nose = landmarks[2];
    const eyeDistance = Math.abs(Number(firstEye.x) - Number(secondEye.x));
    if (eyeDistance > 0.015) {
      const midpoint = (Number(firstEye.x) + Number(secondEye.x)) / 2;
      metrics.yawScore = Math.abs(Number(nose.x) - midpoint) / eyeDistance;
      metrics.hasLandmarks = true;
    }
  }
  return metrics;
}

function faceLooksAway(detection) {
  const metrics = facePoseMetrics(detection);
  if (!metrics) return false;
  return (
    metrics.xCenter < 0.24 ||
    metrics.xCenter > 0.76 ||
    metrics.yCenter < 0.17 ||
    metrics.yCenter > 0.83 ||
    metrics.width < 0.13 ||
    (metrics.hasLandmarks && metrics.yawScore > 0.20)
  );
}

function faceMovesExcessively(detection) {
  const metrics = facePoseMetrics(detection);
  if (!metrics) {
    state.lastFaceMotionSample = null;
    state.faceMotionHistory = [];
    return false;
  }

  const now = performance.now();
  const sample = {
    at: now,
    x: metrics.xCenter,
    y: metrics.yCenter,
    width: Math.max(metrics.width, 0.01),
  };
  const previous = state.lastFaceMotionSample;
  state.lastFaceMotionSample = sample;
  if (!previous) return false;

  const elapsed = Math.max(250, now - previous.at);
  const centreShift = Math.hypot(sample.x - previous.x, sample.y - previous.y);
  const scaleShift = Math.abs(Math.log(sample.width / previous.width));
  const movementStep = centreShift + scaleShift * 0.30;
  const speed = movementStep / (elapsed / 1000);

  state.faceMotionHistory.push({ at: now, movementStep, speed });
  state.faceMotionHistory = state.faceMotionHistory.filter((item) => now - item.at <= 3000);

  const totalMovement = state.faceMotionHistory.reduce((sum, item) => sum + item.movementStep, 0);
  const rapidSamples = state.faceMotionHistory.filter((item) => item.speed > 0.13).length;
  return (
    state.faceMotionHistory.length >= 3 &&
    (totalMovement > 0.20 || rapidSamples >= 2)
  );
}

function frameStatistics(video) {
  const canvas = $("#monitoring-canvas");
  if (!canvas || !video.videoWidth || !video.videoHeight) {
    return { brightness: 255, variance: 255, difference: 255 };
  }
  canvas.width = 64;
  canvas.height = 48;
  const context = canvas.getContext("2d", { willReadFrequently: true, alpha: false });
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const signature = [];
  let sum = 0;
  let sumSquares = 0;
  for (let y = 0; y < canvas.height; y += 4) {
    for (let x = 0; x < canvas.width; x += 4) {
      const index = (y * canvas.width + x) * 4;
      const value = pixels[index] * 0.2126 + pixels[index + 1] * 0.7152 + pixels[index + 2] * 0.0722;
      signature.push(value);
      sum += value;
      sumSquares += value * value;
    }
  }
  const count = Math.max(1, signature.length);
  const brightness = sum / count;
  const variance = Math.max(0, sumSquares / count - brightness * brightness);
  let difference = 255;
  if (state.lastFrameSignature?.length === signature.length) {
    difference = signature.reduce((total, value, index) => total + Math.abs(value - state.lastFrameSignature[index]), 0) / count;
  }
  state.lastFrameSignature = signature;
  return { brightness, variance, difference };
}

function handleFaceDetectionResults(results) {
  const detections = Array.isArray(results?.detections) ? results.detections : [];
  state.latestFaceDetections = detections;
  state.monitoringResultVersion += 1;
  state.lastMonitoringResultAt = performance.now();
  state.detectionFailureCount = 0;
  if (!state.testActive) return;
  updateMonitoringCondition("monitoring_unavailable", false);
  updateMonitoringCondition("no_face", detections.length === 0);
  updateMonitoringCondition("multiple_faces", detections.length > 1);
  updateMonitoringCondition(
    "looking_away",
    detections.length === 1 && faceLooksAway(detections[0])
  );
  updateMonitoringCondition(
    "excessive_movement",
    detections.length === 1 && faceMovesExcessively(detections[0])
  );
  if (detections.length !== 1) {
    state.lastFaceMotionSample = null;
    state.faceMotionHistory = [];
  }
}

function normaliseNativeDetections(detections, video) {
  const width = Math.max(1, video.videoWidth || 1);
  const height = Math.max(1, video.videoHeight || 1);
  return (detections || []).map((detection) => {
    const box = detection.boundingBox || {};
    return {
      boundingBox: {
        xCenter: (Number(box.x || 0) + Number(box.width || 0) / 2) / width,
        yCenter: (Number(box.y || 0) + Number(box.height || 0) / 2) / height,
        width: Number(box.width || 0) / width,
        height: Number(box.height || 0) / height,
      },
      landmarks: [],
    };
  });
}

function normaliseTasksDetections(detections, video) {
  const width = Math.max(1, Number(video.videoWidth || 1));
  const height = Math.max(1, Number(video.videoHeight || 1));
  return (detections || []).map((detection) => {
    const box = detection?.boundingBox || {};
    const originX = Number(box.originX ?? box.origin_x ?? 0);
    const originY = Number(box.originY ?? box.origin_y ?? 0);
    const boxWidth = Number(box.width || 0);
    const boxHeight = Number(box.height || 0);
    const keypoints = Array.isArray(detection?.keypoints)
      ? detection.keypoints.map((point) => ({
          x: Number(point.x || 0),
          y: Number(point.y || 0),
        }))
      : [];
    return {
      boundingBox: {
        xCenter: (originX + boxWidth / 2) / width,
        yCenter: (originY + boxHeight / 2) / height,
        width: boxWidth / width,
        height: boxHeight / height,
      },
      landmarks: keypoints,
    };
  });
}

async function verifyMonitoringAssets() {
  const response = await fetch(MONITORING_ASSET_STATUS_URL, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Monitoring asset check failed (${response.status}).`);
  }
  const status = await response.json();
  if (!status.ready) {
    const missing = Array.isArray(status.missing) ? status.missing.join(", ") : "unknown files";
    throw new Error(`Monitoring files are missing from the deployment: ${missing}`);
  }
  return status.asset_base || MEDIAPIPE_TASKS_BASES[0];
}

async function loadTasksVisionModule() {
  if (!state.tasksVisionModulePromise) {
    state.tasksVisionModulePromise = (async () => {
      const preferredBase = await verifyMonitoringAssets();
      const candidates = [preferredBase, ...MEDIAPIPE_TASKS_BASES].filter(
        (value, index, values) => value && values.indexOf(value) === index
      );
      let lastError = null;
      for (const base of candidates) {
        try {
          const module = await import(`${base}/vision_bundle.mjs?v=20260701-model-metadata-fix1`);
          state.tasksVisionAssetBase = base;
          return module;
        } catch (error) {
          lastError = error;
          console.error(`Face-monitoring module failed from ${base}:`, error);
        }
      }
      throw lastError || new Error("The face-monitoring module could not be loaded.");
    })().catch((error) => {
      state.tasksVisionModulePromise = null;
      throw error;
    });
  }
  return state.tasksVisionModulePromise;
}

async function createTasksFaceDetector() {
  const { FilesetResolver, FaceDetector } = await loadTasksVisionModule();
  const assetBase = state.tasksVisionAssetBase || MEDIAPIPE_TASKS_BASES[0];
  const vision = await FilesetResolver.forVisionTasks(`${assetBase}/wasm`);
  const modelResponse = await fetch(
    `${assetBase}/models/face_detection_short_range.tflite?v=20260701-model-metadata-fix1`,
    {
      credentials: "same-origin",
      cache: "no-cache",
    }
  );
  if (!modelResponse.ok) {
    throw new Error(`Face model could not be loaded (${modelResponse.status}).`);
  }
  const modelBuffer = await modelResponse.arrayBuffer();
  if (modelBuffer.byteLength < 1000) {
    throw new Error("The local face model is incomplete.");
  }
  return FaceDetector.createFromOptions(vision, {
    baseOptions: {
      modelAssetBuffer: new Uint8Array(modelBuffer),
      delegate: "CPU",
    },
    runningMode: "VIDEO",
    minDetectionConfidence: 0.45,
    minSuppressionThreshold: 0.30,
  });
}

async function ensureMonitoringEngine() {
  if (state.faceDetector) return state.faceDetector;
  if (state.faceDetectorPromise) return state.faceDetectorPromise;

  state.faceDetectorPromise = (async () => {
    let tasksError = null;
    try {
      const detector = await createTasksFaceDetector();
      state.faceDetector = detector;
      state.monitoringEngine = "mediapipe_tasks";
      state.lastDetectionTimestampMs = 0;
      return detector;
    } catch (error) {
      tasksError = error;
      console.error("MediaPipe Tasks Face Detector failed to initialise:", error);
    }

    if (typeof window.FaceDetector === "function") {
      state.faceDetector = new window.FaceDetector({
        fastMode: true,
        maxDetectedFaces: 3,
      });
      state.monitoringEngine = "native";
      return state.faceDetector;
    }

    const rawDetail = tasksError?.message ? String(tasksError.message) : "";
    const metadataFailure = /NormalizationOptions metadata|Input tensor has type float32/i.test(rawDetail);
    const detail = metadataFailure
      ? " The deployed face model is incompatible with MediaPipe Tasks. Redeploy the corrected model package."
      : rawDetail
        ? ` ${rawDetail}`
        : "";
    throw new Error(`The automated face-monitoring engine could not be initialised.${detail}`);
  })();

  try {
    return await state.faceDetectorPromise;
  } catch (error) {
    state.faceDetectorPromise = null;
    throw error;
  }
}

async function runFaceDetectionFrame(video) {
  const detector = await ensureMonitoringEngine();
  if (state.monitoringEngine === "native") {
    const results = await detector.detect(video);
    handleFaceDetectionResults({
      detections: normaliseNativeDetections(results, video),
    });
    return;
  }

  const now = performance.now();
  const timestampMs = Math.max(now, state.lastDetectionTimestampMs + 1);
  state.lastDetectionTimestampMs = timestampMs;
  const result = detector.detectForVideo(video, timestampMs);
  handleFaceDetectionResults({
    detections: normaliseTasksDetections(result?.detections, video),
  });
}

async function runMonitoringPreflight() {
  const video = $("#webcam-preview");
  const track = state.cameraStream?.getVideoTracks?.()[0];
  if (!video || !track || track.readyState !== "live") throw new Error("A live camera feed is required.");
  await ensureMonitoringEngine();
  setWebcamStatus("Checking camera monitoring readiness.");
  const deadline = performance.now() + MONITOR_PREFLIGHT_TIMEOUT_MS;
  let oneFaceStreak = 0;
  let resultCount = 0;
  while (performance.now() < deadline) {
    if (track.readyState !== "live" || track.muted) throw new Error("The camera feed became unavailable.");
    if (!video.videoWidth || !video.videoHeight) { await new Promise((resolve) => setTimeout(resolve, 180)); continue; }
    const before = state.monitoringResultVersion;
    try {
      await runFaceDetectionFrame(video);
    } catch (error) {
      state.detectionFailureCount += 1;
      if (state.detectionFailureCount >= FACE_DETECTION_MAX_FAILURES) {
        throw new Error(`Automated face monitoring could not start: ${error.message}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
      continue;
    }
    if (state.monitoringResultVersion > before) resultCount += 1;
    const faces = state.latestFaceDetections.length;
    const stats = frameStatistics(video);
    if (faces === 1 && stats.brightness >= 25) {
      oneFaceStreak += 1;
      setWebcamStatus("Camera monitoring check in progress.");
    } else {
      oneFaceStreak = 0;
      if (faces === 0) setWebcamStatus("Position one face clearly inside the camera view.", "limited");
      else if (faces > 1) setWebcamStatus("Only one face should be visible before starting.", "limited");
      else setWebcamStatus("Improve the lighting before starting.", "limited");
    }
    if (oneFaceStreak >= 3 && resultCount >= 3) {
      state.monitoringSupported = true;
      setWebcamStatus("Camera monitoring ready.");
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 140));
  }
  throw new Error(resultCount ? "Monitoring could not confirm one clearly visible face." : "The face-monitoring engine did not return results. Reload the page or use a current Chrome or Edge browser.");
}

function clearMonitoringTimers() {
  if (state.monitoringTimer) clearInterval(state.monitoringTimer);
  if (state.monitoringWatchdogTimer) clearInterval(state.monitoringWatchdogTimer);
  if (state.monitoringFlushTimer) clearInterval(state.monitoringFlushTimer);
  state.monitoringTimer = null;
  state.monitoringWatchdogTimer = null;
  state.monitoringFlushTimer = null;
  state.monitoringBusy = false;
}

async function monitoringTick() {
  if (!state.testActive || state.monitoringBusy || document.visibilityState === "hidden") return;
  const video = $("#webcam-preview");
  const track = state.cameraStream?.getVideoTracks?.()[0];
  if (!video || !track || track.readyState !== "live" || track.muted || !video.videoWidth) {
    updateMonitoringCondition("camera_interrupted", true);
    return;
  }
  updateMonitoringCondition("camera_interrupted", false);
  const stats = frameStatistics(video);
  updateMonitoringCondition("low_light", stats.brightness < 42);
  updateMonitoringCondition("camera_covered", stats.brightness < 14 || (stats.variance < 5 && state.latestFaceDetections.length === 0));
  const currentTime = Number(video.currentTime || 0);
  if (state.lastVideoCurrentTime != null && Math.abs(currentTime - state.lastVideoCurrentTime) < 0.001) {
    if (state.videoFrozenSince == null) state.videoFrozenSince = performance.now();
  } else {
    state.videoFrozenSince = null;
  }
  state.lastVideoCurrentTime = currentTime;
  updateMonitoringCondition("camera_frozen", state.videoFrozenSince != null);
  state.monitoringBusy = true;
  try {
    await runFaceDetectionFrame(video);
    state.monitoringSupported = true;
    state.detectionFailureCount = 0;
  } catch (error) {
    state.detectionFailureCount += 1;
    console.error("Automated face monitoring failed:", error);
    if (state.detectionFailureCount >= FACE_DETECTION_MAX_FAILURES) {
      state.monitoringSupported = false;
      updateMonitoringCondition("monitoring_unavailable", true);
    }
  } finally {
    state.monitoringBusy = false;
  }
}

async function startSuspiciousMonitoring() {
  clearMonitoringTimers();
  resetMonitoringStates();
  await ensureMonitoringEngine();
  state.lastMonitoringResultAt = performance.now();
  state.detectionFailureCount = 0;
  setWebcamStatus("Camera monitoring active.");
  state.monitoringTimer = window.setInterval(monitoringTick, MONITOR_INTERVAL_MS);
  state.monitoringWatchdogTimer = window.setInterval(() => {
    if (!state.testActive) return;
    updateMonitoringCondition("monitoring_unavailable", performance.now() - state.lastMonitoringResultAt > MONITOR_RESULT_TIMEOUT_MS);
  }, 700);
  state.monitoringFlushTimer = window.setInterval(flushMonitoringEventQueue, 1500);
  await monitoringTick();
}

function stopSuspiciousMonitoring({ closeDetector = true } = {}) {
  clearMonitoringTimers();
  flushMonitoringEventQueue();
  if (closeDetector && state.faceDetector?.close) {
    try { state.faceDetector.close(); } catch (_) {}
  }
  if (closeDetector) {
    state.faceDetector = null;
    state.faceDetectorPromise = null;
    state.monitoringEngine = null;
    state.lastDetectionTimestampMs = 0;
  }
  state.lastFaceMotionSample = null;
  state.faceMotionHistory = [];
  state.latestFaceDetections = [];
  state.lastMonitoringResultAt = 0;
  hideMonitoringWarning();
}

async function startCamera() {
  const activeTrack = state.cameraStream?.getVideoTracks?.()[0];
  if (activeTrack?.readyState === "live") return state.cameraStream;
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser does not support webcam access. Use a current browser on an HTTPS connection.");
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: "user",
      width: { ideal: 640, max: 1280 },
      height: { ideal: 480, max: 720 },
    },
    audio: false,
  });
  state.cameraStream = stream;
  const video = $("#webcam-preview");
  video.srcObject = stream;
  await video.play();
  const track = stream.getVideoTracks()[0];
  track.addEventListener("ended", () => {
    if (!state.testActive) return;
    setWebcamStatus("Camera access stopped. Restore access to continue monitoring.", "interrupted");
    updateMonitoringCondition("camera_interrupted", true);
  });
  track.addEventListener("mute", () => {
    if (!state.testActive) return;
    setWebcamStatus("The camera feed is temporarily unavailable.", "interrupted");
    updateMonitoringCondition("camera_interrupted", true);
  });
  track.addEventListener("unmute", () => {
    if (!state.testActive) return;
    updateMonitoringCondition("camera_interrupted", false);
  });
  setWebcamStatus("Camera monitoring ready.");
  return stream;
}

function stopCamera() {
  clearSnapshotTimer();
  stopSuspiciousMonitoring();
  state.cameraStream?.getTracks?.().forEach((track) => track.stop());
  state.cameraStream = null;
  const video = $("#webcam-preview");
  if (video) video.srcObject = null;
}

function clearSnapshotTimer() {
  if (state.snapshotTimer) clearTimeout(state.snapshotTimer);
  state.snapshotTimer = null;
  state.captureScheduledForCurrentQuestion = false;
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("The webcam image could not be created.")),
      "image/jpeg",
      0.78
    );
  });
}

async function captureSnapshotOnce(reason = "random") {
  if (state.snapshotCaptured) return true;
  if (state.snapshotPromise) return state.snapshotPromise;

  state.snapshotPromise = (async () => {
    const video = $("#webcam-preview");
    const canvas = $("#webcam-canvas");
    const track = state.cameraStream?.getVideoTracks?.()[0];
    if (!video || !canvas || !track || track.readyState !== "live") {
      setWebcamStatus("Camera monitoring requires a live camera feed.", "interrupted");
      return false;
    }
    if (!video.videoWidth || !video.videoHeight) {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!video.videoWidth || !video.videoHeight) {
      setWebcamStatus("Camera monitoring requires a clear camera feed.", "interrupted");
      return false;
    }

    const maxWidth = 640;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const context = canvas.getContext("2d", { alpha: false });
    context.translate(canvas.width, 0);
    context.scale(-1, 1);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    context.setTransform(1, 0, 0, 1, 0, 0);

    const blob = await canvasBlob(canvas);
    const formData = new FormData();
    formData.append("image", blob, "webcam-snapshot.jpg");
    formData.append("capture_reason", reason);
    const result = await api(`/api/assessments/${state.assessmentId}/snapshot`, {
      method: "POST",
      headers: { Authorization: `Bearer ${state.token}` },
      body: formData,
    });
    state.snapshotCaptured = Boolean(result.captured);
    if (state.snapshotCaptured) {
      setWebcamStatus("Camera monitoring active.", "captured");
    }
    return state.snapshotCaptured;
  })();

  try {
    return await state.snapshotPromise;
  } catch (error) {
    setWebcamStatus(`Camera verification failed: ${error.message}`, "interrupted");
    return false;
  } finally {
    state.snapshotPromise = null;
    clearSnapshotTimer();
  }
}

$$('[data-view-link]').forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    showView(button.dataset.viewLink);
  });
});

$$('[data-password-toggle]').forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;
    const revealing = input.type === "password";
    input.type = revealing ? "text" : "password";
    button.textContent = revealing ? "Hide" : "Show";
  });
});

function renderResolvedCourse(course) {
  state.resolvedCourse = course;
  const card = $("#course-requirements-card");
  if (!card) return;
  card.classList.remove("hidden");
  const processParts = [];
  processParts.push((course.assessment_tasks || []).length
    ? `${course.assessment_tasks.length} assessment task(s)`
    : `Legacy course workflow · ${course.max_attempts || 1} attempt(s) permitted`);
  const aiPolicy = course.ai_policy || {};
  processParts.push(aiPolicy.label || "AI permitted with disclosure");
  if (course.live_exam_monitoring_enabled) {
    processParts.push(`Live monitoring: ${course.live_exam_title || "enabled"}`);
  }
  card.innerHTML = `<strong></strong><span></span><small></small>`;
  card.querySelector("strong").textContent = course.course;
  card.querySelector("span").textContent = `${course.lecturer || "Assigned lecturer"} · ${course.institution || "Institution"}`;
  card.querySelector("small").textContent = `${processParts.join(" · ")} · Final justification required${aiPolicy.guidance ? ` · ${aiPolicy.guidance}` : ""}${course.live_exam_instructions ? ` · ${course.live_exam_instructions}` : ""}`;

  const aiSelect = $("#ai-use-level");
  if (aiSelect && aiPolicy.level === "required") aiSelect.value = "required";
  if (aiSelect && aiPolicy.level === "prohibited") aiSelect.value = "none";
  aiSelect?.dispatchEvent(new Event("change"));

  const milestoneSelect = $("#student-milestone");
  milestoneSelect.replaceChildren(new Option("Select milestone", ""));
  (course.milestones || []).forEach((item) => milestoneSelect.appendChild(new Option(item.title, item.id)));

  const tasks = course.assessment_tasks || [];
  const taskField = $("#assessment-task-field");
  const taskSelect = $("#student-assessment-task");
  taskSelect.replaceChildren(new Option("Select assessment task", ""));
  tasks.forEach((task) => taskSelect.appendChild(new Option(
    `${task.title} · ${task.mode === "standalone_exam" ? "Standalone exam" : "Document ownership"} · ${task.max_attempts || 1} attempt(s)`,
    task.id
  )));
  taskField?.classList.toggle("hidden", tasks.length === 0);
  taskSelect.required = tasks.length > 1;
  if (tasks.length === 1) taskSelect.value = tasks[0].id;
  applySelectedAssessmentTask();
}

function applySelectedAssessmentTask() {
  const course = state.resolvedCourse;
  if (!course) return;
  const selectedId = $("#student-assessment-task")?.value || "";
  const task = (course.assessment_tasks || []).find((item) => item.id === selectedId) || null;
  state.selectedTask = task;
  const standalone = task?.mode === "standalone_exam";
  const features = task?.effective_features || {
    learning_milestones: Boolean(course.milestones_enabled),
    version_history: Boolean(course.version_history_enabled),
    webcam_monitoring: Boolean(course.webcam_monitoring_enabled),
    secure_browser_enforcement: Boolean(course.secure_browser_enforcement),
  };
  state.monitoringEnabled = Boolean(features.webcam_monitoring);
  state.secureBrowserEnforcement = Boolean(features.secure_browser_enforcement);
  state.activeFeatures = features;
  const consentEvidence = [];
  if (features.webcam_monitoring) consentEvidence.push("camera monitoring");
  if (features.screen_activity_recording) consentEvidence.push("screen activity recording");
  if (features.environment_audio_recording) consentEvidence.push("environment audio recording");
  const consentText = $("#assessment-consent-text");
  if (consentText) {
    consentText.textContent = consentEvidence.length
      ? `I understand the assessment rules and consent to ${consentEvidence.join(", ")} being retained for authorised review.`
      : "I understand the assessment rules and the configured browser controls.";
  }

  $("#submission-assurance-fields")?.classList.toggle("hidden", standalone);
  $("#standalone-exam-card")?.classList.toggle("hidden", !standalone);
  $("#submission-assurance-fields")?.querySelectorAll("input, select, textarea, button").forEach((input) => {
    input.disabled = standalone;
  });
  if (standalone) {
    $("#standalone-exam-title").textContent = task.title;
    $("#standalone-exam-instructions").textContent = `${task.instructions || "Complete the examination in one uninterrupted session."} Maximum starts per candidate: ${task.max_attempts || 1}.${task.ready ? "" : " The lecturer is still preparing the question bank."}`;
    const recordingLabels = [];
    if (features.screen_activity_recording) recordingLabels.push("screen activity");
    if (features.environment_audio_recording) recordingLabels.push("environment audio");
    if (features.webcam_monitoring) recordingLabels.push("camera monitoring");
    $("#standalone-consent-text").textContent = recordingLabels.length
      ? `I understand the examination rules and consent to ${recordingLabels.join(", ")} being retained for authorised review.`
      : "I understand the examination rules and the configured browser controls.";
    $("#start-standalone-exam").disabled = !task.ready || !$("#standalone-exam-consent")?.checked;
  }

  const milestonesEnabled = Boolean(features.learning_milestones) && !standalone;
  const stage = $("#submission-stage");
  $("#submission-stage-field")?.classList.toggle("hidden", !milestonesEnabled);
  if (!milestonesEnabled && stage) stage.value = "final";
  $("#milestone-field")?.classList.toggle("hidden", !milestonesEnabled || stage?.value !== "milestone");
}

async function resolveStudentCourse(showErrors = true) {
  const input = $("#student-course-code");
  const code = input?.value.trim();
  if (!code || code.length < 6) {
    state.resolvedCourse = null;
    $("#course-requirements-card")?.classList.add("hidden");
    if (showErrors) setMessage($("#upload-message"), "Enter a valid course enrolment code.", "warning");
    return null;
  }
  try {
    const result = await api(`/api/courses/resolve?code=${encodeURIComponent(code)}`);
    renderResolvedCourse(result);
    if (showErrors) setMessage($("#upload-message"), "Course requirements confirmed.", "success");
    return result;
  } catch (error) {
    state.resolvedCourse = null;
    $("#course-requirements-card")?.classList.add("hidden");
    if (showErrors) setMessage($("#upload-message"), error.message, "error");
    return null;
  }
}

$("#verify-course-code")?.addEventListener("click", () => resolveStudentCourse(true));
$("#student-course-code")?.addEventListener("change", () => resolveStudentCourse(false));
$("#student-assessment-task")?.addEventListener("change", applySelectedAssessmentTask);
$("#standalone-exam-consent")?.addEventListener("change", applySelectedAssessmentTask);
$("#submission-stage")?.addEventListener("change", (event) => {
  const milestone = event.target.value === "milestone";
  $("#milestone-field")?.classList.toggle("hidden", !milestone);
  $("#student-milestone").required = milestone;
});
$("#ai-use-level")?.addEventListener("change", (event) => {
  const details = $("#ai-use-details-field");
  const textarea = details?.querySelector("textarea");
  const required = event.target.value !== "none";
  details?.classList.toggle("hidden", !required);
  if (textarea) textarea.required = required;
});

const fileInput = $("#file-input");
const fileDrop = $("#file-drop");
fileInput.addEventListener("change", () => {
  $("#file-label").textContent = fileInput.files[0]?.name || "Choose a PDF, DOCX, or TXT file";
});
["dragenter", "dragover"].forEach((name) => fileDrop.addEventListener(name, (event) => {
  event.preventDefault();
  fileDrop.classList.add("dragover");
}));
["dragleave", "drop"].forEach((name) => fileDrop.addEventListener(name, (event) => {
  event.preventDefault();
  fileDrop.classList.remove("dragover");
}));
fileDrop.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) {
    fileInput.files = event.dataTransfer.files;
    $("#file-label").textContent = fileInput.files[0].name;
  }
});

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  button.textContent = "Uploading…";
  setMessage($("#upload-message"));
  try {
    const course = state.resolvedCourse || await resolveStudentCourse(false);
    if (!course) throw new Error("Confirm the course enrolment code before uploading.");
    const formData = new FormData(event.currentTarget);
    if (state.selectedTask?.mode === "standalone_exam") throw new Error("Use Start examination for this task.");
    if (!state.selectedTask?.effective_features?.learning_milestones && state.selectedTask) formData.set("submission_stage", "final");
    const result = await api("/api/documents", { method: "POST", body: formData });
    state.documentId = result.document_id;
    pollStartedAt = Date.now();
    setProcessingSpinner(true);
    setGenerationActions(false);
    localStorage.setItem("kanokware_document_id", state.documentId);
    showPanel("prepare", 2);
    pollDocumentStatus();
  } catch (error) {
    setMessage($("#upload-message"), error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Generate learning assessment";
  }
});

let pollHandle = null;
let pollStartedAt = null;
const POLL_LIMIT_MS = 9 * 60 * 1000;

function setGenerationActions(visible, allowRetry = true) {
  const retryButton = $("#retry-generation-button");
  const restartButton = $("#restart-upload-button");
  if (retryButton) retryButton.classList.toggle("hidden", !visible || !allowRetry);
  if (restartButton) restartButton.classList.toggle("hidden", !visible);
}

function setProcessingSpinner(running) {
  const spinner = $("#panel-prepare .spinner");
  if (!spinner) return;
  spinner.style.animation = running ? "" : "none";
  spinner.style.animationPlayState = running ? "running" : "paused";
  spinner.style.opacity = running ? "1" : "0";
  spinner.style.visibility = running ? "visible" : "hidden";
}

async function pollDocumentStatus() {
  clearTimeout(pollHandle);
  if (!state.documentId) return;
  if (!pollStartedAt) pollStartedAt = Date.now();
  try {
    const result = await api(`/api/documents/${state.documentId}/status`);
    const elapsedMs = Date.now() - pollStartedAt;
    $("#processing-questions").textContent = result.question_count;
    state.assessmentQuestionCount = Number(result.assessment_question_count || 20);
    const passThreshold = Number(result.pass_threshold || 80);
    const thresholdBadge = document.querySelector(".threshold-badge");
    if (thresholdBadge) thresholdBadge.textContent = `Pass threshold: ${passThreshold}%`;
    const requiredCorrect = Math.ceil(
      state.assessmentQuestionCount * passThreshold / 100
    );
    const instructionCount = $("#instruction-question-count");
    const instructionRequirement = $("#instruction-question-requirement");
    if (instructionCount) instructionCount.textContent = `${state.assessmentQuestionCount} questions`;
    if (instructionRequirement) {
      instructionRequirement.textContent = `The lecturer selected ${state.assessmentQuestionCount} questions. At least ${requiredCorrect} correct answers are required at the displayed threshold.`;
    }
    $("#processing-mode").textContent = result.generation_mode === "pending" ? "Pending" : result.generation_mode;
    const widths = { queued: 12, generating: 55, ready: 100, failed: 100 };
    $("#processing-progress").style.width = `${widths[result.status] || 25}%`;
    const messages = {
      queued: "The document is queued for processing.",
      generating: `Generating and validating ${state.assessmentQuestionCount} document-specific questions.`,
      ready: "All questions passed grounding and structure checks.",
      failed: "Question generation could not be completed.",
    };
    $("#processing-status").textContent = messages[result.status] || "Processing the document.";
    if (result.status === "ready") {
      $("#mode-warning").className = "message";
      if (result.generation_mode === "demo") {
        setMessage(
          $("#mode-warning"),
          "Demo mode is active because no OpenAI API key is configured. Use AI mode before relying on results for institutional decisions.",
          "warning"
        );
      }
      setGenerationActions(false);
      setTimeout(() => showPanel("ready", 2), 450);
      return;
    }
    if (result.status === "failed") {
      setMessage($("#prepare-error"), result.error || "Question generation failed.", "error");
      setGenerationActions(true);
      return;
    }
    if (elapsedMs >= POLL_LIMIT_MS) {
      setMessage(
        $("#prepare-error"),
        "Question generation is taking too long and polling has stopped. Retry generation or upload the document again.",
        "error"
      );
      setGenerationActions(true);
      return;
    }
    pollHandle = setTimeout(pollDocumentStatus, 1800);
  } catch (error) {
    clearTimeout(pollHandle);
    pollHandle = null;
    setProcessingSpinner(false);

    const documentMissing = error.status === 404 || /document not found/i.test(error.message || "");
    if (documentMissing) {
      clearTimeout(pollHandle);
      pollHandle = null;
      localStorage.removeItem("kanokware_document_id");
      state.documentId = null;
      clearAssessmentSession();
      pollStartedAt = null;
      setProcessingSpinner(false);
      $("#processing-status").textContent = "The saved submission is no longer available.";
      $("#processing-mode").textContent = "Stopped";
      $("#processing-progress").style.width = "0%";
      setMessage($("#prepare-error"));
      showPanel("upload", 1);
      setMessage(
        $("#upload-message"),
        "The previous submission no longer exists on the server. Please upload the document again.",
        "error"
      );
      return;
    }

    setMessage($("#prepare-error"), error.message, "error");
    setGenerationActions(true);
  }
}

const retryGenerationButton = $("#retry-generation-button");
if (retryGenerationButton) retryGenerationButton.addEventListener("click", async () => {
  const button = $("#retry-generation-button");
  button.disabled = true;
  setMessage($("#prepare-error"), "Restarting question generation…");
  try {
    await api(`/api/documents/${state.documentId}/retry`, { method: "POST" });
    pollStartedAt = Date.now();
    setProcessingSpinner(true);
    setGenerationActions(false);
    $("#processing-questions").textContent = "0";
    $("#processing-mode").textContent = "Pending";
    $("#processing-progress").style.width = "12%";
    $("#processing-status").textContent = "Question generation has restarted.";
    setMessage($("#prepare-error"));
    pollDocumentStatus();
  } catch (error) {
    setMessage($("#prepare-error"), error.message, "error");
    setGenerationActions(true);
  } finally {
    button.disabled = false;
  }
});

const restartUploadButton = $("#restart-upload-button");
if (restartUploadButton) restartUploadButton.addEventListener("click", () => {
  clearTimeout(pollHandle);
  pollStartedAt = null;
  state.documentId = null;
  localStorage.removeItem("kanokware_document_id");
  $("#upload-form").reset();
  $("#file-label").textContent = "Choose a PDF, DOCX, or TXT file";
  setMessage($("#prepare-error"));
  setProcessingSpinner(true);
  setGenerationActions(false);
  showPanel("upload", 1);
});

$("#consent-checkbox").addEventListener("change", (event) => {
  $("#start-button").disabled = !event.target.checked;
});

async function activateStartedAssessment(result, monitoringPrepared = false) {
  state.assessmentId = result.assessment_id;
  state.token = result.session_token;
  state.snapshotCaptured = false;
  state.assessmentQuestionCount = Number(result.question_count || state.assessmentQuestionCount || 20);
  state.monitoringEnabled = Boolean(result.monitoring_enabled);
  state.secureBrowserEnforcement = Boolean(result.secure_browser_enforcement);
  state.activeFeatures = result.active_features || {};
  state.secureBrowserArmed = false;
  state.testActive = true;
  saveAssessmentSession();
  state.heartbeatIntervalMs = Math.max(3000, Number(result.heartbeat_interval_seconds || 5) * 1000);
  state.connectionStatus = "online";
  showPanel("test", 3);
  setConnectionOverlay("online");
  if (state.secureBrowserEnforcement) {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen?.().catch(() => {});
    }
    if (!document.fullscreenElement) {
      state.secureBrowserArmed = true;
      await terminateForSecureBrowser("fullscreen_exited");
      throw new Error("Secure fullscreen could not be started. This attempt was locked for review.");
    }
    window.setTimeout(() => { state.secureBrowserArmed = state.testActive; }, 750);
  }
  if (state.monitoringEnabled) {
    if (!monitoringPrepared) {
      await startCamera();
      await runMonitoringPreflight();
    }
    await startSuspiciousMonitoring();
    await sendHeartbeat({ cameraVerified: true, reason: "start" });
    startHeartbeatLoop();
  } else {
    stopCamera();
  }
  startEvidenceRecordings(state.activeFeatures);
  await loadQuestion();
}

$("#start-button").addEventListener("click", async () => {
  const button = $("#start-button");
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    let monitoringPrepared = false;
    if (state.secureBrowserEnforcement && !document.fullscreenElement) {
      await document.documentElement.requestFullscreen();
    }
    await prepareEvidenceCapture(state.activeFeatures);
    if (state.monitoringEnabled) {
      button.textContent = "Checking camera…";
      await startCamera();
      await runMonitoringPreflight();
      monitoringPrepared = true;
    }
    button.textContent = "Starting assessment…";
    const result = await api("/api/assessments/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: state.documentId,
        client_instance_id: state.clientInstanceId,
      }),
    });
    await activateStartedAssessment(result, monitoringPrepared);
  } catch (error) {
    stopCamera();
    await stopEvidenceRecordings();
    if (isAttemptLimitError(error)) {
      await returnToStartModeAfterAttemptLimit(error.message);
      return;
    }
    setMessage($("#mode-warning"), `The enabled examination controls could not start: ${error.message}`, "error");
    button.disabled = false;
  } finally {
    button.textContent = "Start assessment";
  }
});

$("#start-standalone-exam")?.addEventListener("click", async () => {
  const button = $("#start-standalone-exam");
  const task = state.selectedTask;
  const candidateName = $("#exam-candidate-name")?.value.trim();
  const candidateId = $("#exam-candidate-id")?.value.trim();
  if (!task || task.mode !== "standalone_exam") return;
  if (!candidateName || !candidateId) {
    setMessage($("#standalone-exam-message"), "Enter the candidate name and ID.", "warning");
    return;
  }
  button.disabled = true;
  setMessage($("#standalone-exam-message"), "Starting examination…");
  try {
    let monitoringPrepared = false;
    if (state.secureBrowserEnforcement && !document.fullscreenElement) {
      await document.documentElement.requestFullscreen();
    }
    await prepareEvidenceCapture(state.activeFeatures);
    if (state.monitoringEnabled) {
      await startCamera();
      await runMonitoringPreflight();
      monitoringPrepared = true;
    }
    const result = await api("/api/examinations/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        course_code: $("#student-course-code").value.trim(),
        task_id: task.id,
        candidate_name: candidateName,
        candidate_id: candidateId,
        client_instance_id: state.clientInstanceId,
      }),
    });
    await activateStartedAssessment(result, monitoringPrepared);
  } catch (error) {
    stopCamera();
    await stopEvidenceRecordings();
    if (isAttemptLimitError(error)) {
      await returnToStartModeAfterAttemptLimit(error.message);
      return;
    }
    setMessage($("#standalone-exam-message"), error.message, "error");
  } finally {
    button.disabled = !task.ready || !$("#standalone-exam-consent")?.checked;
  }
});

function clearQuestionTimer() {
  if (state.timerHandle) clearInterval(state.timerHandle);
  state.timerHandle = null;
}

function startQuestionTimer(remainingMs) {
  clearQuestionTimer();
  const started = performance.now();
  const timer = $("#timer");
  const value = $("#timer-value");
  const render = () => {
    const left = Math.max(0, remainingMs - (performance.now() - started));
    value.textContent = (left / 1000).toFixed(1);
    timer.classList.toggle("warning", left <= 10000 && left > 5000);
    timer.classList.toggle("danger", left <= 5000);
    if (left <= 0) {
      clearQuestionTimer();
      disableOptions();
      if (state.captureScheduledForCurrentQuestion && !state.snapshotCaptured) {
        captureSnapshotOnce("random");
      }
      setMessage($("#test-message"), "Time expired. Moving to the next question.", "warning");
      setTimeout(loadQuestion, 350);
    }
  };
  render();
  state.timerHandle = setInterval(render, 100);
}

function disableOptions() {
  $$(".option-button").forEach((button) => { button.disabled = true; });
}

async function loadQuestion() {
  if (!state.assessmentId || !state.token) return;
  if (!navigator.onLine || state.connectionStatus === "offline") {
    markConnectionInterrupted("offline");
    return;
  }
  clearSnapshotTimer();
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/question`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (result.status === "completed") {
      await loadResult();
      return;
    }
    if (result.status === "locked") {
      state.connectionStatus = "locked";
      clearQuestionTimer();
      disableOptions();
      setConnectionOverlay("locked", result);
      return;
    }
    if (result.status === "interrupted") {
      state.connectionStatus = "interrupted";
      clearQuestionTimer();
      disableOptions();
      setConnectionOverlay("interrupted", result);
      return;
    }
    state.connectionStatus = "online";
    setConnectionOverlay("online");
    if (state.currentPosition === result.position && result.remaining_ms < 250) {
      setTimeout(loadQuestion, 300);
      return;
    }
    state.currentPosition = result.position;
    setMessage($("#test-message"));
    $("#question-progress-text").textContent = `Question ${result.position} of ${result.total}`;
    $("#question-progress-bar").style.width = `${(result.position / result.total) * 100}%`;
    $("#difficulty-badge").textContent = result.difficulty;
    $("#question-heading").textContent = result.stem;
    const options = $("#options");
    options.replaceChildren();
    result.options.forEach((option, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "option-button";
      button.innerHTML = `<span class="option-letter">${String.fromCharCode(65 + index)}</span><span></span>`;
      button.lastElementChild.textContent = option;
      button.addEventListener("click", () => submitAnswer(index, button));
      options.appendChild(button);
    });
    startQuestionTimer(result.remaining_ms);
    if (result.capture_requested && !state.snapshotCaptured) {
      state.captureScheduledForCurrentQuestion = true;
      const requestedDelay = Number(result.capture_after_ms || 1500);
      const safeDelay = Math.max(250, Math.min(requestedDelay, Math.max(250, result.remaining_ms - 500)));
      state.snapshotTimer = setTimeout(() => captureSnapshotOnce("random"), safeDelay);
    }
  } catch (error) {
    if (error.networkError) {
      markConnectionInterrupted("network_failure");
      return;
    }
    if (error.status === 401 || error.status === 404) {
      clearAssessmentSession();
      state.testActive = false;
      stopCamera();
      await stopEvidenceRecordings();
      showPanel("upload", 1);
      setMessage($("#upload-message"), error.message, "error");
      return;
    }
    setMessage($("#test-message"), error.message, "error");
  }
}

async function submitAnswer(index, selectedButton) {
  if (!navigator.onLine || state.connectionStatus !== "online") {
    markConnectionInterrupted("offline");
    return;
  }
  clearQuestionTimer();
  disableOptions();
  selectedButton.classList.add("selected");
  setMessage($("#test-message"), "Submitting answer…");
  const pendingCapture = state.captureScheduledForCurrentQuestion && !state.snapshotCaptured
    ? captureSnapshotOnce("random")
    : Promise.resolve(false);
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/answer`, {
      method: "POST",
      headers: assessmentHeaders(),
      body: JSON.stringify({ selected_index: index }),
    });
    await pendingCapture;
    setMessage($("#test-message"), "Answer received.", "success");
    if (result.status === "completed") {
      await loadResult();
    } else {
      setTimeout(loadQuestion, 300);
    }
  } catch (error) {
    if (error.networkError) {
      markConnectionInterrupted("network_failure");
      setMessage($("#test-message"), "The answer was not confirmed by the server. Reconnect to continue.", "error");
      return;
    }
    if (error.status === 409 || error.status === 423) {
      await recoverAssessment("manual_retry");
      return;
    }
    setMessage($("#test-message"), error.message, "error");
    setTimeout(loadQuestion, 500);
  }
}

async function loadResult() {
  clearQuestionTimer();
  clearSnapshotTimer();
  clearHeartbeatTimer();
  try {
    if (!state.snapshotCaptured) await captureSnapshotOnce("completion_fallback");
    stopCamera();
    await stopEvidenceRecordings();
    state.testActive = false;
    const result = await api(`/api/assessments/${state.assessmentId}/result`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    showPanel("result", 4);
    const score = Number(result.score || 0);
    $("#score-value").textContent = `${score.toFixed(0)}%`;
    $("#score-ring").style.background = `conic-gradient(var(--brand) ${score}%, #e6ede9 ${score}%)`;
    $("#result-decision").textContent = result.decision;
    $("#correct-count").textContent = `${result.correct_count}/${result.question_count}`;
    $("#timeout-count").textContent = result.timed_out_count;
    $("#focus-count").textContent = result.focus_loss_count;
    $("#monitoring-count").textContent = result.monitoring_event_count || 0;
    if ($("#interruption-count")) $("#interruption-count").textContent = result.interruption_count || 0;
    $("#result-disclaimer").textContent = result.disclaimer;
    clearAssessmentSession();
  } catch (error) {
    setMessage($("#test-message"), error.message, "error");
  }
}

$("#new-assessment-button").addEventListener("click", () => {
  stopCamera();
  stopEvidenceRecordings();
  clearHeartbeatTimer();
  state.documentId = null;
  clearAssessmentSession();
  state.currentPosition = null;
  state.snapshotCaptured = false;
  state.monitoringEventCount = 0;
  state.assessmentQuestionCount = 20;
  pollStartedAt = null;
  localStorage.removeItem("kanokware_document_id");
  $("#upload-form").reset();
  $("#file-label").textContent = "Choose a PDF, DOCX, or TXT file";
  $("#consent-checkbox").checked = false;
  $("#start-button").disabled = true;
  showPanel("upload", 1);
  document.exitFullscreen?.().catch(() => {});
});

document.addEventListener("visibilitychange", () => {
  if (!state.testActive || document.visibilityState !== "hidden") return;
  if (state.secureBrowserEnforcement) {
    terminateForSecureBrowser("page_hidden");
    return;
  }
  fetch(`/api/assessments/${state.assessmentId}/focus-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify({ event: "hidden" }),
    keepalive: true,
  }).catch(() => {});
});
window.addEventListener("blur", () => {
  if (!state.testActive) return;
  if (state.secureBrowserEnforcement) {
    terminateForSecureBrowser("window_blur");
    return;
  }
  showMonitoringWarning({ title: "Assessment window lost focus", message: "Return to the assessment window.", severity: "warning" });
  postMonitoringEvent("window_blur", 0, false, { severity: "warning", message: "The assessment browser window lost focus." });
  fetch(`/api/assessments/${state.assessmentId}/focus-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify({ event: "blur" }),
    keepalive: true,
  }).catch(() => {});
});
document.addEventListener("fullscreenchange", () => {
  if (!state.testActive || !state.secureBrowserEnforcement || !state.secureBrowserArmed) return;
  if (!document.fullscreenElement) terminateForSecureBrowser("fullscreen_exited");
});
window.addEventListener("focus", () => {
  if (!state.testActive) return;
  postMonitoringEvent("window_blur", 0, true, { severity: "warning", message: "The assessment browser window regained focus." });
  const activeWarning = Object.values(state.monitoringStates).some((condition) => condition.warned);
  if (!activeWarning) hideMonitoringWarning();
});

window.addEventListener("offline", () => {
  if (!state.testActive) return;
  markConnectionInterrupted("offline");
  reportExplicitInterruption("offline");
});

window.addEventListener("online", () => {
  if (!state.testActive) return;
  recoverAssessment("reconnect");
});

const retryConnectionButton = $("#retry-connection-button");
if (retryConnectionButton) {
  retryConnectionButton.addEventListener("click", () => recoverAssessment("manual_retry"));
}

async function loadLecturerSession(silent = false) {
  try {
    const result = await api("/api/auth/me");
    if (!result.authenticated || !result.user) {
      state.lecturerUser = null;
      showLecturerAuth();
      return;
    }
    state.lecturerUser = result.user;
    showLecturerDashboard(result.user);
    if (!result.user.must_change_password) {
      await Promise.all([loadCourses(), loadSubmissions()]);
    }
  } catch (error) {
    state.lecturerUser = null;
    showLecturerAuth();
    if (!silent) setMessage($("#lecturer-auth-message"), error.message, "error");
  }
}

function setPasswordChangeMode(required) {
  const panel = $("#change-password-panel");
  const content = $("#lecturer-main-content");
  panel.classList.toggle("hidden", !required);
  content.classList.toggle("hidden", required);
  $("#show-change-password").classList.toggle("hidden", required);
  if (required) {
    $("#change-password-heading").textContent = "Set a private password";
    setMessage($("#password-change-message"), "Set a private password before using the lecturer workspace.", "warning");
  } else {
    $("#change-password-heading").textContent = "Change password";
    setMessage($("#password-change-message"));
  }
}

function showLecturerDashboard(user) {
  $("#lecturer-auth-panel").classList.add("hidden");
  $("#lecturer-dashboard").classList.remove("hidden");
  $("#lecturer-name").textContent = user.full_name;
  $("#lecturer-profile").textContent = `${user.role.replaceAll("_", " ")} · ${user.department || "Department not set"} · ${user.institution?.name || "Institution"}`;
  setPasswordChangeMode(Boolean(user.must_change_password));
}

function showLecturerAuth() {
  $("#lecturer-auth-panel").classList.remove("hidden");
  $("#lecturer-dashboard").classList.add("hidden");
}

function formJson(form) {
  return Object.fromEntries(new FormData(form).entries());
}

$("#lecturer-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setMessage($("#lecturer-auth-message"), "Signing in…");
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formJson(event.currentTarget)),
    });
    state.lecturerUser = result.user;
    event.target?.reset?.();
    setMessage($("#lecturer-auth-message"));
    showLecturerDashboard(result.user);
    if (!result.user.must_change_password) {
      await Promise.all([loadCourses(), loadSubmissions()]);
    }
  } catch (error) {
    setMessage($("#lecturer-auth-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});

$("#lecturer-activation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formJson(event.currentTarget);
  if (data.new_password !== data.confirm_password) {
    setMessage($("#lecturer-auth-message"), "The passwords do not match.", "error");
    return;
  }
  if (data.recovery_pin !== data.confirm_recovery_pin) {
    setMessage($("#lecturer-auth-message"), "The recovery PINs do not match.", "error");
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  setMessage($("#lecturer-auth-message"), "Activating account…");
  try {
    const result = await api("/api/auth/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: data.email,
        setup_code: data.setup_code,
        new_password: data.new_password,
        recovery_pin: data.recovery_pin,
      }),
    });
    state.lecturerUser = result.user;
    event.target?.reset?.();
    showLecturerDashboard(result.user);
    setMessage($("#lecturer-message"), "Account activated. You are now signed in.", "success");
    await Promise.all([loadCourses(), loadSubmissions()]);
  } catch (error) {
    setMessage($("#lecturer-auth-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});

$("#lecturer-password-reset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formJson(event.currentTarget);
  if (data.new_password !== data.confirm_password) {
    setMessage($("#lecturer-auth-message"), "The new passwords do not match.", "error");
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  setMessage($("#lecturer-auth-message"), "Resetting password…");
  try {
    const result = await api("/api/auth/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: data.email,
        recovery_pin: data.recovery_pin,
        new_password: data.new_password,
      }),
    });
    state.lecturerUser = result.user;
    event.target?.reset?.();
    showLecturerDashboard(result.user);
    setMessage($("#lecturer-message"), "Password reset successfully. You are now signed in.", "success");
    await Promise.all([loadCourses(), loadSubmissions()]);
  } catch (error) {
    setMessage($("#lecturer-auth-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});

$("#show-change-password").addEventListener("click", () => {
  $("#change-password-panel").classList.toggle("hidden");
  $("#change-password-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("#change-password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formJson(event.currentTarget);
  if (data.new_password !== data.confirm_password) {
    setMessage($("#password-change-message"), "The new passwords do not match.", "error");
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: data.current_password, new_password: data.new_password }),
    });
    event.target?.reset?.();
    state.lecturerUser.must_change_password = false;
    setPasswordChangeMode(false);
    $("#change-password-panel").classList.add("hidden");
    $("#lecturer-main-content").classList.remove("hidden");
    setMessage($("#lecturer-message"), "Password changed successfully.", "success");
    await Promise.all([loadCourses(), loadSubmissions()]);
  } catch (error) {
    setMessage($("#password-change-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});

$("#lecturer-logout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) {
    // The cookie may already have expired.
  }
  state.lecturerUser = null;
  state.courses = [];
  stopLiveExamMonitor();
  state.liveMonitorCourseId = null;
  clearSnapshotObjectUrls();
  showLecturerAuth();
  setMessage($("#lecturer-auth-message"), "You have signed out.", "success");
});

const milestonesEnabledInput = document.querySelector('#course-create-form input[name="milestones_enabled"]');
milestonesEnabledInput?.addEventListener("change", (event) => {
  $("#milestone-titles-field")?.classList.toggle("hidden", !event.target.checked);
});

$("#course-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setMessage($("#lecturer-message"), "Creating course…");
  try {
    const raw = formJson(event.currentTarget);
    const milestoneTitles = String(raw.milestone_titles_text || "")
      .split(/\n+/)
      .map((item) => item.trim())
      .filter(Boolean);
    const payload = {
      course_code: raw.course_code,
      title: raw.title,
      academic_year: raw.academic_year,
      semester: raw.semester,
      assessment_question_count: Number(raw.assessment_question_count || 20),
      max_attempts: Number(raw.max_attempts || 1),
      checkpoint_question_count: Number(raw.checkpoint_question_count || 3),
      milestones_enabled: Boolean(event.currentTarget.elements.milestones_enabled.checked),
      version_history_enabled: Boolean(event.currentTarget.elements.version_history_enabled.checked),
      ai_use_policy: raw.ai_use_policy || "permitted_with_disclosure",
      ai_policy_guidance: String(raw.ai_policy_guidance || "").trim() || null,
      live_exam_monitoring_enabled: Boolean(event.currentTarget.elements.live_exam_monitoring_enabled.checked),
      live_exam_title: String(raw.live_exam_title || "").trim() || null,
      live_exam_instructions: String(raw.live_exam_instructions || "").trim() || null,
      oral_defence_enabled: Boolean(event.currentTarget.elements.oral_defence_enabled.checked),
      oral_defence_question_count: Number(raw.oral_defence_question_count || 5),
      practical_tasks_enabled: Boolean(event.currentTarget.elements.practical_tasks_enabled.checked),
      practical_task_count: Number(raw.practical_task_count || 1),
      supervisor_meetings_enabled: Boolean(event.currentTarget.elements.supervisor_meetings_enabled.checked),
      cross_version_analysis_enabled: Boolean(event.currentTarget.elements.cross_version_analysis_enabled.checked),
      random_moderation_enabled: Boolean(event.currentTarget.elements.random_moderation_enabled.checked),
      moderation_sample_percent: Number(raw.moderation_sample_percent || 10),
      moderation_variance_threshold: Number(raw.moderation_variance_threshold || 10),
      marker_anomaly_detection_enabled: Boolean(event.currentTarget.elements.marker_anomaly_detection_enabled.checked),
      milestone_titles: milestoneTitles,
    };
    const result = await api("/api/lecturer/courses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    event.target?.reset?.();
    $("#milestone-titles-field")?.classList.add("hidden");
    setMessage($("#lecturer-message"), `Course created. Student enrolment code: ${result.course.enrollment_code}`, "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  } finally {
    button.disabled = false;
  }
});

$("#collaborator-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = formJson(event.currentTarget);
  const button = event.submitter;
  button.disabled = true;
  try {
    await api(`/api/lecturer/courses/${data.course_id}/collaborators`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: data.email, access_level: data.access_level }),
    });
    event.target?.reset?.();
    setMessage($("#lecturer-message"), "Co-lecturer access updated.", "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  } finally {
    button.disabled = false;
  }
});

$("#refresh-courses").addEventListener("click", loadCourses);
$("#refresh-submissions").addEventListener("click", loadSubmissions);
$("#submission-course-filter").addEventListener("change", loadSubmissions);
$("#refresh-progress")?.addEventListener("click", loadLearningProgress);
$("#progress-course-filter")?.addEventListener("change", loadLearningProgress);

async function loadCourses() {
  try {
    const result = await api("/api/lecturer/courses");
    state.courses = result.courses;
    renderCourses(result.courses);
    populateCourseSelectors(result.courses);
  } catch (error) {
    handleLecturerError(error);
  }
}

function populateCourseSelectors(courses) {
  const collaborator = $("#collaborator-course");
  const filter = $("#submission-course-filter");
  const progress = $("#progress-course-filter");
  const taskCourse = $("#task-course");
  const selectedFilter = filter.value;
  const selectedProgress = progress?.value || "";
  const selectedTaskCourse = taskCourse?.value || "";
  collaborator.replaceChildren(new Option("Select course", ""));
  filter.replaceChildren(new Option("All my courses", ""));
  progress?.replaceChildren(new Option("Select a course", ""));
  taskCourse?.replaceChildren(new Option("Select course", ""));
  courses.forEach((course) => {
    const label = `${course.course_code} · ${course.title}`;
    if (["owner", "institution_admin"].includes(course.my_access_level)) {
      collaborator.appendChild(new Option(label, course.id));
    }
    if (["owner", "co_lecturer", "institution_admin"].includes(course.my_access_level)) {
      taskCourse?.appendChild(new Option(label, course.id));
    }
    filter.appendChild(new Option(label, course.id));
    progress?.appendChild(new Option(label, course.id));
  });
  if (courses.some((course) => course.id === selectedFilter)) filter.value = selectedFilter;
  if (progress && courses.some((course) => course.id === selectedProgress)) progress.value = selectedProgress;
  if (taskCourse && courses.some((course) => course.id === selectedTaskCourse)) taskCourse.value = selectedTaskCourse;
}

async function loadTaskFeatureOptions() {
  const courseId = $("#task-course")?.value;
  const container = $("#task-feature-options");
  if (!courseId || !container) {
    if (container) container.innerHTML = "<small>Select a course to load administrator-approved features.</small>";
    return;
  }
  try {
    const result = await api(`/api/lecturer/courses/${courseId}/tasks`);
    container.replaceChildren();
    (result.optional_feature_catalog || []).forEach((feature) => {
      const label = document.createElement("label");
      label.className = "checkbox-setting";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.featureKey = feature.key;
      input.disabled = feature.activation_mode !== "lecturer_allowed";
      input.checked = feature.activation_mode === "always_on";
      const text = document.createElement("span");
      const rule = {
        disabled: "disabled by administrator",
        admin_only: "administrator activation required",
        lecturer_allowed: "lecturer may activate",
        always_on: "required by administrator",
      }[feature.activation_mode];
      text.textContent = `${feature.label} — ${rule}`;
      label.title = feature.description;
      label.append(input, text);
      container.appendChild(label);
    });
  } catch (error) {
    setMessage($("#task-create-message"), error.message, "error");
  }
}

$("#task-course")?.addEventListener("change", loadTaskFeatureOptions);
$("#task-mode")?.addEventListener("change", (event) => {
  $("#exam-source-fields")?.classList.toggle("hidden", event.target.value !== "standalone_exam");
});

$("#assessment-task-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  const data = formJson(form);
  const features = {};
  $("#task-feature-options")?.querySelectorAll('input[data-feature-key]:not(:disabled)').forEach((input) => {
    features[input.dataset.featureKey] = input.checked;
  });
  const sourceFile = form.elements.exam_source_file?.files?.[0];
  const questionFile = form.elements.exam_questions_file?.files?.[0];
  if (sourceFile && questionFile) {
    setMessage($("#task-create-message"), "Choose either a source document or a question-bank JSON file, not both.", "warning");
    return;
  }
  button.disabled = true;
  try {
    const created = await api(`/api/lecturer/courses/${data.course_id}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: data.title,
        instructions: data.instructions || null,
        mode: data.mode,
        assessment_question_count: Number(data.assessment_question_count),
        max_attempts: Number(data.max_attempts),
        features,
      }),
    });
    if (data.mode === "standalone_exam" && (sourceFile || questionFile)) {
      const upload = new FormData();
      upload.set("title", data.exam_source_title || data.title);
      upload.set("file", sourceFile || questionFile);
      const route = questionFile ? "question-bank" : "exam-source";
      await api(`/api/lecturer/courses/${data.course_id}/tasks/${created.task.id}/${route}`, {
        method: "POST",
        body: upload,
      });
    }
    form.reset();
    $("#exam-source-fields")?.classList.add("hidden");
    $("#task-feature-options").innerHTML = "<small>Select a course to load administrator-approved features.</small>";
    setMessage($("#task-create-message"), "Assessment task created. Any selected question source is now being prepared.", "success");
    await loadCourses();
  } catch (error) {
    setMessage($("#task-create-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});


function renderCourses(courses) {
  const body = $("#courses-body");
  body.replaceChildren();
  if (!courses.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty-state">Create your first course to receive student submissions.</td></tr>';
    return;
  }
  courses.forEach((course) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td><strong></strong><small></small></td>
      <td><code class="enrollment-code"></code></td>
      <td><strong class="question-count"></strong><small></small></td>
      <td class="process-cell"></td>
      <td class="course-lecturers"></td>
      <td></td>
      <td><div class="action-row"></div></td>`;
    row.children[0].querySelector("strong").textContent = course.course_code;
    row.children[0].querySelector("small").textContent = course.title;
    row.children[1].querySelector("strong").textContent = course.academic_year;
    row.children[1].querySelector("small").textContent = course.semester;
    row.children[2].querySelector("code").textContent = course.enrollment_code;
    row.children[3].querySelector("strong").textContent = `${course.assessment_question_count || 20} final`;
    row.children[3].querySelector("small").textContent = `${course.checkpoint_question_count || 3} per milestone`;
    const process = [];
    process.push(course.milestones_enabled ? `${course.milestones.length} milestone(s)` : "Milestones off");
    process.push(course.version_history_enabled ? "Versions on" : "Versions off");
    process.push(course.ai_policy?.label || "AI permitted with disclosure");
    if (course.live_exam_monitoring_enabled) process.push("Live exam monitor on");
    const phase2Labels = [];
    if (course.oral_defence_enabled) phase2Labels.push("Viva");
    if (course.practical_tasks_enabled) phase2Labels.push("Practical");
    if (course.supervisor_meetings_enabled) phase2Labels.push("Meetings");
    if (course.cross_version_analysis_enabled) phase2Labels.push("Version analysis");
    if (course.random_moderation_enabled) phase2Labels.push(`${course.moderation_sample_percent}% moderation`);
    if (course.marker_anomaly_detection_enabled) phase2Labels.push("Marker analytics");
    if (phase2Labels.length) process.push(`Phase 2: ${phase2Labels.join(", ")}`);
    if ((course.assessment_tasks || []).length) {
      process.push(`${course.assessment_tasks.length} task profile(s)`);
      process.push(...course.assessment_tasks.map((task) => `${task.title}: ${task.max_attempts || 1} attempt(s), ${Object.values(task.effective_features || {}).filter(Boolean).length} optional feature(s)`));
    }
    row.children[4].textContent = process.join(" · ");
    row.children[5].textContent = course.lecturers.map((item) => `${item.full_name} (${item.access_level.replaceAll("_", " ")})`).join(", ");
    row.children[6].textContent = String(course.submission_count);
    const actions = row.children[7].querySelector(".action-row");
    const copy = document.createElement("button");
    copy.textContent = "Copy code";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(course.enrollment_code);
      setMessage($("#lecturer-message"), `${course.enrollment_code} copied.`, "success");
    });
    actions.appendChild(copy);
    const progress = document.createElement("button");
    progress.textContent = "Progress";
    progress.addEventListener("click", async () => {
      $("#progress-course-filter").value = course.id;
      await loadLearningProgress();
      $("#learning-progress-body").closest("section")?.scrollIntoView({ behavior: "smooth" });
    });
    actions.appendChild(progress);
    if (course.live_exam_monitoring_enabled || (course.assessment_tasks || []).some((task) => task.effective_features?.live_exam_monitoring)) {
      const liveMonitor = document.createElement("button");
      liveMonitor.textContent = "Live monitor";
      liveMonitor.addEventListener("click", () => openLiveExamMonitor(course));
      actions.appendChild(liveMonitor);
    }
    if (["owner", "co_lecturer", "institution_admin"].includes(course.my_access_level) && (course.assessment_tasks || []).length) {
      const taskAttempts = document.createElement("button");
      taskAttempts.textContent = "Attempt limits";
      taskAttempts.addEventListener("click", () => configureTaskAttemptLimit(course));
      actions.appendChild(taskAttempts);
      const resetCandidate = document.createElement("button");
      resetCandidate.textContent = "Reset candidate";
      resetCandidate.addEventListener("click", () => resetCandidateTaskAttempts(course));
      actions.appendChild(resetCandidate);
    }
    if (["owner", "institution_admin"].includes(course.my_access_level)) {
      const questions = document.createElement("button");
      questions.textContent = "Settings";
      questions.addEventListener("click", () => updateCourseLearningSettings(course));
      actions.appendChild(questions);
      const milestone = document.createElement("button");
      milestone.textContent = "Add milestone";
      milestone.addEventListener("click", () => addCourseMilestone(course));
      actions.appendChild(milestone);
      if (course.marker_anomaly_detection_enabled) {
        const analytics = document.createElement("button");
        analytics.textContent = "Marker analytics";
        analytics.addEventListener("click", () => loadMarkerAnomalies(course, true));
        actions.appendChild(analytics);
      }
      const regenerate = document.createElement("button");
      regenerate.textContent = "New code";
      regenerate.addEventListener("click", () => regenerateCourseCode(course));
      actions.appendChild(regenerate);
    }
    body.appendChild(row);
  });
}

function stopLiveExamMonitor() {
  if (state.liveMonitorTimer) clearTimeout(state.liveMonitorTimer);
  state.liveMonitorTimer = null;
}

async function openLiveExamMonitor(course) {
  stopLiveExamMonitor();
  state.liveMonitorCourseId = course.id;
  $("#live-exam-monitor-panel").classList.remove("hidden");
  $("#live-exam-monitor-panel").scrollIntoView({ behavior: "smooth" });
  await loadLiveExamMonitor();
}

function renderLiveExamMonitor(result) {
  const sessions = result.sessions || [];
  $("#live-exam-monitor-title").textContent = result.exam?.title || "Live examination monitoring";
  $("#live-exam-monitor-instructions").textContent = result.exam?.instructions || "Monitor connection, progress, camera state and review indicators in near real time.";
  $("#live-exam-last-updated").textContent = result.server_time ? `Updated ${new Date(result.server_time).toLocaleTimeString()}` : "";
  $("#live-exam-disclaimer").textContent = result.disclaimer || "Monitoring indicators require contextual human review.";

  const summary = $("#live-exam-summary");
  summary.replaceChildren();
  const metrics = [
    [sessions.length, "sessions in last 12 hours"],
    [sessions.filter((item) => item.connected).length, "connected now"],
    [sessions.filter((item) => ["interrupted", "locked"].includes(item.status)).length, "require attention"],
    [sessions.filter((item) => item.status === "completed").length, "completed"],
  ];
  metrics.forEach(([value, label]) => {
    const block = document.createElement("div");
    block.innerHTML = "<strong></strong><span></span>";
    block.querySelector("strong").textContent = value;
    block.querySelector("span").textContent = label;
    summary.appendChild(block);
  });

  const body = $("#live-exam-monitor-body");
  body.replaceChildren();
  if (!sessions.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty-state">No examination session has started in the last 12 hours.</td></tr>';
    return;
  }
  sessions.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td><span class="status-pill"></span><small></small></td>
      <td><strong></strong><small></small></td>
      <td><span class="status-pill"></span></td>
      <td><strong></strong><small></small></td>
      <td><strong></strong><small></small></td>
      <td><span></span></td>
      <td><button type="button" class="secondary-button">Review</button></td>`;
    row.children[0].querySelector("strong").textContent = item.student_name;
    row.children[0].querySelector("small").textContent = item.student_id;
    const connection = row.children[1].querySelector("span");
    connection.textContent = item.connected ? "Connected" : String(item.status || "unknown").replaceAll("_", " ");
    connection.classList.add(item.connected ? "active" : item.status || "warning");
    row.children[1].querySelector("small").textContent = item.seconds_since_last_seen == null ? "No heartbeat yet" : `${item.seconds_since_last_seen}s since heartbeat`;
    row.children[2].querySelector("strong").textContent = item.status === "completed" ? `${Number(item.score || 0).toFixed(0)}%` : `${Math.min(item.current_position || 1, item.question_count || 1)}/${item.question_count || 0}`;
    row.children[2].querySelector("small").textContent = String(item.status || "unknown").replaceAll("_", " ");
    const camera = row.children[3].querySelector("span");
    camera.textContent = String(item.camera_status || "not captured").replaceAll("_", " ");
    camera.classList.add(item.camera_status === "captured" ? "active" : "warning");
    row.children[4].querySelector("strong").textContent = `${item.critical_event_count || 0} critical`;
    row.children[4].querySelector("small").textContent = `${item.unresolved_event_count || 0} unresolved · ${item.monitoring_event_count || 0} total`;
    row.children[5].querySelector("strong").textContent = item.interruption_count || 0;
    row.children[5].querySelector("small").textContent = formatDuration(item.total_offline_seconds || 0);
    row.children[6].querySelector("span").textContent = item.started_at ? new Date(item.started_at).toLocaleTimeString() : "—";
    row.children[7].querySelector("button").addEventListener("click", () => reviewAssessment(item.assessment_id));
    body.appendChild(row);
  });
}

async function loadLiveExamMonitor() {
  const courseId = state.liveMonitorCourseId;
  if (!courseId) return;
  stopLiveExamMonitor();
  try {
    const result = await api(`/api/lecturer/courses/${courseId}/live-exam-monitor`);
    if (state.liveMonitorCourseId !== courseId) return;
    renderLiveExamMonitor(result);
    state.liveMonitorTimer = setTimeout(loadLiveExamMonitor, Math.max(3000, Number(result.refresh_seconds || 5) * 1000));
  } catch (error) {
    handleLecturerError(error);
  }
}

$("#refresh-live-exam-monitor")?.addEventListener("click", loadLiveExamMonitor);
$("#close-live-exam-monitor")?.addEventListener("click", () => {
  stopLiveExamMonitor();
  state.liveMonitorCourseId = null;
  $("#live-exam-monitor-panel")?.classList.add("hidden");
});

function chooseCourseTask(course, actionLabel) {
  const tasks = course.assessment_tasks || [];
  if (!tasks.length) return null;
  if (tasks.length === 1) return tasks[0];
  const choices = tasks.map((task, index) => `${index + 1}. ${task.title} (${task.mode.replaceAll("_", " ")})`).join("\n");
  const selected = prompt(`${actionLabel}\n\n${choices}\n\nEnter the task number:`, "1");
  if (selected == null) return null;
  const index = Number(selected) - 1;
  if (!Number.isInteger(index) || index < 0 || index >= tasks.length) {
    setMessage($("#lecturer-message"), "Select a valid assessment task number.", "error");
    return null;
  }
  return tasks[index];
}

async function configureTaskAttemptLimit(course) {
  const task = chooseCourseTask(course, "Choose the task whose attempt limit you want to change.");
  if (!task) return;
  const input = prompt(
    `Maximum starts per student for “${task.title}”:`,
    String(task.max_attempts || 1)
  );
  if (input == null) return;
  const maxAttempts = Number(input);
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    setMessage($("#lecturer-message"), "Maximum attempts must be a positive whole number.", "error");
    return;
  }
  try {
    await api(`/api/lecturer/courses/${course.id}/tasks/${task.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_attempts: maxAttempts }),
    });
    setMessage($("#lecturer-message"), `${task.title} now permits ${maxAttempts} attempt(s) per student.`, "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  }
}

async function resetCandidateTaskAttempts(course) {
  const task = chooseCourseTask(course, "Choose the task to reset for a candidate.");
  if (!task) return;
  const candidateId = prompt(`Candidate/student ID to reset for “${task.title}”:`, "");
  if (candidateId == null || !candidateId.trim()) return;
  if (!confirm(
    `Reset all attempts for ${candidateId.trim()} in “${task.title}”? Answers, monitoring evidence, screen recordings and environment-audio recordings for those attempts will be deleted. This cannot be undone.`
  )) return;
  try {
    const result = await api(
      `/api/lecturer/courses/${course.id}/tasks/${task.id}/attempts?candidate_id=${encodeURIComponent(candidateId.trim())}`,
      { method: "DELETE" }
    );
    setMessage(
      $("#lecturer-message"),
      `${candidateId.trim()} was reset for ${task.title}. ${result.deleted_attempts} prior attempt(s) were removed and the candidate may start afresh.`,
      "success"
    );
    await Promise.all([loadCourses(), loadSubmissions()]);
  } catch (error) {
    handleLecturerError(error);
  }
}

async function updateCourseLearningSettings(course) {
  const finalCountInput = prompt(`Final assessment questions for ${course.course_code} (any positive whole number):`, String(course.assessment_question_count || 20));
  if (finalCountInput == null) return;
  const finalCount = Number(finalCountInput);
  if (!Number.isInteger(finalCount) || finalCount < 1) {
    setMessage($("#lecturer-message"), "Final questions must be a positive whole number.", "error");
    return;
  }
  const maxAttemptsInput = prompt(
    "Maximum starts per student for legacy assessments without a task profile:",
    String(course.max_attempts || 1)
  );
  if (maxAttemptsInput == null) return;
  const maxAttempts = Number(maxAttemptsInput);
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    setMessage($("#lecturer-message"), "Maximum attempts must be a positive whole number.", "error");
    return;
  }
  const checkpointInput = prompt("Questions for each milestone checkpoint (any positive whole number):", String(course.checkpoint_question_count || 3));
  if (checkpointInput == null) return;
  const checkpointCount = Number(checkpointInput);
  if (!Number.isInteger(checkpointCount) || checkpointCount < 1) {
    setMessage($("#lecturer-message"), "Checkpoint questions must be a positive whole number.", "error");
    return;
  }
  const milestonesEnabled = confirm("Select OK to enable Kanokware learning milestones. Select Cancel for final-submission-only or manually monitored learning process.");
  const versionHistoryEnabled = confirm("Select OK to keep document version history in Kanokware. Select Cancel to leave version tracking off.");
  const allowedAiPolicies = ["prohibited", "restricted", "permitted_with_disclosure", "required"];
  const aiPolicyInput = prompt(
    "Course AI-use rule. Enter prohibited, restricted, permitted_with_disclosure, or required:",
    course.ai_policy?.level || "permitted_with_disclosure"
  );
  if (aiPolicyInput == null) return;
  const aiUsePolicy = aiPolicyInput.trim().toLowerCase();
  if (!allowedAiPolicies.includes(aiUsePolicy)) {
    setMessage($("#lecturer-message"), "Enter a valid AI-use rule: prohibited, restricted, permitted_with_disclosure, or required.", "error");
    return;
  }
  const aiGuidanceInput = prompt(
    "Student-facing AI-use guidance. Leave blank if no additional guidance is needed:",
    course.ai_policy?.guidance || ""
  );
  if (aiGuidanceInput == null) return;
  const liveExamMonitoringEnabled = confirm("Select OK to enable the optional live examination monitoring dashboard for this course.");
  const liveExamTitleInput = liveExamMonitoringEnabled
    ? prompt("Live examination title:", course.live_exam_title || `${course.course_code} live examination`)
    : (course.live_exam_title || "");
  if (liveExamTitleInput == null) return;
  const liveExamInstructionsInput = liveExamMonitoringEnabled
    ? prompt("Candidate-facing live examination monitoring instructions:", course.live_exam_instructions || "")
    : (course.live_exam_instructions || "");
  if (liveExamInstructionsInput == null) return;
  const oralDefenceEnabled = confirm("Select OK to enable the optional oral-defence module.");
  const oralCountInput = oralDefenceEnabled ? prompt("Oral-defence questions (any positive whole number):", String(course.oral_defence_question_count || 5)) : String(course.oral_defence_question_count || 5);
  if (oralCountInput == null) return;
  const oralCount = Number(oralCountInput);
  if (!Number.isInteger(oralCount) || oralCount < 1) {
    setMessage($("#lecturer-message"), "Oral-defence questions must be a positive whole number.", "error");
    return;
  }
  const practicalTasksEnabled = confirm("Select OK to enable optional practical reproduction tasks.");
  const practicalCountInput = practicalTasksEnabled ? prompt("Practical tasks per assessment, 1 to 5:", String(course.practical_task_count || 1)) : String(course.practical_task_count || 1);
  if (practicalCountInput == null) return;
  const practicalCount = Number(practicalCountInput);
  if (!Number.isInteger(practicalCount) || practicalCount < 1 || practicalCount > 5) {
    setMessage($("#lecturer-message"), "Practical task count must be a whole number from 1 to 5.", "error");
    return;
  }
  const supervisorMeetingsEnabled = confirm("Select OK to enable optional supervisor meeting records.");
  const crossVersionAnalysisEnabled = confirm("Select OK to enable optional advanced cross-version analysis.");
  const randomModerationEnabled = confirm("Select OK to enable optional random moderation.");
  const moderationPercentInput = randomModerationEnabled ? prompt("Percentage of marked assessments selected for moderation, 1 to 100:", String(course.moderation_sample_percent || 10)) : String(course.moderation_sample_percent || 10);
  if (moderationPercentInput == null) return;
  const moderationPercent = Number(moderationPercentInput);
  if (!Number.isInteger(moderationPercent) || moderationPercent < 1 || moderationPercent > 100) {
    setMessage($("#lecturer-message"), "Moderation percentage must be a whole number from 1 to 100.", "error");
    return;
  }
  const varianceInput = randomModerationEnabled ? prompt("Mark variance requiring attention, 1 to 50 points:", String(course.moderation_variance_threshold || 10)) : String(course.moderation_variance_threshold || 10);
  if (varianceInput == null) return;
  const varianceThreshold = Number(varianceInput);
  if (!Number.isFinite(varianceThreshold) || varianceThreshold < 1 || varianceThreshold > 50) {
    setMessage($("#lecturer-message"), "Moderation variance threshold must be from 1 to 50.", "error");
    return;
  }
  const markerAnomalyDetectionEnabled = confirm("Select OK to enable optional marker anomaly detection.");
  try {
    await api(`/api/lecturer/courses/${course.id}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        assessment_question_count: finalCount,
        max_attempts: maxAttempts,
        checkpoint_question_count: checkpointCount,
        milestones_enabled: milestonesEnabled,
        version_history_enabled: versionHistoryEnabled,
        ai_use_policy: aiUsePolicy,
        ai_policy_guidance: aiGuidanceInput.trim(),
        live_exam_monitoring_enabled: liveExamMonitoringEnabled,
        live_exam_title: liveExamTitleInput.trim(),
        live_exam_instructions: liveExamInstructionsInput.trim(),
        oral_defence_enabled: oralDefenceEnabled,
        oral_defence_question_count: oralCount,
        practical_tasks_enabled: practicalTasksEnabled,
        practical_task_count: practicalCount,
        supervisor_meetings_enabled: supervisorMeetingsEnabled,
        cross_version_analysis_enabled: crossVersionAnalysisEnabled,
        random_moderation_enabled: randomModerationEnabled,
        moderation_sample_percent: moderationPercent,
        moderation_variance_threshold: varianceThreshold,
        marker_anomaly_detection_enabled: markerAnomalyDetectionEnabled,
      }),
    });
    setMessage($("#lecturer-message"), "Course learning-assurance settings updated.", "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  }
}

async function addCourseMilestone(course) {
  const title = prompt(`New milestone title for ${course.course_code}:`, "");
  if (title == null || !title.trim()) return;
  const countInput = prompt("Checkpoint questions for this milestone (any positive whole number):", String(course.checkpoint_question_count || 3));
  if (countInput == null) return;
  const count = Number(countInput);
  if (!Number.isInteger(count) || count < 1) {
    setMessage($("#lecturer-message"), "Enter a positive whole number.", "error");
    return;
  }
  try {
    await api(`/api/lecturer/courses/${course.id}/milestones`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim(), required: true, checkpoint_question_count: count }),
    });
    setMessage($("#lecturer-message"), "Learning milestone added.", "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  }
}


async function regenerateCourseCode(course) {
  if (!confirm(`Generate a new enrolment code for ${course.course_code}? The old code will stop working.`)) return;
  try {
    const result = await api(`/api/lecturer/courses/${course.id}/regenerate-code`, { method: "POST" });
    setMessage($("#lecturer-message"), `New code: ${result.enrollment_code}`, "success");
    await loadCourses();
  } catch (error) {
    handleLecturerError(error);
  }
}

async function loadLearningProgress() {
  const courseId = $("#progress-course-filter")?.value;
  const body = $("#learning-progress-body");
  if (!courseId) {
    body.innerHTML = '<tr><td colspan="8" class="empty-state">Select a course to view learning progress.</td></tr>';
    return;
  }
  try {
    const result = await api(`/api/lecturer/courses/${courseId}/learning-progress`);
    renderLearningProgress(result.students || []);
  } catch (error) {
    handleLecturerError(error);
  }
}

function renderLearningProgress(rows) {
  const body = $("#learning-progress-body");
  body.replaceChildren();
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty-state">No student learning evidence has been submitted for this course.</td></tr>';
    return;
  }
  rows.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td></td><td></td><td></td><td></td><td></td><td></td><td></td>`;
    row.children[0].querySelector("strong").textContent = item.student_name;
    row.children[0].querySelector("small").textContent = item.student_id;
    row.children[1].textContent = item.milestones_total ? `${item.milestones_completed}/${item.milestones_total}` : "Not tracked";
    row.children[2].textContent = String(item.version_count || 1);
    row.children[3].textContent = item.final_submitted ? "Submitted" : "Pending";
    row.children[4].textContent = item.latest_score == null ? "—" : `${Number(item.latest_score).toFixed(0)}%`;
    row.children[5].textContent = (item.latest_ai_use_level || "none").replaceAll("_", " ");
    row.children[6].textContent = String(item.open_alert_count || 0);
    if (item.open_alert_count) row.children[6].classList.add("attention-cell");
    row.children[7].textContent = item.last_activity_at ? new Date(item.last_activity_at).toLocaleString() : "—";
    body.appendChild(row);
  });
}

async function loadSubmissions() {
  const courseId = $("#submission-course-filter").value;
  setMessage($("#lecturer-message"), "Loading submissions…");
  try {
    const suffix = courseId ? `?course_id=${encodeURIComponent(courseId)}` : "";
    const result = await api(`/api/lecturer/submissions${suffix}`);
    renderSubmissions(result.submissions);
    setMessage($("#lecturer-message"), `${result.submissions.length} submission(s) loaded.`, "success");
  } catch (error) {
    handleLecturerError(error);
  }
}

function handleLecturerError(error) {
  if (error.status === 401) {
    state.lecturerUser = null;
    showLecturerAuth();
    setMessage($("#lecturer-auth-message"), "Your session has expired. Sign in again.", "warning");
    return;
  }
  setMessage($("#lecturer-message"), error.message, "error");
}

function renderSubmissions(rows) {
  clearSnapshotObjectUrls();
  const body = $("#submissions-body");
  body.replaceChildren();
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty-state">No submissions found for the selected course.</td></tr>';
    return;
  }
  rows.forEach((item) => {
    const row = document.createElement("tr");
    const score = item.score == null ? "—" : `${Number(item.score).toFixed(0)}%`;
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td><strong></strong><small></small></td>
      <td><span class="status-pill"></span></td>
      <td>${score}</td>
      <td></td>
      <td class="snapshot-cell"></td>
      <td class="monitoring-cell"></td>
      <td class="interruption-cell"></td>
      <td><div class="action-row"></div></td>`;
    row.children[0].querySelector("strong").textContent = item.student_name;
    row.children[0].querySelector("small").textContent = item.student_id;
    row.children[1].querySelector("strong").textContent = item.title;
    const stage = item.submission_stage === "milestone" ? (item.milestone || "Milestone") : "Final";
    row.children[1].querySelector("small").textContent = `${item.course} · ${stage} · Version ${item.version_number || 1} · ${item.word_count.toLocaleString()} words · AI: ${(item.ai_use_level || "none").replaceAll("_", " ")}`;
    const status = row.children[2].querySelector("span");
    status.textContent = item.assessment_status || item.status;
    status.classList.add(item.assessment_status || item.status);
    row.children[4].replaceChildren();
    const decision = document.createElement("span");
    decision.textContent = item.decision || "—";
    row.children[4].appendChild(decision);
    if (item.learning_alert_count) {
      const alertBadge = document.createElement("small");
      alertBadge.className = item.learning_alert_critical_count ? "learning-alert-badge critical" : "learning-alert-badge";
      alertBadge.textContent = `${item.learning_alert_count} learning alert${item.learning_alert_count === 1 ? "" : "s"}`;
      row.children[4].appendChild(alertBadge);
    }
    renderSnapshotCell(row.children[5], item);
    renderMonitoringCell(row.children[6], item);
    renderInterruptionCell(row.children[7], item);
    const actions = row.children[8].querySelector(".action-row");
    if (item.assessment_id) {
      const review = document.createElement("button");
      review.textContent = "Review";
      review.addEventListener("click", () => reviewAssessment(item.assessment_id));
      actions.appendChild(review);
      if (item.assessment_status === "completed") {
        const report = document.createElement("button");
        report.textContent = "PDF";
        report.addEventListener("click", () => downloadReport(item.assessment_id, item.student_id));
        actions.appendChild(report);
      } else {
        const reset = document.createElement("button");
        reset.textContent = "Reset";
        reset.addEventListener("click", () => resetAttempt(item.assessment_id, item.student_name));
        actions.appendChild(reset);
      }
    }
    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.className = "delete";
    remove.addEventListener("click", () => deleteSubmission(item.document_id, item.student_name));
    actions.appendChild(remove);
    body.appendChild(row);
  });
}

function renderMonitoringCell(cell, item) {
  cell.replaceChildren();
  if (!item.assessment_id) {
    cell.textContent = "No attempt";
    return;
  }
  const count = Number(item.monitoring_event_count || 0);
  const unresolved = Number(item.monitoring_unresolved_count || 0);
  const critical = Number(item.monitoring_critical_count || 0);
  const badge = document.createElement("span");
  badge.className = "monitoring-indicator";
  badge.textContent = count ? `${count} warning${count === 1 ? "" : "s"}` : "No warnings";
  if (critical) badge.classList.add("critical");
  else if (unresolved || count) badge.classList.add("warning");
  cell.appendChild(badge);
}

function renderInterruptionCell(cell, item) {
  cell.replaceChildren();
  if (!item.assessment_id) {
    cell.textContent = "No attempt";
    return;
  }
  const count = Number(item.interruption_count || 0);
  const offline = Number(item.total_offline_seconds || 0) + Number(item.current_offline_seconds || 0);
  const badge = document.createElement("span");
  badge.className = "interruption-indicator";
  if (item.assessment_status === "locked") {
    badge.textContent = "Locked";
    badge.classList.add("critical");
  } else if (item.assessment_status === "interrupted") {
    badge.textContent = `Interrupted · ${formatDuration(offline)}`;
    badge.classList.add("critical");
  } else if (count) {
    badge.textContent = `${count} interruption${count === 1 ? "" : "s"} · ${formatDuration(offline)}`;
    badge.classList.add("warning");
  } else {
    badge.textContent = "Continuous";
  }
  if (item.interruption_excused) badge.classList.add("excused");
  cell.appendChild(badge);
}

function clearSnapshotObjectUrls() {
  state.snapshotUrls.forEach((url) => URL.revokeObjectURL(url));
  state.snapshotUrls = [];
}

function renderSnapshotCell(cell, item) {
  cell.replaceChildren();
  if (!item.assessment_id || !item.snapshot_available) {
    const placeholder = document.createElement("span");
    placeholder.className = "snapshot-placeholder";
    placeholder.textContent = !item.assessment_id ? "No attempt" : item.assessment_status === "completed" ? "Not captured" : "Awaiting capture";
    cell.appendChild(placeholder);
    return;
  }
  const image = document.createElement("img");
  image.className = "snapshot-thumb";
  image.alt = `Webcam snapshot for ${item.student_name}`;
  image.loading = "lazy";
  const time = document.createElement("small");
  time.className = "snapshot-time";
  time.textContent = item.snapshot_captured_at ? new Date(item.snapshot_captured_at).toLocaleString() : "Captured";
  cell.append(image, time);
  loadSnapshotImage(item.assessment_id, image);
}

async function loadSnapshotImage(assessmentId, image) {
  try {
    const response = await fetch(`/api/lecturer/assessments/${assessmentId}/snapshot`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("Snapshot unavailable");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    state.snapshotUrls.push(url);
    image.src = url;
    image.addEventListener("click", () => window.open(url, "_blank", "noopener"));
    image.title = "Open webcam snapshot";
    image.style.cursor = "zoom-in";
  } catch (_) {
    image.replaceWith(Object.assign(document.createElement("span"), { className: "snapshot-placeholder", textContent: "Could not load" }));
  }
}

function evidenceMetricValue(item) {
  if (!item) return "Pending";
  const status = String(item.status || "pending").replaceAll("_", " ");
  return item.score == null ? status : `${Number(item.score).toFixed(1)}% · ${status}`;
}

function renderEvidenceProfile(profile) {
  const grid = $("#evidence-profile-grid");
  const recommendation = $("#evidence-recommendation");
  const form = $("#assurance-decision-form");
  if (!grid || !recommendation || !form) return;
  grid.replaceChildren();
  const rows = [
    ["Document knowledge", evidenceMetricValue(profile?.document_knowledge), profile?.document_knowledge?.status],
    ["Independent reasoning", evidenceMetricValue(profile?.independent_reasoning), profile?.independent_reasoning?.status],
    ["Application and transfer", evidenceMetricValue(profile?.application_transfer), profile?.application_transfer?.status],
    ["AI-policy alignment", String(profile?.ai_policy_alignment?.status || "pending").replaceAll("_", " "), profile?.ai_policy_alignment?.status],
    ["Session evidence", String(profile?.session_evidence?.status || "pending").replaceAll("_", " "), profile?.session_evidence?.status],
  ];
  rows.forEach(([label, value, status]) => {
    const card = document.createElement("article");
    card.className = `evidence-profile-card ${String(status || "pending").replaceAll("_", "-")}`;
    card.innerHTML = "<span></span><strong></strong>";
    card.querySelector("span").textContent = label;
    card.querySelector("strong").textContent = value;
    grid.appendChild(card);
  });
  recommendation.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = profile?.recommendation?.label || "Assessment evidence pending";
  const list = document.createElement("ul");
  (profile?.recommendation?.reasons || []).forEach((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    list.appendChild(item);
  });
  const disclaimer = document.createElement("small");
  disclaimer.textContent = profile?.recommendation?.disclaimer || "This profile supports human academic judgement.";
  recommendation.append(heading, list, disclaimer);

  const saved = profile?.lecturer_decision || {};
  form.elements.decision.value = saved.code || "evidence_consistent";
  form.elements.note.value = saved.note || "";
}

async function reviewAssessment(assessmentId) {
  try {
    const result = await api(`/api/lecturer/assessments/${assessmentId}`);
    state.currentReviewAssessmentId = assessmentId;
    state.currentReviewSummary = result.summary;
    state.currentReviewData = result;
    $("#review-panel").classList.remove("hidden");
    $("#review-title").textContent = `${result.summary.student_name} · ${result.summary.document_title}`;

    const summary = $("#review-summary");
    summary.replaceChildren();
    const metrics = [
      [result.summary.score == null ? "In progress" : `${Number(result.summary.score).toFixed(0)}%`, "mastery score"],
      [result.summary.correct_count ?? "—", "correct answers"],
      [result.summary.submission_stage === "milestone" ? (result.summary.milestone || "Milestone") : "Final", "submission stage"],
      [`Version ${result.summary.version_number || 1}`, "document version"],
      [(result.summary.ai_use_level || "none").replaceAll("_", " "), "AI use"],
      [result.summary.monitoring_event_count ?? 0, "webcam warnings"],
      [result.summary.interruption_count ?? 0, "interruptions"],
      [formatDuration(result.summary.total_offline_seconds || 0), "offline time"],
    ];
    metrics.forEach(([value, label]) => {
      const block = document.createElement("div");
      block.innerHTML = "<strong></strong><span></span>";
      block.querySelector("strong").textContent = value;
      block.querySelector("span").textContent = label;
      summary.appendChild(block);
    });
    $("#evidence-profile-panel")?.classList.toggle("hidden", !result.evidence_profile);
    if (result.evidence_profile) renderEvidenceProfile(result.evidence_profile);

    const justification = $("#review-justification");
    justification.replaceChildren();
    const evidenceItems = [
      ["Understanding summary", result.justification?.understanding_summary],
      ["Key decisions and reasons", result.justification?.key_decisions],
      ["Main contribution or finding", result.justification?.main_contribution],
      ["Limitation identified", result.justification?.stated_limitation],
      ["AI-use declaration", (result.justification?.ai_use_level || "none").replaceAll("_", " ")],
      ["AI-use details", result.justification?.ai_use_details || "No AI use declared"],
    ];
    evidenceItems.forEach(([label, value]) => {
      const card = document.createElement("article");
      card.innerHTML = "<strong></strong><p></p>";
      card.querySelector("strong").textContent = label;
      card.querySelector("p").textContent = value || "Not provided";
      justification.appendChild(card);
    });

    const versions = $("#review-versions");
    versions.replaceChildren();
    if ((result.version_history || []).length > 1) {
      const heading = document.createElement("h4");
      heading.textContent = "Version history";
      versions.appendChild(heading);
      const list = document.createElement("div");
      list.className = "version-list";
      result.version_history.forEach((item) => {
        const line = document.createElement("div");
        line.innerHTML = "<strong></strong><span></span>";
        line.querySelector("strong").textContent = `Version ${item.version_number} · ${item.submission_stage === "milestone" ? (item.milestone || "Milestone") : "Final"}`;
        line.querySelector("span").textContent = `${Number(item.word_count || 0).toLocaleString()} words · ${item.created_at ? new Date(item.created_at).toLocaleString() : ""}`;
        list.appendChild(line);
      });
      versions.appendChild(list);
      if (result.version_comparison) {
        const comparison = document.createElement("p");
        comparison.className = "version-comparison";
        comparison.textContent = `Compared with the previous version: ${result.version_comparison.word_count_change >= 0 ? "+" : ""}${result.version_comparison.word_count_change} words and ${result.version_comparison.similarity_percent}% textual similarity.`;
        versions.appendChild(comparison);
      }
    }

    renderLearningAlerts(result.learning_alerts || []);
    renderRubric(result.rubric || [], result.rubric_mark, result.mark_audit || []);
    renderPhase2Modules(result);

    const interruptionPanel = $("#review-interruption");
    const interruptionCount = Number(result.summary.interruption_count || 0);
    const interruptedStatus = ["interrupted", "locked"].includes(result.summary.status);
    interruptionPanel.classList.toggle("hidden", !interruptionCount && !interruptedStatus);
    if (!interruptionPanel.classList.contains("hidden")) {
      const title = result.summary.status === "locked" ? "Assessment locked for review" : result.summary.status === "interrupted" ? "Assessment currently interrupted" : "Assessment resumed after interruption";
      $("#review-interruption-title").textContent = title;
      const reason = (result.summary.lock_reason || result.summary.last_interruption_reason || "connection interruption").replaceAll("_", " ");
      const note = result.summary.interruption_note ? ` Note: ${result.summary.interruption_note}` : "";
      $("#review-interruption-text").textContent = `${interruptionCount} interruption(s), ${formatDuration(result.summary.total_offline_seconds || 0)} recorded offline, ${result.summary.resume_count || 0} successful resume(s). Reason: ${reason}.${note}`;
      $("#allow-resume-button").classList.toggle("hidden", !interruptedStatus);
      $("#finish-interrupted-button").classList.toggle("hidden", !interruptedStatus);
      $("#excuse-interruption-button").textContent = result.summary.interruption_excused ? "Excused" : "Mark excused";
      $("#excuse-interruption-button").disabled = Boolean(result.summary.interruption_excused);
    }

    const monitoringPanel = $("#review-monitoring");
    const monitoringList = $("#review-monitoring-events");
    monitoringList.replaceChildren();
    const monitoringEvents = result.monitoring_events || [];
    monitoringPanel.classList.toggle("hidden", monitoringEvents.length === 0);
    monitoringEvents.forEach((event) => {
      const card = document.createElement("article");
      card.className = "monitoring-event";
      const duration = `${(Number(event.duration_ms || 0) / 1000).toFixed(1)}s`;
      const question = event.question_position ? `Question ${event.question_position}` : "Assessment";
      const label = (event.message || event.event_type.replaceAll("_", " ")).replace(/\.$/, "");
      card.innerHTML = `<div><strong></strong><small></small></div><span class="event-status"></span>`;
      card.querySelector("strong").textContent = label;
      card.querySelector("small").textContent = `${question} · ${duration} · ${event.created_at ? new Date(event.created_at).toLocaleString() : "Time unavailable"}`;
      const status = card.querySelector(".event-status");
      status.textContent = event.corrected ? "Corrected" : "Needs review";
      if (event.corrected) status.classList.add("corrected");
      monitoringList.appendChild(card);
    });

    const recordingPanel = $("#review-recordings");
    const recordingList = $("#review-recording-list");
    const recordings = result.recordings || [];
    recordingList.replaceChildren();
    recordingPanel.classList.toggle("hidden", recordings.length === 0);
    recordings.forEach((recording) => {
      const card = document.createElement("article");
      card.className = "monitoring-event";
      card.innerHTML = '<div><strong></strong><small></small></div><a class="secondary-button">Download chunk</a>';
      card.querySelector("strong").textContent = `${recording.recording_type.replaceAll("_", " ")} · chunk ${recording.sequence + 1}`;
      card.querySelector("small").textContent = `${(recording.byte_count / 1024 / 1024).toFixed(2)} MB · ${recording.created_at ? new Date(recording.created_at).toLocaleString() : ""}`;
      const link = card.querySelector("a");
      link.href = recording.download_url;
      link.download = "";
      recordingList.appendChild(card);
    });

    const questions = $("#review-questions");
    questions.replaceChildren();
    result.questions.forEach((item) => {
      const card = document.createElement("article");
      card.className = "review-question";
      const outcome = item.timed_out ? "Timed out" : item.is_correct ? "Correct" : item.is_correct === false ? "Incorrect" : "Not answered";
      card.innerHTML = `
        <h3></h3><div class="review-meta"><span class="${item.is_correct ? "correct" : "incorrect"}">${outcome}</span><span></span><span>${item.response_ms == null ? "No time" : `${(item.response_ms / 1000).toFixed(1)}s`}</span><span></span></div>
        <ol class="review-options"></ol><div class="source-evidence"><strong>Supporting passage</strong><br><span></span></div>`;
      card.querySelector("h3").textContent = `${item.position}. ${item.stem}`;
      card.querySelectorAll(".review-meta span")[1].textContent = `${item.category.replaceAll("_", " ")} · ${item.difficulty}${item.adaptive_reason ? ` · ${item.adaptive_reason.replaceAll("_", " ")}` : ""}`;
      card.querySelector(".review-meta span:last-child").textContent = item.source_location;
      const optionList = card.querySelector(".review-options");
      item.options.forEach((option, optionIndex) => {
        const line = document.createElement("li");
        line.textContent = option;
        if (optionIndex === item.correct_index) line.classList.add("correct-option");
        if (optionIndex === item.selected_index) line.classList.add("selected-option");
        optionList.appendChild(line);
      });
      card.querySelector(".source-evidence span").textContent = item.source_quote;
      questions.appendChild(card);
    });
    $("#review-panel").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    handleLecturerError(error);
  }
}

function renderLearningAlerts(alerts) {
  const panel = $("#review-alerts");
  const list = $("#review-alert-list");
  list.replaceChildren();
  panel.classList.toggle("hidden", alerts.length === 0);
  alerts.forEach((alert) => {
    const card = document.createElement("article");
    card.className = `learning-alert-card ${alert.severity}`;
    card.innerHTML = `<div><strong></strong><p></p><small></small></div><div class="action-row"></div>`;
    card.querySelector("strong").textContent = alert.alert_type.replaceAll("_", " ");
    card.querySelector("p").textContent = alert.message;
    card.querySelector("small").textContent = `${alert.status.replaceAll("_", " ")} · ${alert.created_at ? new Date(alert.created_at).toLocaleString() : ""}`;
    const actions = card.querySelector(".action-row");
    if (alert.status !== "resolved") {
      const acknowledge = document.createElement("button");
      acknowledge.type = "button";
      acknowledge.textContent = alert.status === "acknowledged" ? "Acknowledged" : "Acknowledge";
      acknowledge.disabled = alert.status === "acknowledged";
      acknowledge.addEventListener("click", () => updateLearningAlert(alert.id, "acknowledged"));
      actions.appendChild(acknowledge);
      const resolve = document.createElement("button");
      resolve.type = "button";
      resolve.textContent = "Resolve";
      resolve.addEventListener("click", () => updateLearningAlert(alert.id, "resolved"));
      actions.appendChild(resolve);
    }
    list.appendChild(card);
  });
}

async function updateLearningAlert(alertId, status) {
  const note = prompt(status === "resolved" ? "How was this concern addressed?" : "Optional lecturer note:", "");
  if (note === null) return;
  try {
    await api(`/api/lecturer/learning-alerts/${alertId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note: note.trim() || null }),
    });
    await Promise.all([reviewAssessment(state.currentReviewAssessmentId), loadSubmissions(), loadLearningProgress()]);
  } catch (error) {
    handleLecturerError(error);
  }
}

function renderRubric(rubric, mark, auditRows) {
  const container = $("#rubric-criteria");
  container.replaceChildren();
  const existing = new Map((mark?.criteria || []).map((item) => [item.key, item]));
  rubric.forEach((criterion) => {
    const saved = existing.get(criterion.key);
    const row = document.createElement("div");
    row.className = "rubric-row";
    row.innerHTML = `<div><strong></strong><small></small></div><label>Score<input type="number" step="0.5" min="0"><span></span></label><label>Comment<textarea rows="2" maxlength="1000"></textarea></label>`;
    row.dataset.criterionKey = criterion.key;
    row.querySelector("strong").textContent = criterion.label;
    row.querySelector("small").textContent = criterion.description || "";
    const score = row.querySelector('input[type="number"]');
    score.max = String(criterion.max_score);
    score.value = saved?.score ?? "";
    row.querySelector("label span").textContent = `/ ${criterion.max_score}`;
    row.querySelector("textarea").value = saved?.comment || "";
    score.addEventListener("input", updateRubricTotal);
    container.appendChild(row);
  });
  $("#rubric-mark-form").elements.overall_comment.value = mark?.overall_comment || "";
  $("#rubric-change-reason-field").classList.toggle("hidden", !mark);
  $("#rubric-mark-form").elements.change_reason.required = Boolean(mark);
  updateRubricTotal();
  const audit = $("#mark-audit-list");
  audit.replaceChildren();
  if (auditRows.length) {
    const heading = document.createElement("h4");
    heading.textContent = "Mark-change audit trail";
    audit.appendChild(heading);
    auditRows.forEach((item) => {
      const line = document.createElement("p");
      line.textContent = `${item.created_at ? new Date(item.created_at).toLocaleString() : ""}: ${item.previous_total == null ? "Initial" : item.previous_total} → ${item.new_total}. ${item.reason || ""}`;
      audit.appendChild(line);
    });
  }
}

function updateRubricTotal() {
  const total = $$("#rubric-criteria .rubric-row input").reduce((sum, input) => sum + Number(input.value || 0), 0);
  $("#rubric-total").textContent = `Total: ${total.toFixed(1)}/100`;
}


$("#rubric-mark-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const assessmentId = state.currentReviewAssessmentId;
  if (!assessmentId) return;
  const rows = $$("#rubric-criteria .rubric-row");
  const scores = rows.map((row) => ({
    criterion_key: row.dataset.criterionKey,
    score: Number(row.querySelector('input[type="number"]').value),
    comment: row.querySelector("textarea").value.trim() || null,
  }));
  if (scores.some((item) => !Number.isFinite(item.score))) {
    setMessage($("#lecturer-message"), "Enter a score for every rubric criterion.", "error");
    return;
  }
  const button = event.submitter;
  button.disabled = true;
  try {
    await api(`/api/lecturer/assessments/${assessmentId}/rubric-mark`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scores,
        overall_comment: event.currentTarget.elements.overall_comment.value.trim() || null,
        change_reason: event.currentTarget.elements.change_reason.value.trim() || null,
      }),
    });
    setMessage($("#lecturer-message"), "Criterion-level rubric mark saved and added to the audit trail.", "success");
    await reviewAssessment(assessmentId);
  } catch (error) {
    handleLecturerError(error);
  } finally {
    button.disabled = false;
  }
});

$("#assurance-decision-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const assessmentId = state.currentReviewAssessmentId;
  if (!assessmentId) return;
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api(`/api/lecturer/assessments/${assessmentId}/assurance-decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision: event.currentTarget.elements.decision.value,
        note: event.currentTarget.elements.note.value.trim(),
      }),
    });
    renderEvidenceProfile(result.evidence_profile);
    setMessage($("#lecturer-message"), "Verification decision recorded in the assessment evidence profile.", "success");
  } catch (error) {
    handleLecturerError(error);
  } finally {
    button.disabled = false;
  }
});


function localDateTimeValue(date = new Date()) {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function renderPhase2Modules(result) {
  const settings = result.phase2_settings || {};
  const anyEnabled = [
    settings.oral_defence_enabled,
    settings.practical_tasks_enabled,
    settings.supervisor_meetings_enabled,
    settings.cross_version_analysis_enabled,
    settings.random_moderation_enabled,
  ].some(Boolean);
  $("#review-phase2").classList.toggle("hidden", !anyEnabled);

  renderOralDefenceModule(settings, result.oral_defence);
  renderPracticalTasksModule(settings, result.practical_tasks || []);
  renderSupervisorMeetingModule(settings, result.supervisor_meetings || []);
  renderCrossVersionModule(settings, result.cross_version_analysis, result.version_history || []);
  renderModerationModule(settings, result.moderation);
}

function renderOralDefenceModule(settings, oralDefence) {
  const panel = $("#oral-defence-panel");
  panel.classList.toggle("hidden", !settings.oral_defence_enabled);
  if (!settings.oral_defence_enabled) return;
  const prepare = $("#prepare-oral-defence");
  const form = $("#oral-defence-form");
  const list = $("#oral-defence-questions");
  const output = $("#oral-defence-result");
  list.replaceChildren();
  output.replaceChildren();
  prepare.classList.toggle("hidden", Boolean(oralDefence));
  form.classList.toggle("hidden", !oralDefence);
  if (!oralDefence) {
    output.textContent = `Not yet prepared. The course setting allows ${settings.oral_defence_question_count || 5} defence questions.`;
    return;
  }
  const saved = new Map((oralDefence.scores || []).map((item) => [item.question_id, item]));
  (oralDefence.questions || []).forEach((question, index) => {
    const savedScore = saved.get(question.id);
    const card = document.createElement("article");
    card.className = "oral-question phase2-card";
    card.dataset.questionId = question.id;
    card.innerHTML = `<div><strong></strong><small></small></div><label>Score<input type="number" min="0" step="0.5"><span></span></label><label>Comment<textarea rows="2" maxlength="1000"></textarea></label>`;
    card.querySelector("strong").textContent = `${index + 1}. ${question.prompt}`;
    card.querySelector("small").textContent = question.focus.replaceAll("_", " ");
    const scoreInput = card.querySelector('input[type="number"]');
    scoreInput.max = String(question.max_score || 5);
    scoreInput.value = savedScore?.score ?? "";
    card.querySelector("label span").textContent = `/ ${question.max_score || 5}`;
    card.querySelector("textarea").value = savedScore?.comment || "";
    list.appendChild(card);
  });
  form.elements.outcome.value = oralDefence.outcome || "satisfactory";
  form.elements.notes.value = oralDefence.notes || "";
  if (oralDefence.status === "completed") {
    output.textContent = `Recorded score: ${Number(oralDefence.total_score || 0).toFixed(1)}%. Outcome: ${(oralDefence.outcome || "").replaceAll("_", " ")}.`;
  }
}

function renderPracticalTasksModule(settings, tasks) {
  const panel = $("#practical-task-panel");
  panel.classList.toggle("hidden", !settings.practical_tasks_enabled);
  if (!settings.practical_tasks_enabled) return;
  const prepare = $("#prepare-practical-tasks");
  const list = $("#practical-task-list");
  list.replaceChildren();
  prepare.classList.toggle("hidden", tasks.length > 0);
  if (!tasks.length) {
    list.textContent = `No task prepared. The course setting allows ${settings.practical_task_count || 1} practical task(s).`;
    return;
  }
  tasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "phase2-card practical-task-card";
    card.innerHTML = `<div><strong></strong><small></small><p></p></div><form class="stack-form compact-form"><label>Student response or performance summary<textarea name="student_response" rows="3" minlength="20" maxlength="6000" required></textarea></label><label>Score<input name="score" type="number" min="0" step="0.5" required><span></span></label><label>Lecturer notes<textarea name="notes" rows="2" maxlength="2000"></textarea></label><button class="secondary-button" type="submit">Save practical-task score</button></form>`;
    card.querySelector("strong").textContent = `Task ${task.sequence}: ${task.task_type.replaceAll("_", " ")}`;
    card.querySelector("small").textContent = task.status.replaceAll("_", " ");
    card.querySelector("p").textContent = task.prompt;
    const form = card.querySelector("form");
    form.elements.student_response.value = task.student_response || "";
    form.elements.score.max = String(task.max_score || 20);
    form.elements.score.value = task.score ?? "";
    form.querySelector("label span").textContent = `/ ${task.max_score || 20}`;
    form.elements.notes.value = task.notes || "";
    form.addEventListener("submit", (event) => savePracticalTask(event, task.id));
    list.appendChild(card);
  });
}

function renderSupervisorMeetingModule(settings, meetings) {
  const panel = $("#supervisor-meeting-panel");
  panel.classList.toggle("hidden", !settings.supervisor_meetings_enabled);
  if (!settings.supervisor_meetings_enabled) return;
  const form = $("#supervisor-meeting-form");
  if (!form.elements.meeting_at.value) form.elements.meeting_at.value = localDateTimeValue();
  const list = $("#supervisor-meeting-list");
  list.replaceChildren();
  if (!meetings.length) {
    list.textContent = "No supervisor meeting has been recorded for this student and course.";
    return;
  }
  meetings.forEach((meeting) => {
    const card = document.createElement("article");
    card.className = "phase2-card";
    card.innerHTML = `<strong></strong><small></small><p></p><div class="meeting-details"></div>`;
    card.querySelector("strong").textContent = meeting.meeting_type.replaceAll("_", " ");
    card.querySelector("small").textContent = meeting.meeting_at ? new Date(meeting.meeting_at).toLocaleString() : "Date unavailable";
    card.querySelector("p").textContent = meeting.summary;
    const details = [];
    if (meeting.decisions) details.push(`Decisions: ${meeting.decisions}`);
    if (meeting.action_items) details.push(`Actions: ${meeting.action_items}`);
    if (meeting.next_meeting_at) details.push(`Next: ${new Date(meeting.next_meeting_at).toLocaleString()}`);
    card.querySelector(".meeting-details").textContent = details.join(" · ");
    list.appendChild(card);
  });
}

function renderCrossVersionModule(settings, analysis, versions) {
  const panel = $("#cross-version-panel");
  panel.classList.toggle("hidden", !settings.cross_version_analysis_enabled);
  if (!settings.cross_version_analysis_enabled) return;
  const button = $("#run-version-analysis");
  const output = $("#cross-version-result");
  output.replaceChildren();
  const hasPrevious = versions.length > 1;
  button.disabled = !hasPrevious;
  button.title = hasPrevious ? "Run or refresh analysis" : "A previous version is required";
  if (!analysis) {
    output.textContent = hasPrevious ? "No advanced comparison has been saved yet." : "A previous version is required before this module can run.";
    return;
  }
  const card = document.createElement("article");
  card.className = `phase2-card risk-${analysis.risk_level || "low"}`;
  const flags = (analysis.flags || []).map((item) => item.message).join(" ") || "No automated risk flags.";
  card.innerHTML = `<strong></strong><p></p><div class="phase2-metrics"></div><small></small>`;
  card.querySelector("strong").textContent = `${String(analysis.risk_level || "low").toUpperCase()} change risk`;
  card.querySelector("p").textContent = flags;
  const metrics = card.querySelector(".phase2-metrics");
  [
    [`${analysis.similarity_percent ?? 0}%`, "similarity"],
    [analysis.word_count_change >= 0 ? `+${analysis.word_count_change}` : analysis.word_count_change, "word change"],
    [`${analysis.inserted_or_replaced_percent ?? 0}%`, "inserted/replaced"],
    [(analysis.added_headings || []).length, "new headings"],
  ].forEach(([value, label]) => {
    const metric = document.createElement("span");
    metric.innerHTML = `<strong></strong><small></small>`;
    metric.querySelector("strong").textContent = value;
    metric.querySelector("small").textContent = label;
    metrics.appendChild(metric);
  });
  card.querySelector("small:last-child").textContent = `New concepts: ${(analysis.new_keywords || []).join(", ") || "None identified"}`;
  output.appendChild(card);
}

function renderModerationModule(settings, moderation) {
  const panel = $("#moderation-panel");
  panel.classList.toggle("hidden", !settings.random_moderation_enabled);
  if (!settings.random_moderation_enabled) return;
  const status = $("#moderation-status");
  const form = $("#moderation-form");
  if (!moderation) {
    status.textContent = `This assessment has not been selected by the ${settings.moderation_sample_percent || 10}% moderation sample.`;
    form.classList.add("hidden");
    return;
  }
  status.textContent = moderation.status === "completed"
    ? `Moderation completed. Original ${moderation.original_mark}; moderated ${moderation.moderated_mark}; variance ${Number(moderation.variance || 0).toFixed(1)}. Decision: ${(moderation.decision || "").replaceAll("_", " ")}.`
    : `Selected for moderation: ${moderation.selected_reason}. Original rubric mark: ${moderation.original_mark}.`;
  form.classList.toggle("hidden", moderation.status === "completed");
  if (moderation.status !== "completed") {
    form.elements.moderated_mark.value = moderation.original_mark;
    form.elements.notes.value = moderation.notes || "";
  }
}

async function loadMarkerAnomalies(course, refresh = false) {
  state.currentAnomalyCourseId = course.id;
  $("#marker-anomaly-panel").classList.remove("hidden");
  $("#marker-anomaly-title").textContent = `Marker integrity analytics · ${course.course_code}`;
  setMessage($("#marker-anomaly-message"), "Analysing marking patterns…");
  try {
    const result = await api(`/api/lecturer/courses/${course.id}/marker-anomalies?refresh=${refresh ? "true" : "false"}`);
    renderMarkerAnomalies(result.anomalies || []);
    setMessage($("#marker-anomaly-message"), result.anomalies?.length ? "Indicators found for review." : "No anomaly indicators were found with the available marking data.", result.anomalies?.length ? "warning" : "success");
    $("#marker-anomaly-panel").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    handleLecturerError(error);
  }
}

function renderMarkerAnomalies(alerts) {
  const body = $("#marker-anomaly-body");
  body.replaceChildren();
  if (!alerts.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-state">No anomaly indicators were identified. At least four marked assessments per marker are required for most checks.</td></tr>';
    return;
  }
  alerts.forEach((alert) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td></td><td><strong></strong><small></small></td><td></td><td></td><td><div class="action-row"></div></td>`;
    row.children[0].textContent = alert.marker_name || "Unknown marker";
    row.children[1].querySelector("strong").textContent = alert.anomaly_type.replaceAll("_", " ");
    row.children[1].querySelector("small").textContent = alert.severity;
    row.children[2].textContent = alert.details;
    row.children[3].textContent = alert.status;
    const actions = row.children[4].querySelector(".action-row");
    if (alert.status !== "resolved") {
      ["acknowledged", "resolved"].forEach((status) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = status === "resolved" ? "Resolve" : "Acknowledge";
        button.addEventListener("click", () => updateMarkerAnomaly(alert.id, status));
        actions.appendChild(button);
      });
    }
    body.appendChild(row);
  });
}

async function updateMarkerAnomaly(alertId, status) {
  const note = prompt(status === "resolved" ? "How was this indicator reviewed or resolved?" : "Optional review note:", "");
  if (note === null) return;
  try {
    await api(`/api/lecturer/marker-anomalies/${alertId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note: note.trim() || null }),
    });
    const course = state.courses.find((item) => item.id === state.currentAnomalyCourseId);
    if (course) await loadMarkerAnomalies(course, false);
  } catch (error) {
    handleLecturerError(error);
  }
}

$("#refresh-marker-anomalies")?.addEventListener("click", async () => {
  const course = state.courses.find((item) => item.id === state.currentAnomalyCourseId);
  if (course) await loadMarkerAnomalies(course, true);
});

$("#prepare-oral-defence")?.addEventListener("click", async () => {
  if (!state.currentReviewAssessmentId) return;
  try {
    await api(`/api/lecturer/assessments/${state.currentReviewAssessmentId}/oral-defence/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_count: state.currentReviewData?.phase2_settings?.oral_defence_question_count || null }),
    });
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

$("#oral-defence-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.currentReviewAssessmentId) return;
  const scores = $$("#oral-defence-questions .oral-question").map((card) => ({
    question_id: card.dataset.questionId,
    score: Number(card.querySelector('input[type="number"]').value),
    comment: card.querySelector("textarea").value.trim() || null,
  }));
  if (scores.some((item) => !Number.isFinite(item.score))) {
    setMessage($("#lecturer-message"), "Enter a score for every oral-defence question.", "error");
    return;
  }
  try {
    await api(`/api/lecturer/assessments/${state.currentReviewAssessmentId}/oral-defence/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scores, outcome: event.currentTarget.elements.outcome.value, notes: event.currentTarget.elements.notes.value.trim() || null }),
    });
    setMessage($("#lecturer-message"), "Oral-defence result saved.", "success");
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

$("#prepare-practical-tasks")?.addEventListener("click", async () => {
  if (!state.currentReviewAssessmentId) return;
  try {
    await api(`/api/lecturer/assessments/${state.currentReviewAssessmentId}/practical-tasks/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: state.currentReviewData?.phase2_settings?.practical_task_count || null }),
    });
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

async function savePracticalTask(event, taskId) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api(`/api/lecturer/practical-tasks/${taskId}/score`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student_response: form.elements.student_response.value.trim(),
        score: Number(form.elements.score.value),
        notes: form.elements.notes.value.trim() || null,
      }),
    });
    setMessage($("#lecturer-message"), "Practical-task evidence saved.", "success");
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
}

$("#supervisor-meeting-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const summary = state.currentReviewSummary;
  if (!summary?.course_id) return;
  const form = event.currentTarget;
  try {
    await api(`/api/lecturer/courses/${summary.course_id}/supervisor-meetings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        student_id: summary.student_id,
        document_id: summary.document_id,
        meeting_at: new Date(form.elements.meeting_at.value).toISOString(),
        meeting_type: form.elements.meeting_type.value,
        summary: form.elements.summary.value.trim(),
        decisions: form.elements.decisions.value.trim() || null,
        action_items: form.elements.action_items.value.trim() || null,
        next_meeting_at: form.elements.next_meeting_at.value ? new Date(form.elements.next_meeting_at.value).toISOString() : null,
      }),
    });
    form.reset();
    form.elements.meeting_at.value = localDateTimeValue();
    setMessage($("#lecturer-message"), "Supervisor meeting recorded.", "success");
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

$("#run-version-analysis")?.addEventListener("click", async () => {
  const documentId = state.currentReviewSummary?.document_id;
  if (!documentId) return;
  try {
    await api(`/api/lecturer/documents/${documentId}/version-analysis`, { method: "POST" });
    setMessage($("#lecturer-message"), "Cross-version analysis updated.", "success");
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

$("#moderation-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.currentReviewAssessmentId) return;
  const form = event.currentTarget;
  try {
    await api(`/api/lecturer/assessments/${state.currentReviewAssessmentId}/moderation/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        moderated_mark: Number(form.elements.moderated_mark.value),
        decision: form.elements.decision.value,
        notes: form.elements.notes.value.trim(),
      }),
    });
    setMessage($("#lecturer-message"), "Moderation record completed.", "success");
    await reviewAssessment(state.currentReviewAssessmentId);
  } catch (error) {
    handleLecturerError(error);
  }
});

$("#close-review").addEventListener("click", () => $("#review-panel").classList.add("hidden"));

async function lecturerInterruptionAction(action) {
  const assessmentId = state.currentReviewAssessmentId;
  if (!assessmentId) return;
  const prompts = {
    "allow-resume": "Optional note for allowing the student to resume:",
    "finish-interrupted": "Optional note before ending and scoring all unanswered questions as timed out:",
    "excuse-interruption": "Add a short reason for excusing the interruption:",
  };
  const note = prompt(prompts[action], "");
  if (note === null) return;
  if (action === "finish-interrupted" && !confirm("End this assessment and score every remaining unanswered question as timed out?")) return;
  try {
    await api(`/api/lecturer/assessments/${assessmentId}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note.trim() || null }),
    });
    setMessage($("#lecturer-message"), action === "allow-resume"
      ? "The student may resume after camera reverification."
      : action === "finish-interrupted"
        ? "The interrupted assessment was ended and scored."
        : "The interruption was marked as excused.", "success");
    await Promise.all([reviewAssessment(assessmentId), loadSubmissions()]);
  } catch (error) {
    handleLecturerError(error);
  }
}

$("#allow-resume-button").addEventListener("click", () => lecturerInterruptionAction("allow-resume"));
$("#finish-interrupted-button").addEventListener("click", () => lecturerInterruptionAction("finish-interrupted"));
$("#excuse-interruption-button").addEventListener("click", () => lecturerInterruptionAction("excuse-interruption"));

async function downloadReport(assessmentId, studentId) {
  try {
    const response = await fetch(`/api/lecturer/assessments/${assessmentId}/report.pdf`, { credentials: "same-origin" });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "The report could not be downloaded.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `kanokware-${studentId.replaceAll("/", "-")}.pdf`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    handleLecturerError(error);
  }
}

async function resetAttempt(assessmentId, studentName) {
  if (!confirm(`Reset ${studentName}'s attempt? Current answers and the still image will be deleted.`)) return;
  try {
    await api(`/api/lecturer/assessments/${assessmentId}`, { method: "DELETE" });
    await loadSubmissions();
  } catch (error) {
    handleLecturerError(error);
  }
}

async function deleteSubmission(documentId, studentName) {
  if (!confirm(`Delete ${studentName}'s submission and all related evidence? This cannot be undone.`)) return;
  try {
    await api(`/api/lecturer/documents/${documentId}`, { method: "DELETE" });
    await Promise.all([loadCourses(), loadSubmissions()]);
  } catch (error) {
    handleLecturerError(error);
  }
}

const platformKeyInput = $("#platform-admin-key");
platformKeyInput.value = state.platformKey;

function platformHeaders(extra = {}) {
  return { "X-Admin-Key": state.platformKey, ...extra };
}

function showPlatformLogin() {
  state.platformAuthenticated = false;
  $("#platform-login-panel").classList.remove("hidden");
  $("#platform-dashboard").classList.add("hidden");
}

function showPlatformDashboard() {
  state.platformAuthenticated = true;
  $("#platform-login-panel").classList.add("hidden");
  $("#platform-dashboard").classList.remove("hidden");
}

async function unlockPlatform(silent = false) {
  if (!state.platformKey) {
    showPlatformLogin();
    if (!silent) setMessage($("#platform-login-message"), "Enter the platform ADMIN_KEY.", "warning");
    return;
  }
  try {
    await api("/api/platform/verify", { headers: platformHeaders() });
    sessionStorage.setItem("kanokware_platform_key", state.platformKey);
    showPlatformDashboard();
    setMessage($("#platform-login-message"));
    await Promise.all([loadPlatformUsers(), loadPlatformFeatures(), loadPlatformTasks()]);
  } catch (error) {
    showPlatformLogin();
    if (!silent) setMessage($("#platform-login-message"), error.message, "error");
  }
}

$("#platform-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  state.platformKey = platformKeyInput.value.trim();
  await unlockPlatform(false);
  button.disabled = false;
});

$("#platform-logout").addEventListener("click", () => {
  state.platformKey = "";
  platformKeyInput.value = "";
  sessionStorage.removeItem("kanokware_platform_key");
  showPlatformLogin();
  setMessage($("#platform-login-message"), "Admin dashboard locked.", "success");
});

$("#platform-create-user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const data = formJson(event.currentTarget);
  button.disabled = true;
  setMessage($("#platform-message"), "Creating lecturer account…");
  try {
    const result = await api("/api/platform/users", {
      method: "POST",
      headers: platformHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(data),
    });
    event.target?.reset?.();
    showSetupCredentials(result.user, result.setup_code, result.setup_code_expires_at);
    setMessage($("#platform-message"), result.message, "success");
    await loadPlatformUsers();
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  } finally {
    button.disabled = false;
  }
});

function showSetupCredentials(user, setupCode, expiresAt) {
  $("#setup-credential-card").classList.remove("hidden");
  $("#setup-credential-user").textContent = `${user.full_name} · ${user.institution?.name || "Institution"}`;
  $("#login-email-value").textContent = user.email;
  $("#setup-code-value").textContent = setupCode;
  $("#setup-code-expiry").textContent = expiresAt
    ? `Code expires ${new Date(expiresAt).toLocaleString()}.`
    : "Give this code directly to the lecturer.";
  $("#setup-credential-card").scrollIntoView({ behavior: "smooth", block: "center" });
}

$("#copy-setup-details").addEventListener("click", async () => {
  const email = $("#login-email-value").textContent;
  const setupCode = $("#setup-code-value").textContent;
  if (!email || !setupCode) return;
  await navigator.clipboard.writeText(`Kanokware login email: ${email}\nOne-time setup code: ${setupCode}`);
  setMessage($("#platform-message"), "Setup details copied.", "success");
});

$("#refresh-platform-users").addEventListener("click", loadPlatformUsers);
$("#refresh-platform-features")?.addEventListener("click", loadPlatformFeatures);
$("#refresh-platform-tasks")?.addEventListener("click", loadPlatformTasks);

async function loadPlatformTasks() {
  try {
    const result = await api("/api/platform/assessment-tasks", { headers: platformHeaders() });
    renderPlatformTasks(result.tasks || []);
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  }
}

function renderPlatformTasks(tasks) {
  const body = $("#platform-tasks-body");
  if (!body) return;
  body.replaceChildren();
  if (!tasks.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-state">No assessment tasks have been created.</td></tr>';
    return;
  }
  tasks.forEach((task) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.innerHTML = "<strong></strong><small></small>";
    name.querySelector("strong").textContent = task.title;
    name.querySelector("small").textContent = task.course_id;
    const mode = document.createElement("td");
    mode.textContent = task.mode.replaceAll("_", " ");
    const effective = document.createElement("td");
    effective.textContent = task.features.filter((feature) => feature.effective).map((feature) => feature.label).join(", ") || "Basic workflow only";
    const controls = document.createElement("td");
    const group = document.createElement("div");
    group.className = "action-row";
    task.features.filter((feature) => feature.activation_mode === "admin_only").forEach((feature) => {
      const button = document.createElement("button");
      button.textContent = `${feature.effective ? "Disable" : "Enable"} ${feature.label}`;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`/api/platform/assessment-tasks/${task.id}/features`, {
            method: "PATCH",
            headers: platformHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ features: { [feature.key]: !feature.effective } }),
          });
          await loadPlatformTasks();
        } catch (error) {
          setMessage($("#platform-message"), error.message, "error");
          button.disabled = false;
        }
      });
      group.appendChild(button);
    });
    if (!group.childElementCount) group.textContent = "No administrator-only controls";
    controls.appendChild(group);
    row.append(name, mode, effective, controls);
    body.appendChild(row);
  });
}

async function loadPlatformFeatures() {
  try {
    const result = await api("/api/platform/features", { headers: platformHeaders() });
    renderPlatformFeatures(result.optional_features || []);
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  }
}

function renderPlatformFeatures(features) {
  const body = $("#platform-features-body");
  if (!body) return;
  body.replaceChildren();
  features.forEach((feature) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.innerHTML = "<strong></strong><small></small>";
    name.querySelector("strong").textContent = feature.label;
    name.querySelector("small").textContent = feature.group.replaceAll("_", " ");
    const purpose = document.createElement("td");
    purpose.textContent = feature.description;
    const control = document.createElement("td");
    const select = document.createElement("select");
    [
      ["disabled", "Disabled"],
      ["admin_only", "Administrator only"],
      ["lecturer_allowed", "Lecturer may activate"],
      ["always_on", "Always active"],
    ].forEach(([value, label]) => select.appendChild(new Option(label, value)));
    select.value = feature.activation_mode;
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await api(`/api/platform/features/${feature.key}`, {
          method: "PATCH",
          headers: platformHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ activation_mode: select.value }),
        });
        setMessage($("#platform-message"), `${feature.label} policy updated.`, "success");
      } catch (error) {
        select.value = feature.activation_mode;
        setMessage($("#platform-message"), error.message, "error");
      } finally {
        select.disabled = false;
      }
    });
    control.appendChild(select);
    row.append(name, purpose, control);
    body.appendChild(row);
  });
}

async function loadPlatformUsers() {
  try {
    const result = await api("/api/platform/users", { headers: platformHeaders() });
    renderPlatformUsers(result.users);
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
    if (error.status === 401) showPlatformLogin();
  }
}

function renderPlatformUsers(users) {
  const body = $("#platform-users-body");
  body.replaceChildren();
  if (!users.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-state">No lecturer accounts have been created.</td></tr>';
    return;
  }
  users.forEach((user) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td><strong></strong><small></small></td>
      <td><span class="status-pill"></span><small></small></td>
      <td><strong></strong><small></small></td>
      <td><div class="action-row"></div></td>`;
    row.children[0].querySelector("strong").textContent = user.full_name;
    row.children[0].querySelector("small").textContent = user.email;
    row.children[1].querySelector("strong").textContent = user.institution?.name || "—";
    row.children[1].querySelector("small").textContent = user.department || "—";
    const status = row.children[2].querySelector("span");
    status.textContent = user.account_status;
    status.classList.add(user.account_status);
    row.children[2].querySelector("small").textContent = user.role.replaceAll("_", " ");
    row.children[3].querySelector("strong").textContent = `${user.course_count} course(s) · ${user.submission_count} submission(s)`;
    row.children[3].querySelector("small").textContent = user.last_login_at ? `Last login ${new Date(user.last_login_at).toLocaleString()}` : "Never signed in";
    const actions = row.children[4].querySelector(".action-row");
    const setup = document.createElement("button");
    setup.textContent = user.account_status === "pending_activation" ? "Reissue setup code" : "Issue new setup code";
    setup.addEventListener("click", () => issuePlatformSetupCode(user));
    actions.appendChild(setup);
    if (user.account_status === "active" || user.account_status === "suspended") {
      const statusButton = document.createElement("button");
      statusButton.textContent = user.account_status === "active" ? "Suspend" : "Reactivate";
      statusButton.addEventListener("click", () => setPlatformUserStatus(user, user.account_status === "active" ? "suspended" : "active"));
      actions.appendChild(statusButton);
    }
    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.className = "delete";
    remove.addEventListener("click", () => deletePlatformUser(user));
    actions.appendChild(remove);
    body.appendChild(row);
  });
}

async function issuePlatformSetupCode(user) {
  if (!confirm(`Issue a new setup code for ${user.full_name}? Their current sessions will end and they will create a new password and recovery PIN.`)) return;
  try {
    const result = await api(`/api/platform/users/${user.id}/reset-password`, {
      method: "POST",
      headers: platformHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
    });
    showSetupCredentials(result.user || user, result.setup_code, result.setup_code_expires_at);
    setMessage($("#platform-message"), result.message, "success");
    await loadPlatformUsers();
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  }
}

async function setPlatformUserStatus(user, status) {
  const action = status === "active" ? "reactivate" : "suspend";
  if (!confirm(`${action[0].toUpperCase() + action.slice(1)} ${user.full_name}'s account?`)) return;
  try {
    await api(`/api/platform/users/${user.id}/status`, {
      method: "POST",
      headers: platformHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ status }),
    });
    setMessage($("#platform-message"), `Account ${status}.`, "success");
    await loadPlatformUsers();
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  }
}

async function deletePlatformUser(user) {
  if (!confirm(`Permanently delete ${user.full_name}'s account? Course ownership will be transferred to another assigned lecturer. This cannot be undone.`)) return;
  try {
    await api(`/api/platform/users/${user.id}`, { method: "DELETE", headers: platformHeaders() });
    setMessage($("#platform-message"), "Lecturer account deleted.", "success");
    await loadPlatformUsers();
  } catch (error) {
    setMessage($("#platform-message"), error.message, "error");
  }
}


const trialCodeButton = $("#use-trial-code-button");
if (trialCodeButton) {
  trialCodeButton.addEventListener("click", () => {
    const courseCodeInput = document.querySelector('input[name="course_code"]');
    if (!courseCodeInput) return;
    courseCodeInput.value = "KANO-3LH9PW";
    courseCodeInput.focus();
    setMessage(
      $("#upload-message"),
      "Practice code KANO-3LH9PW has been added. Complete the remaining details and upload a document.",
      "success"
    );
  });
}

$$('[data-view-link="lecturer"]').forEach((button) => button.addEventListener("click", () => loadLecturerSession(true)));
$$('[data-view-link="platform"]').forEach((button) => button.addEventListener("click", () => unlockPlatform(true)));

if (state.documentId && !state.assessmentId) {
  showPanel("prepare", 2);
  pollDocumentStatus();
}
if (state.assessmentId && state.token) {
  state.testActive = true;
  saveAssessmentSession();
  showPanel("test", 3);
  setConnectionOverlay("reconnecting");
  recoverAssessment("page_load");
}

loadLecturerSession(true);
if (state.platformKey) unlockPlatform(true);
window.addEventListener("pagehide", () => {
  if (state.secureBrowserEnforcement) terminateForSecureBrowser("page_hidden");
  else reportExplicitInterruption("pagehide");
  clearHeartbeatTimer();
  stopCamera();
  stopEvidenceRecordings();
});
