const state = {
  documentId: localStorage.getItem("kanokwere_document_id"),
  assessmentId: sessionStorage.getItem("kanokwere_assessment_id"),
  token: sessionStorage.getItem("kanokwere_session_token"),
  testActive: false,
  timerHandle: null,
  currentPosition: null,
  adminKey: sessionStorage.getItem("kanokwere_admin_key") || "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

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
  const response = await fetch(path, options);
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

$$('[data-view-link]').forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    showView(button.dataset.viewLink);
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
    localStorage.setItem("kanokwere_document_id", state.documentId);
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
      localStorage.removeItem("kanokwere_document_id");
      sessionStorage.removeItem("kanokwere_assessment_id");
      sessionStorage.removeItem("kanokwere_session_token");
      state.documentId = null;
      state.assessmentId = null;
      state.token = null;
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
  localStorage.removeItem("kanokwere_document_id");
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
    const result = await api("/api/assessments/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_id: state.documentId }),
    });
    state.assessmentId = result.assessment_id;
    state.token = result.session_token;
    state.testActive = true;
    sessionStorage.setItem("kanokwere_assessment_id", state.assessmentId);
    sessionStorage.setItem("kanokwere_session_token", state.token);
    showPanel("test", 3);
    document.documentElement.requestFullscreen?.().catch(() => {});
    await loadQuestion();
  } catch (error) {
    setMessage($("#mode-warning"), error.message, "error");
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
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/question`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (result.status === "completed") {
      await loadResult();
      return;
    }
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
  } catch (error) {
    setMessage($("#test-message"), error.message, "error");
  }
}

async function submitAnswer(index, selectedButton) {
  clearQuestionTimer();
  disableOptions();
  selectedButton.classList.add("selected");
  setMessage($("#test-message"), "Answer submitted.", "success");
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/answer`, {
      method: "POST",
      headers: assessmentHeaders(),
      body: JSON.stringify({ selected_index: index }),
    });
    if (result.status === "completed") {
      await loadResult();
    } else {
      setTimeout(loadQuestion, 300);
    }
  } catch (error) {
    setMessage($("#test-message"), error.message, "error");
    setTimeout(loadQuestion, 500);
  }
}

