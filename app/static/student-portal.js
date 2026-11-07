/* Optional student portal and low-bandwidth oral evidence. No biometric matching. */
(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  let student = null;
  let oralContext = null;
  let recording = null;
  let identityPrepared = null;
  let rosterPage = 1;
  let rosterHasNext = false;
  let rosterCourses = [];
  const urls = new Set();
  const pendingKey = "kanokware_pending_oral";

  function message(id, text, type = "") { setMessage(el(id), text, type); }
  function button(label, action, className = "secondary-button") {
    const node = document.createElement("button");
    node.type = "button"; node.className = className; node.textContent = label;
    node.addEventListener("click", action); return node;
  }
  function card(title, body) {
    const node = document.createElement("article"); node.className = "portal-card";
    const heading = document.createElement("h3"); heading.textContent = title;
    const paragraph = document.createElement("p"); paragraph.textContent = body;
    node.append(heading, paragraph); return node;
  }
  function jsonOptions(method, data) {
    return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
  }
  function oralHeaders() { return oralContext?.token ? { Authorization: `Bearer ${oralContext.token}` } : {}; }
  function forbidDuringExam() {
    if (state.testActive) throw new Error("Finish the active assessment before changing your student session.");
  }
  function syncFields() {
    ["student_name", "student_id"].forEach((name) => {
      const input = el("upload-form").elements[name];
      input.readOnly = Boolean(student);
      if (student) input.value = name === "student_name" ? student.full_name : student.student_number;
    });
    if (student) {
      el("exam-candidate-name").value = student.full_name;
      el("exam-candidate-id").value = student.student_number;
    }
  }
  async function workspace() {
    const me = await api("/api/student/me"); student = me.student;
    el("student-auth-forms").classList.toggle("hidden", Boolean(student));
    el("student-workspace").classList.toggle("hidden", !student);
    syncFields();
    if (!student) return;
    const result = await api("/api/student/workspace");
    student = result.student;
    el("student-account-name").textContent = `${student.full_name} · ${student.student_number}`;
    el("student-portal-id").textContent = `Your unique Portal ID: ${student.portal_id}. You can use this instead of your student ID to sign in.`;
    el("student-photo-status").textContent = `Latest photo: ${student.photo_status.replaceAll("_", " ")}. Approved reference ${student.verified_reference_available ? "available" : "not yet available"}.`;
    const courses = el("student-portal-courses"); courses.replaceChildren();
    result.courses.forEach((course) => {
      const node = card(course.title, "Use this course for coursework or an available standalone examination.");
      node.append(button("Open coursework", () => openEligibleAssessment(course.code, "coursework")));
      node.append(button("Browse standalone assessments", () => openEligibleAssessment(course.code, "exam")));
      courses.append(node);
    });
    if (!result.courses.length) courses.textContent = "No active courses yet. Use a course enrolment code above or contact your institution.";
    const available = el("student-portal-assessments"); available.replaceChildren();
    (result.assessments || []).forEach((task) => {
      const mode = task.mode === "standalone_exam" ? "exam" : "coursework";
      const node = card(task.title, `${task.course_title}. ${mode === "exam" ? "Standalone assessment" : "Coursework submission"}${task.requires_exam_pin ? ", examination access PIN required" : ""}.${task.opens_at ? ` Opens ${new Date(task.opens_at).toLocaleString()}.` : ""}`);
      node.append(button(mode === "exam" ? "Open assessment" : "Submit coursework", () => openEligibleAssessment(task.course_code, mode, task.id)));
      available.append(node);
    });
    if (!result.assessments?.length) available.textContent = "No eligible active assessment tasks are currently listed. Your lecturer controls exam eligibility.";
    const attempts = el("student-portal-attempts"); attempts.replaceChildren();
    result.attempts.forEach((attempt) => {
      const node = card(attempt.title, `MCQ: ${attempt.mcq_score == null ? attempt.status : `${attempt.mcq_score}%`}. Identity: ${attempt.identity_status.replaceAll("_", " ")}.`);
      if (attempt.oral_enabled && attempt.status === "completed") node.append(button("Open oral verification", () => openOral(attempt.assessment_id)));
      attempts.append(node);
    });
    if (!result.attempts.length) attempts.textContent = "Your linked assessments will appear here after you start them while signed in.";
  }

  async function openEligibleAssessment(code, mode, taskId = null) {
    try {
      forbidDuringExam(); showView("home"); setStudentEntryMode(mode, false);
      el("student-course-code").value = code;
      const course = await resolveStudentCourse(false);
      if (!course) throw new Error("This course is unavailable. Contact your lecturer.");
      if (taskId) { el("student-assessment-task").value = taskId; applySelectedAssessmentTask(); }
      syncFields(); el("student-access-workflow").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { message("student-portal-message", error.message, "error"); }
  }

  ["login", "activate"].forEach((action) => {
    el(`student-${action}-form`).addEventListener("submit", async (event) => {
      event.preventDefault(); const form = event.currentTarget; const submit = event.submitter;
      submit.disabled = true;
      try {
        forbidDuringExam(); const data = Object.fromEntries(new FormData(form));
        if (action === "activate") {
          if (data.pin !== data.confirm_pin) throw new Error("The private PINs do not match.");
          delete data.confirm_pin;
          if (!data.course_code.trim()) delete data.course_code;
        } else if (!/^[0-9]{6}$/.test(data.pin)) {
          data.password = data.pin; delete data.pin;
        }
        await api(`/api/student/${action}`, jsonOptions("POST", data)); form.reset();
        await workspace(); message("student-portal-message", "Signed in. Open your eligible coursework or assessment below.", "success");
      } catch (error) { message("student-portal-message", error.message, "error"); }
      finally { submit.disabled = false; }
    });
  });
  el("student-enrol-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true;
    try {
      forbidDuringExam();
      const result = await api("/api/student/enrol", jsonOptions("POST", Object.fromEntries(new FormData(event.currentTarget))));
      event.currentTarget.reset(); await workspace(); message("student-portal-message", result.message, "success");
    } catch (error) { message("student-portal-message", error.message, "error"); }
    finally { submit.disabled = false; }
  });
  el("student-signout").addEventListener("click", async () => {
    try {
      forbidDuringExam();
      if (recording) throw new Error("Stop the oral recording before signing out.");
      await api("/api/student/logout", { method: "POST" });
      oralContext = null; el("student-oral-panel").classList.add("hidden");
      sessionStorage.removeItem(pendingKey);
      await workspace(); message("student-portal-message", "Signed out.");
    } catch (error) { message("student-portal-message", error.message, "error"); }
  });
  el("student-photo-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true;
    try {
      const data = new FormData(event.currentTarget); data.set("consent", "true");
      const result = await api("/api/student/reference-photo", { method: "POST", body: data });
      event.currentTarget.reset(); await workspace(); message("student-portal-message", result.message, "success");
    } catch (error) { message("student-portal-message", error.message, "error"); }
    finally { submit.disabled = false; }
  });
  document.querySelectorAll('[data-view-link="student-portal"]').forEach((node) => node.addEventListener("click", () => {
    if (state.testActive) return;
    workspace().catch((error) => message("student-portal-message", error.message, "error"));
  }));

  function courses(items) {
    rosterCourses = items;
    const select = el("portal-roster-course"); const selected = select.value;
    select.replaceChildren(new Option("Select course", ""));
    items.forEach((course) => select.append(new Option(`${course.course_code} · ${course.title}`, course.id)));
    if (items.some((course) => course.id === selected)) select.value = selected;
  }
  function rosterBase() {
    const id = el("portal-roster-course").value;
    if (!id) throw new Error("Select a course first.");
    return `/api/lecturer/courses/${id}/students`;
  }
  async function roster() {
    try {
      const base = rosterBase(); const result = await api(`${base}?page=${rosterPage}&search=${encodeURIComponent(el("student-roster-search").value)}`);
      rosterHasNext = Boolean(result.pagination?.has_next);
      el("student-roster-page").textContent = `Page ${rosterPage} · ${result.pagination?.total || 0} students`;
      el("student-roster-prev").disabled = rosterPage <= 1;
      el("student-roster-next").disabled = !rosterHasNext;
      const list = el("student-roster-list"); list.replaceChildren();
      result.students.forEach((row) => {
        const node = card(`${row.full_name} · ${row.student_number}`, `${row.status.replaceAll("_", " ")} · Photo: ${row.photo_status.replaceAll("_", " ")}`);
        if (row.photo_url) {
          const image = document.createElement("img"); image.src = row.photo_url; image.alt = `Submitted reference photograph for ${row.full_name}`; image.className = "portal-photo";
          node.append(image);
          ["verified", "review_required"].forEach((status) => node.append(button(status === "verified" ? "Verify against institutional record" : "Request identity review", async () => {
            const note = prompt(status === "verified" ? "Describe the trusted student record or supervised check used. A selfie alone does not establish identity." : "Explain the identity check required. This is not a misconduct finding.");
            if (!note) return;
            try { await api(`${base}/${row.id}/photo/${row.photo_id}/review`, jsonOptions("POST", { status, note })); await roster(); }
            catch (error) { message("student-roster-message", error.message, "error"); }
          })));
        }
        node.append(button("Issue replacement setup code", async () => {
          if (!confirm(`Revoke student sessions and reset account access for ${row.student_number}? Distribute the new code privately.`)) return;
          try { const result = await api(`${base}/${row.id}/reset`, { method: "POST" }); showCodes([result]); await roster(); }
          catch (error) { message("student-roster-message", error.message, "error"); }
        })); list.append(node);
      });
      if (!result.students.length) list.textContent = "No student accounts enrolled. Add students or upload a CSV.";
    } catch (error) { message("student-roster-message", error.message, "error"); }
  }
  function showCodes(rows) {
    const output = el("student-provision-codes"); output.classList.remove("hidden");
    output.textContent = rows.map((row) => `${row.student_number} | Portal ID: ${row.portal_id}\nSetup code: ${row.setup_code || "existing account, no new setup code"}`).join("\n\n");
  }
  function taskOptions() {
    const course = rosterCourses.find((row) => row.id === el("portal-roster-course").value);
    const select = el("portal-existing-task"); select.replaceChildren(new Option("Select task", ""));
    (course?.assessment_tasks || []).forEach((task) => select.append(new Option(task.title, task.id)));
    el("portal-existing-features").replaceChildren();
  }
  el("portal-roster-course").addEventListener("change", () => { rosterPage = 1; el("student-provision-codes").textContent = ""; taskOptions(); roster(); });
  el("student-roster-prev").addEventListener("click", () => { if (rosterPage > 1) rosterPage -= 1; roster(); });
  el("student-roster-next").addEventListener("click", () => { if (rosterHasNext) rosterPage += 1; roster(); });
  el("student-roster-find").addEventListener("click", () => { rosterPage = 1; roster(); });
  el("portal-existing-task").addEventListener("change", () => {
    const course = rosterCourses.find((row) => row.id === el("portal-roster-course").value);
    const task = course?.assessment_tasks.find((row) => row.id === el("portal-existing-task").value);
    const target = el("portal-existing-features"); target.replaceChildren();
    if (!task) return;
    task.features.forEach((feature) => {
      const label = document.createElement("label"); label.className = "checkbox-setting";
      const input = document.createElement("input"); input.type = "checkbox";
      input.dataset.featureKey = feature.key; input.checked = feature.effective;
      input.disabled = feature.activation_mode !== "lecturer_allowed";
      label.append(input, document.createTextNode(`${feature.label}${input.disabled ? " (administrator controlled)" : ""}`));
      target.append(label);
    });
    const form = el("portal-task-controls"); const config = task.distance_settings;
    ["oral_question_count", "oral_response_seconds", "oral_window_hours"].forEach((key) => { form.elements[key].value = config[key]; });
    form.elements.typed_alternative.checked = config.typed_alternative;
    form.elements.proctor_groups.value = config.proctor_groups.join("\n");
  });
  el("portal-task-controls").addEventListener("submit", async (event) => {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true;
    try {
      const courseId = el("portal-roster-course").value; const taskId = el("portal-existing-task").value;
      if (!courseId || !taskId) throw new Error("Select the course and task first.");
      const form = event.currentTarget; const data = Object.fromEntries(new FormData(form));
      const features = {}; el("portal-existing-features").querySelectorAll("input:not(:disabled)").forEach((input) => { features[input.dataset.featureKey] = input.checked; });
      const base = `/api/lecturer/courses/${courseId}/tasks/${taskId}`;
      const config = { oral_question_count: Number(data.oral_question_count), oral_response_seconds: Number(data.oral_response_seconds),
        oral_window_hours: Number(data.oral_window_hours), typed_alternative: form.elements.typed_alternative.checked,
        proctor_groups: data.proctor_groups.split("\n").filter((value) => value.trim()) };
      await api(`${base}/distance-settings`, jsonOptions("PUT", config));
      await api(base, jsonOptions("PATCH", { features }));
      await loadCourses(); taskOptions();
      message("student-roster-message", "Task controls saved for future attempts. Existing attempts retain their captured settings.", "success");
    } catch (error) { message("student-roster-message", error.message, "error"); }
    finally { submit.disabled = false; }
  });
  el("refresh-student-roster").addEventListener("click", roster);
  el("provision-student-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true;
    try {
      const result = await api(rosterBase(), jsonOptions("POST", Object.fromEntries(new FormData(event.currentTarget))));
      showCodes([result]); event.currentTarget.reset(); await roster();
    } catch (error) { message("student-roster-message", error.message, "error"); }
    finally { submit.disabled = false; }
  });
  el("import-students-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true;
    try {
      const result = await api(`${rosterBase()}/import`, { method: "POST", body: new FormData(event.currentTarget) });
      showCodes(result.students); event.currentTarget.reset(); await roster(); message("student-roster-message", result.message, "success");
    } catch (error) { message("student-roster-message", error.message, "error"); }
    finally { submit.disabled = false; }
  });

  async function ensureIdentity() {
    if (!state.activeFeatures.student_identity_verification || identityPrepared === state.assessmentId) return;
    const headers = { Authorization: `Bearer ${state.token}` };
    const status = await api(`/api/assessments/${state.assessmentId}/identity-status`, { headers });
    if (!status.capture_required) { identityPrepared = state.assessmentId; return; }
    const dialog = el("identity-capture-dialog"); dialog.classList.remove("hidden");
    const video = el("identity-capture-video"); let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      video.srcObject = stream; await video.play();
      await new Promise((resolve) => {
        el("identity-capture-confirm").onclick = async () => {
          const capture = el("identity-capture-confirm"); capture.disabled = true;
          try {
            if (!el("identity-capture-consent").checked) throw new Error("Confirm photo retention before capture.");
            if (!video.videoWidth) throw new Error("Wait for the camera preview, then retry.");
            const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
            canvas.getContext("2d").drawImage(video, 0, 0);
            const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.82));
            const data = new FormData(); data.set("photo", blob, "identity.jpg"); data.set("consent", "true");
            await api(`/api/assessments/${state.assessmentId}/identity-photo`, { method: "POST", headers, body: data });
            identityPrepared = state.assessmentId; resolve();
          } catch (error) { message("identity-capture-message", error.message, "error"); }
          finally { capture.disabled = false; }
        };
        el("identity-capture-confirm").focus();
      });
    } catch (error) {
      throw new Error(`Identity camera unavailable. Check permissions and retry, or contact your lecturer for an alternative verification arrangement. ${error.message}`);
    } finally {
      stream?.getTracks().forEach((track) => track.stop()); video.srcObject = null; dialog.classList.add("hidden");
    }
  }

  const retryIdentity = button("Retry identity capture", () => loadQuestion());
  retryIdentity.classList.add("hidden");
  el("test-message").after(retryIdentity);
  async function ensureIdentityWithRecovery() {
    retryIdentity.classList.add("hidden");
    try { await ensureIdentity(); }
    catch (error) { retryIdentity.classList.remove("hidden"); throw error; }
  }

  function speak(text) {
    if (!globalThis.speechSynthesis || !globalThis.SpeechSynthesisUtterance) throw new Error("Speech playback is unavailable. Use the displayed question text.");
    speechSynthesis.cancel(); const utterance = new SpeechSynthesisUtterance(text); utterance.lang = "en-GB";
    speechSynthesis.speak(utterance);
  }
  function questionAudio(question, features) {
    let node = el("read-question-aloud");
    if (!node) { node = button("Read question aloud", () => {}); node.id = "read-question-aloud"; el("question-heading").after(node); }
    node.classList.toggle("hidden", !features.audio_questions);
    node.onclick = () => {
      try { speak(`${question.stem}. ${question.options.map((option, index) => `${String.fromCharCode(65 + index)}. ${option}`).join(" ")}`); }
      catch (error) { message("test-message", error.message, "error"); }
    };
  }
  function resultActions(id, token, features) {
    el("open-oral-result")?.remove();
    if (!features.recorded_oral_responses) return;
    sessionStorage.setItem(pendingKey, JSON.stringify({ id, token }));
    const node = button("Continue to recorded oral verification", () => openOral(id, token), "primary-button");
    node.id = "open-oral-result"; el("result-disclaimer").after(node);
  }

  // Answers are saved on this browser until receipt is confirmed. IndexedDB is
  // used only for local recovery, never as evidence that an answer is genuine.
  function drafts() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open("kanokware-oral-drafts", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("answers");
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
  }
  async function draftAction(action, key, value) {
    const db = await drafts();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("answers", action === "get" ? "readonly" : "readwrite");
      const store = transaction.objectStore("answers");
      const request = action === "put" ? store.put(value, key) : action === "delete" ? store.delete(key) : store.get(key);
      let result; request.onsuccess = () => { result = request.result; };
      transaction.oncomplete = () => { db.close(); resolve(result); };
      transaction.onerror = () => { db.close(); reject(transaction.error); };
    });
  }
  async function openOral(id, token = null) {
    try {
      forbidDuringExam();
      if (recording) throw new Error("Stop the current recording first.");
      if (!token) {
        const saved = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
        if (saved?.id === id) token = saved.token;
      }
      oralContext = { id, token };
      const result = await api(`/api/assessments/${id}/oral`, { headers: oralHeaders() });
      if (!result.enabled) throw new Error("Recorded oral verification is not enabled for this assessment.");
      showView("student-portal"); el("student-oral-panel").classList.remove("hidden");
      el("student-oral-summary").textContent = `MCQ score: ${result.mcq_score}%. ${result.questions.filter((q) => q.submitted).length}/${result.questions.length} oral answers received. Window closes ${new Date(result.deadline).toLocaleString()}. Oral outcome: ${result.oral_outcome || "awaiting staff marking"}. Recording guide: ${result.settings.oral_response_seconds} seconds per answer.`;
      const list = el("student-oral-questions"); list.replaceChildren();
      for (const question of result.questions) await renderOralQuestion(list, question, result, { id, token });
      if (result.questions.every((q) => q.submitted)) sessionStorage.removeItem(pendingKey);
      el("student-oral-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { message("student-portal-message", error.message, "error"); }
  }

  async function renderOralQuestion(list, question, result, context) {
    const node = card(question.id.replace("oral-", "Question "), question.prompt); list.append(node);
    const status = document.createElement("p"); status.setAttribute("role", "status"); node.append(status);
    const read = button("Listen to question", () => { try { speak(question.prompt); } catch (error) { status.textContent = error.message; } }); node.append(read);
    if (question.submitted) { status.textContent = "Answer received by the server. Awaiting lecturer review."; return; }
    if (result.closed) { status.textContent = "Response window closed. Contact your lecturer if an accommodation is needed."; return; }
    const key = `${context.id}:${question.id}`;
    let saved = await draftAction("get", key).catch(() => null);
    let blob = saved?.blob || null;
    const player = document.createElement("audio"); player.controls = true; player.classList.toggle("hidden", !blob); node.append(player);
    function playback(value) {
      if (player.src) { URL.revokeObjectURL(player.src); urls.delete(player.src); }
      const url = URL.createObjectURL(value); urls.add(url); player.src = url; player.classList.remove("hidden");
    }
    if (blob) playback(blob);
    const typed = document.createElement("textarea"); typed.maxLength = 10000; typed.placeholder = "Approved typed alternative";
    typed.setAttribute("aria-label", `Typed alternative for ${question.id}`); typed.value = saved?.text || "";
    if (result.settings.typed_alternative) node.append(typed);
    typed.addEventListener("input", () => draftAction("put", key, { blob, text: typed.value }).catch(() => { status.textContent = "Local recovery storage is unavailable. Submit while connected."; }));
    const actions = document.createElement("div"); actions.className = "portal-actions"; node.append(actions);
    const record = button("Record answer", async () => {
      let stream;
      try {
        if (recording) throw new Error("Stop the other recording first.");
        if (!navigator.mediaDevices?.getUserMedia || !globalThis.MediaRecorder) throw new Error("Audio recording is unavailable. Use an approved alternative or contact your lecturer.");
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
        const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((value) => MediaRecorder.isTypeSupported(value));
        const recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 24000 } : {});
        const parts = []; let size = 0; const start = Date.now();
        recording = recorder; record.disabled = true; stop.disabled = false; submit.disabled = true;
        recorder.ondataavailable = (event) => {
          if (!event.data.size) return; parts.push(event.data); size += event.data.size;
          blob = new Blob(parts, { type: recorder.mimeType });
          draftAction("put", key, { blob, text: "" }).catch(() => { status.textContent = "Recording, but local recovery storage is unavailable. Keep this page open."; });
          if (size > 9 * 1024 * 1024 && recorder.state === "recording") recorder.stop();
        };
        const clock = setInterval(() => { status.textContent = `Recording: ${Math.floor((Date.now() - start) / 1000)} seconds. Stop, listen and submit when ready.`; }, 1000);
        const timer = setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, result.settings.oral_response_seconds * 1000);
        recorder.onstop = () => {
          clearInterval(clock); clearTimeout(timer); stream.getTracks().forEach((track) => track.stop());
          recording = null; record.disabled = false; stop.disabled = true; submit.disabled = false;
          typed.value = ""; if (blob) playback(blob); status.textContent = "Recorded locally. Listen, then submit. It is not received until the server confirms it.";
        };
        recorder.onerror = () => { if (recorder.state !== "inactive") recorder.stop(); status.textContent = "Recording failed. Retry or contact your lecturer."; };
        globalThis.speechSynthesis?.cancel(); recorder.start(5000); status.textContent = "Recording started. You may retake before submitting.";
      } catch (error) { stream?.getTracks().forEach((track) => track.stop()); recording = null; record.disabled = false; status.textContent = error.message; }
    });
    const stop = button("Stop recording", () => { if (recording?.state === "recording") recording.stop(); }); stop.disabled = true;
    const discard = button("Discard local draft", async () => {
      if (recording) { status.textContent = "Stop recording first."; return; }
      blob = null; typed.value = ""; player.removeAttribute("src"); player.classList.add("hidden");
      await draftAction("delete", key); status.textContent = "Local draft discarded.";
    });
    const consentLabel = document.createElement("label"); consentLabel.className = "consent-row";
    const consent = document.createElement("input"); consent.type = "checkbox";
    consentLabel.append(consent, document.createTextNode(" I confirm this is my response and understand it will be retained for authorised marking.")); node.append(consentLabel);
    const submit = button("Submit answer", async () => {
      submit.disabled = true;
      try {
        if (recording) throw new Error("Stop recording before submission.");
        if (!consent.checked) throw new Error("Confirm answer retention before submitting.");
        const text = typed.value.trim();
        if (!text && !blob) throw new Error("Record an answer or provide a permitted typed alternative.");
        const data = new FormData(); data.set("consent", "true");
        if (text) data.set("text", text); else data.set("audio", blob, "answer.webm");
        await api(`/api/assessments/${context.id}/oral/${question.id}`, { method: "POST", headers: context.token ? { Authorization: `Bearer ${context.token}` } : {}, body: data });
        await draftAction("delete", key).catch(() => {});
        status.textContent = "Answer received by the server. Awaiting lecturer review.";
        actions.replaceChildren(); consent.disabled = true; typed.disabled = true;
      } catch (error) { status.textContent = `${error.message} Your local draft has been retained. Retry when connected.`; submit.disabled = false; }
    }, "primary-button");
    actions.append(record, stop, discard, submit);
  }

  async function review(id) {
    const target = el("distance-evidence-review"); target.classList.add("hidden"); target.replaceChildren();
    if (!id) return;
    try {
      const result = await api(`/api/lecturer/assessments/${id}/distance-evidence`);
      if (!result.enabled || state.currentReviewAssessmentId !== id) return;
      target.classList.remove("hidden"); target.append(card("Student identity and oral evidence", result.disclaimer));
      const identity = card("Identity review", `${result.student ? `${result.student.full_name} · ${result.student.student_number}` : "No linked student account"}. Status: ${result.identity_status.replaceAll("_", " ")}. ${result.identity_note || ""}`);
      const pair = document.createElement("div"); pair.className = "portal-photo-pair";
      [["Approved reference", result.reference_url], ["Assessment capture", result.capture_url]].forEach(([label, url]) => {
        if (!url) return; const wrapper = card(label, ""); const image = document.createElement("img"); image.src = url; image.alt = label; image.className = "portal-photo"; wrapper.append(image); pair.append(wrapper);
      }); identity.append(pair);
      if (result.reference_url && result.capture_url) ["verified", "review_required"].forEach((status) => identity.append(button(status === "verified" ? "Record identity verified" : "Further identity review", async () => {
        const note = prompt("Record the comparison and any follow-up. A mismatch is not proof of cheating."); if (!note) return;
        try { await api(`/api/lecturer/assessments/${id}/identity-review`, jsonOptions("POST", { status, note })); await review(id); }
        catch (error) { message("lecturer-message", error.message, "error"); }
      })));
      target.append(identity);
      result.oral_questions.forEach((question) => {
        const response = result.responses.find((row) => row.question_id === question.id);
        const node = card(question.prompt, response?.typed_text || (response ? `Received ${new Date(response.received_at).toLocaleString()}` : "Answer not yet received."));
        if (response?.audio_url) { const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none"; audio.src = response.audio_url; node.append(audio); }
        target.append(node);
      });
    } catch (error) { target.classList.remove("hidden"); target.textContent = `Identity and oral evidence: ${error.message}`; }
  }

  globalThis.StudentPortal = { courses, ensureIdentity: ensureIdentityWithRecovery, questionAudio, resultActions, review, openOral };
  workspace().catch(() => {});
  if (state.courses.length) courses(state.courses);
  const pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
  if (pending?.id) {
    const node = button("Resume pending oral verification", () => openOral(pending.id, pending.token));
    el("student-oral-panel").before(node);
  }
  window.addEventListener("beforeunload", () => {
    if (recording?.state === "recording") recording.stop();
    urls.forEach((url) => URL.revokeObjectURL(url));
  });
})();
