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
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const MEDIAPIPE_TASKS_BASE = "/static/vendor/mediapipe-tasks-vision";
const FACE_DETECTION_MODEL_URL = `${MEDIAPIPE_TASKS_BASE}/models/face_detection_short_range.tflite`;
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

async function loadTasksVisionModule() {
  if (!state.tasksVisionModulePromise) {
    state.tasksVisionModulePromise = import(
      `${MEDIAPIPE_TASKS_BASE}/vision_bundle.mjs`
    ).catch((error) => {
      state.tasksVisionModulePromise = null;
      throw error;
    });
  }
  return state.tasksVisionModulePromise;
}

async function createTasksFaceDetector() {
  const { FilesetResolver, FaceDetector } = await loadTasksVisionModule();
  const vision = await FilesetResolver.forVisionTasks(
    `${MEDIAPIPE_TASKS_BASE}/wasm`
  );
  const modelResponse = await fetch(FACE_DETECTION_MODEL_URL, {
    credentials: "same-origin",
    cache: "force-cache",
  });
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

    const detail = tasksError?.message ? ` ${tasksError.message}` : "";
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
    const formData = new FormData(event.currentTarget);
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
    button.textContent = "Generate ownership assessment";
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
      generating: "Generating and validating 20 document-specific questions.",
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

$("#start-button").addEventListener("click", async () => {
  const button = $("#start-button");
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    button.textContent = "Starting camera…";
    await startCamera();
    button.textContent = "Checking monitoring…";
    await runMonitoringPreflight();
    button.textContent = "Starting assessment…";
    const result = await api("/api/assessments/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: state.documentId,
        client_instance_id: state.clientInstanceId,
      }),
    });
    state.assessmentId = result.assessment_id;
    state.token = result.session_token;
    state.snapshotCaptured = false;
    state.assessmentQuestionCount = Number(result.question_count || state.assessmentQuestionCount || 20);
    state.testActive = true;
    saveAssessmentSession();
    state.heartbeatIntervalMs = Math.max(3000, Number(result.heartbeat_interval_seconds || 5) * 1000);
    state.connectionStatus = "online";
    showPanel("test", 3);
    setConnectionOverlay("online");
    document.documentElement.requestFullscreen?.().catch(() => {});
    await startSuspiciousMonitoring();
    await sendHeartbeat({ cameraVerified: true, reason: "start" });
    startHeartbeatLoop();
    await loadQuestion();
  } catch (error) {
    stopCamera();
    setMessage($("#mode-warning"), `Camera monitoring could not start: ${error.message}`, "error");
    button.disabled = false;
  } finally {
    button.textContent = "Start assessment";
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
  state.testActive = false;
  try {
    if (!state.snapshotCaptured) await captureSnapshotOnce("completion_fallback");
    stopCamera();
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
  fetch(`/api/assessments/${state.assessmentId}/focus-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify({ event: "hidden" }),
    keepalive: true,
  }).catch(() => {});
});
window.addEventListener("blur", () => {
  if (!state.testActive) return;
  showMonitoringWarning({ title: "Assessment window lost focus", message: "Return to the assessment window.", severity: "warning" });
  postMonitoringEvent("window_blur", 0, false, { severity: "warning", message: "The assessment browser window lost focus." });
  fetch(`/api/assessments/${state.assessmentId}/focus-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify({ event: "blur" }),
    keepalive: true,
  }).catch(() => {});
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
  clearSnapshotObjectUrls();
  showLecturerAuth();
  setMessage($("#lecturer-auth-message"), "You have signed out.", "success");
});

$("#course-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setMessage($("#lecturer-message"), "Creating course…");
  try {
    const result = await api("/api/lecturer/courses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formJson(event.currentTarget)),
    });
    event.target?.reset?.();
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
  const selectedFilter = filter.value;
  collaborator.replaceChildren(new Option("Select course", ""));
  filter.replaceChildren(new Option("All my courses", ""));
  courses.forEach((course) => {
    const label = `${course.course_code} · ${course.title}`;
    if (["owner", "institution_admin"].includes(course.my_access_level)) {
      collaborator.appendChild(new Option(label, course.id));
    }
    filter.appendChild(new Option(label, course.id));
  });
  if (courses.some((course) => course.id === selectedFilter)) filter.value = selectedFilter;
}