async function loadResult() {
  clearQuestionTimer();
  state.testActive = false;
  try {
    const result = await api(`/api/assessments/${state.assessmentId}/result`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    showPanel("result", 4);
    const score = Number(result.score || 0);
    $("#score-value").textContent = `${score.toFixed(0)}%`;
    $("#score-ring").style.background = `conic-gradient(var(--brand) ${score}%, #e6ede9 ${score}%)`;
    $("#result-decision").textContent = result.decision;
    $("#correct-count").textContent = `${result.correct_count}/20`;
    $("#timeout-count").textContent = result.timed_out_count;
    $("#focus-count").textContent = result.focus_loss_count;
    $("#result-disclaimer").textContent = result.disclaimer;
    sessionStorage.removeItem("kanokwere_assessment_id");
    sessionStorage.removeItem("kanokwere_session_token");
  } catch (error) {
    setMessage($("#test-message"), error.message, "error");
  }
}

$("#new-assessment-button").addEventListener("click", () => {
  state.documentId = null;
  state.assessmentId = null;
  state.token = null;
  state.currentPosition = null;
  pollStartedAt = null;
  localStorage.removeItem("kanokwere_document_id");
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
  fetch(`/api/assessments/${state.assessmentId}/focus-event`, {
    method: "POST",
    headers: assessmentHeaders(),
    body: JSON.stringify({ event: "blur" }),
    keepalive: true,
  }).catch(() => {});
});

const adminKeyInput = $("#admin-key");
adminKeyInput.value = state.adminKey;
$("#load-submissions").addEventListener("click", loadSubmissions);
$("#refresh-submissions").addEventListener("click", loadSubmissions);

async function loadSubmissions() {
  state.adminKey = adminKeyInput.value.trim();
  if (!state.adminKey) {
    setMessage($("#admin-message"), "Enter the administrator key.", "warning");
    return;
  }
  sessionStorage.setItem("kanokwere_admin_key", state.adminKey);
  setMessage($("#admin-message"), "Loading submissions…");
  try {
    const result = await api("/api/admin/submissions", {
      headers: { "X-Admin-Key": state.adminKey },
    });
    renderSubmissions(result.submissions);
    setMessage($("#admin-message"), `${result.submissions.length} submission(s) loaded.`, "success");
  } catch (error) {
    setMessage($("#admin-message"), error.message, "error");
  }
}

function renderSubmissions(rows) {
  const body = $("#submissions-body");
  body.replaceChildren();
  if (!rows.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6" class="empty-state">No submissions found.</td>';
    body.appendChild(row);
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
      <td><div class="action-row"></div></td>`;
    row.children[0].querySelector("strong").textContent = item.student_name;
    row.children[0].querySelector("small").textContent = item.student_id;
    row.children[1].querySelector("strong").textContent = item.title;
    row.children[1].querySelector("small").textContent = `${item.word_count.toLocaleString()} words`;
    const status = row.children[2].querySelector("span");
    status.textContent = item.assessment_status || item.status;
    status.classList.add(item.assessment_status || item.status);
    row.children[4].textContent = item.decision || "—";
    const actions = row.children[5].querySelector(".action-row");
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

async function reviewAssessment(assessmentId) {
  try {
    const result = await api(`/api/admin/assessments/${assessmentId}`, {
      headers: { "X-Admin-Key": state.adminKey },
    });
    $("#review-panel").classList.remove("hidden");
    $("#review-title").textContent = `${result.summary.student_name} · ${result.summary.document_title}`;
    const summary = $("#review-summary");
    summary.replaceChildren();
    const metrics = [
      [result.summary.score == null ? "In progress" : `${Number(result.summary.score).toFixed(0)}%`, "score"],
      [result.summary.correct_count ?? "—", "correct answers"],
      [result.summary.timed_out_count ?? "—", "timed out"],
      [result.summary.focus_loss_count ?? "—", "focus losses"],
    ];
    metrics.forEach(([value, label]) => {
      const block = document.createElement("div");
      const strong = document.createElement("strong");
      const span = document.createElement("span");
      strong.textContent = value;
      span.textContent = label;
      block.append(strong, span);
      summary.appendChild(block);
    });
    const questions = $("#review-questions");
    questions.replaceChildren();
    result.questions.forEach((item) => {
      const card = document.createElement("article");
      card.className = "review-question";
      const outcome = item.timed_out ? "Timed out" : item.is_correct ? "Correct" : item.is_correct === false ? "Incorrect" : "Not answered";
      card.innerHTML = `
        <h3></h3>
        <div class="review-meta">
          <span class="${item.is_correct ? "correct" : "incorrect"}">${outcome}</span>
          <span>${item.difficulty}</span>
          <span>${item.response_ms == null ? "No time" : `${(item.response_ms / 1000).toFixed(1)}s`}</span>
          <span></span>
        </div>
        <ol class="review-options"></ol>
        <div class="source-evidence"><strong>Supporting passage</strong><br><span></span></div>`;
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
    setMessage($("#admin-message"), error.message, "error");
  }
}

$("#close-review").addEventListener("click", () => $("#review-panel").classList.add("hidden"));

async function downloadReport(assessmentId, studentId) {
  try {
    const response = await fetch(`/api/admin/assessments/${assessmentId}/report.pdf`, {
      headers: { "X-Admin-Key": state.adminKey },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "The report could not be downloaded.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `kanokwere-${studentId.replaceAll("/", "-")}.pdf`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    setMessage($("#admin-message"), error.message, "error");
  }
}


async function resetAttempt(assessmentId, studentName) {
  if (!confirm(`Reset ${studentName}'s unfinished attempt? The current responses will be deleted.`)) return;
  try {
    await api(`/api/admin/assessments/${assessmentId}`, {
      method: "DELETE",
      headers: { "X-Admin-Key": state.adminKey },
    });
    await loadSubmissions();
  } catch (error) {
    setMessage($("#admin-message"), error.message, "error");
  }
}

async function deleteSubmission(documentId, studentName) {
  if (!confirm(`Delete ${studentName}'s document, questions, assessments, and reports? This cannot be undone.`)) return;
  try {
    await api(`/api/admin/documents/${documentId}`, {
      method: "DELETE",
      headers: { "X-Admin-Key": state.adminKey },
    });
    await loadSubmissions();
  } catch (error) {
    setMessage($("#admin-message"), error.message, "error");
  }
}

if (state.documentId && !state.assessmentId) {
  showPanel("prepare", 2);
  pollDocumentStatus();
}
if (state.assessmentId && state.token) {
  state.testActive = true;
  showPanel("test", 3);
  loadQuestion();
}