function renderCourses(courses) {
  const body = $("#courses-body");
  body.replaceChildren();
  if (!courses.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-state">Create your first course to receive student submissions.</td></tr>';
    return;
  }
  courses.forEach((course) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong></strong><small></small></td>
      <td><strong></strong><small></small></td>
      <td><code class="enrollment-code"></code></td>
      <td><strong class="question-count"></strong><small>per attempt</small></td>
      <td class="course-lecturers"></td>
      <td></td>
      <td><div class="action-row"></div></td>`;
    row.children[0].querySelector("strong").textContent = course.course_code;
    row.children[0].querySelector("small").textContent = course.title;
    row.children[1].querySelector("strong").textContent = course.academic_year;
    row.children[1].querySelector("small").textContent = course.semester;
    row.children[2].querySelector("code").textContent = course.enrollment_code;
    row.children[3].querySelector("strong").textContent = String(course.assessment_question_count || 20);
    row.children[4].textContent = course.lecturers.map((item) => `${item.full_name} (${item.access_level.replaceAll("_", " ")})`).join(", ");
    row.children[5].textContent = String(course.submission_count);
    const actions = row.children[6].querySelector(".action-row");
    const copy = document.createElement("button");
    copy.textContent = "Copy code";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(course.enrollment_code);
      setMessage($("#lecturer-message"), `${course.enrollment_code} copied.`, "success");
    });
    actions.appendChild(copy);
    if (["owner", "institution_admin"].includes(course.my_access_level)) {
      const questions = document.createElement("button");
      questions.textContent = "Set questions";
      questions.addEventListener("click", () => updateCourseQuestionCount(course));
      actions.appendChild(questions);
      const regenerate = document.createElement("button");
      regenerate.textContent = "New code";
      regenerate.addEventListener("click", () => regenerateCourseCode(course));
      actions.appendChild(regenerate);
    }
    body.appendChild(row);
  });
}

async function updateCourseQuestionCount(course) {
  const entered = prompt(
    `How many questions should each ${course.course_code} assessment contain? Enter 5 to 20.`,
    String(course.assessment_question_count || 20)
  );
  if (entered == null) return;
  const count = Number(entered);
  if (!Number.isInteger(count) || count < 5 || count > 20) {
    setMessage($("#lecturer-message"), "Enter a whole number from 5 to 20.", "error");
    return;
  }
  try {
    await api(`/api/lecturer/courses/${course.id}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assessment_question_count: count }),
    });
    setMessage($("#lecturer-message"), `${course.course_code} will now use ${count} questions per assessment.`, "success");
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
    row.children[1].querySelector("small").textContent = `${item.course} · ${item.word_count.toLocaleString()} words`;
    const status = row.children[2].querySelector("span");
    status.textContent = item.assessment_status || item.status;
    status.classList.add(item.assessment_status || item.status);
    row.children[4].textContent = item.decision || "—";
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

async function reviewAssessment(assessmentId) {
  try {
    const result = await api(`/api/lecturer/assessments/${assessmentId}`);
    state.currentReviewAssessmentId = assessmentId;
    state.currentReviewSummary = result.summary;
    $("#review-panel").classList.remove("hidden");
    $("#review-title").textContent = `${result.summary.student_name} · ${result.summary.document_title}`;
    const summary = $("#review-summary");
    summary.replaceChildren();
    const metrics = [
      [result.summary.score == null ? "In progress" : `${Number(result.summary.score).toFixed(0)}%`, "score"],
      [result.summary.correct_count ?? "—", "correct answers"],
      [result.summary.timed_out_count ?? "—", "timed out"],
      [result.summary.focus_loss_count ?? "—", "focus losses"],
      [result.summary.monitoring_event_count ?? 0, "webcam warnings"],
      [result.summary.interruption_count ?? 0, "interruptions"],
      [formatDuration(result.summary.total_offline_seconds || 0), "offline time"],
      [result.summary.resume_count ?? 0, "resumes"],
    ];
    metrics.forEach(([value, label]) => {
      const block = document.createElement("div");
      block.innerHTML = "<strong></strong><span></span>";
      block.querySelector("strong").textContent = value;
      block.querySelector("span").textContent = label;
      summary.appendChild(block);
    });
    const interruptionPanel = $("#review-interruption");
    const interruptionCount = Number(result.summary.interruption_count || 0);
    const interruptedStatus = ["interrupted", "locked"].includes(result.summary.status);
    interruptionPanel.classList.toggle("hidden", !interruptionCount && !interruptedStatus);
    if (!interruptionPanel.classList.contains("hidden")) {
      const title = result.summary.status === "locked"
        ? "Assessment locked for review"
        : result.summary.status === "interrupted"
          ? "Assessment currently interrupted"
          : "Assessment resumed after interruption";
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

    const questions = $("#review-questions");
    questions.replaceChildren();
    result.questions.forEach((item) => {
      const card = document.createElement("article");
      card.className = "review-question";
      const outcome = item.timed_out ? "Timed out" : item.is_correct ? "Correct" : item.is_correct === false ? "Incorrect" : "Not answered";
      card.innerHTML = `
        <h3></h3><div class="review-meta"><span class="${item.is_correct ? "correct" : "incorrect"}">${outcome}</span><span>${item.difficulty}</span><span>${item.response_ms == null ? "No time" : `${(item.response_ms / 1000).toFixed(1)}s`}</span><span></span></div>
        <ol class="review-options"></ol><div class="source-evidence"><strong>Supporting passage</strong><br><span></span></div>`;
      card.querySelector("h3").textContent = `${item.position}. ${item.stem}`;
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
    await loadPlatformUsers();
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
  reportExplicitInterruption("pagehide");
  clearHeartbeatTimer();
  stopCamera();
});
