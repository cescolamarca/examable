const el = (id) => document.getElementById(id);
let defaultUserId = null;
let currentStudyQuestion = null;
let currentCorrection = null;
let cachedDocuments = [];
let cachedTagPresets = [];
const studyFilters = { documentId: "", tag: "", tagPreset: "", questionType: "", reviewMode: "unreviewed" };
const studySession = {
  correct: 0,
  wrong: 0,
  seenQuestionIds: new Set(),
  history: [],
  historyIndex: -1,
  shuffleNew: false
};
const customSimulation = { questions: [], index: 0 };

function normalizeOptionId(value) {
  return String(value || "")
    .trim()
    .replace(/[).:]/g, "")
    .toLowerCase();
}

function extractCorrectOptionId(solution) {
  if (!solution || typeof solution !== "object") return null;
  const candidateKeys = [
    "correct_option",
    "correctOption",
    "correct_answer",
    "correctAnswer",
    "answer",
    "option",
    "choice",
    "label"
  ];
  for (const key of candidateKeys) {
    if (solution[key] !== undefined && solution[key] !== null) {
      const normalized = normalizeOptionId(solution[key]);
      if (normalized) return normalized;
    }
  }
  if (Array.isArray(solution.correct_options) && solution.correct_options.length) {
    const normalized = normalizeOptionId(solution.correct_options[0]);
    if (normalized) return normalized;
  }
  return null;
}

function effectiveCorrectOptionId(solution, correction) {
  if (correction && typeof correction === "object") {
    const direct = normalizeOptionId(correction.correct_option_id || "");
    if (direct) return direct;
    const payload = correction.answer_payload && typeof correction.answer_payload === "object"
      ? correction.answer_payload
      : {};
    const fromNormalized = normalizeOptionId(payload.selected_option_normalized || "");
    if (fromNormalized) return fromNormalized;
    const fromRaw = normalizeOptionId(payload.selected_option_raw || "");
    if (fromRaw) return fromRaw;
  }
  return extractCorrectOptionId(solution);
}

async function api(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!res.ok) {
    throw new Error(payload.detail || payload.raw || `HTTP ${res.status}`);
  }
  return payload;
}

async function ensureDefaultUser() {
  if (defaultUserId) return defaultUserId;
  const user = await api("/users/default");
  defaultUserId = user.id;
  return defaultUserId;
}

function correctionStatusLabel(correction) {
  if (!correction || !correction.has_correction) return "Mancante";
  if (correction.correct_option_id && correction.explanation_text) return "Risposta + spiegazione";
  if (correction.correct_option_id) return "Risposta corretta";
  if (correction.explanation_text || (correction.answer_payload && Object.keys(correction.answer_payload).length)) {
    return "Spiegazione";
  }
  return "Mancante";
}

async function loadQuestionCorrection(questionId) {
  const userId = await ensureDefaultUser();
  return api(`/questions/${questionId}/correction?user_id=${encodeURIComponent(userId)}`);
}

async function setQuestionCorrection(questionId, payload) {
  const userId = await ensureDefaultUser();
  const correction = await api(`/questions/${questionId}/correction`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      correct_option_id: payload.correct_option_id || null,
      explanation_text: payload.explanation_text || null,
      answer_payload: payload.answer_payload || {}
    })
  });
  currentCorrection = correction;
  const badge = el("correction-status-pill");
  if (badge) badge.textContent = `Correzione: ${correctionStatusLabel(currentCorrection)}`;
}

async function discardQuestion(questionId, discarded = true) {
  return api(`/questions/${questionId}/discard?discarded=${discarded ? "true" : "false"}`, {
    method: "POST"
  });
}

function updateStudyCounter() {
  el("study-counter").textContent = `Sessione: ${studySession.correct} corrette / ${studySession.wrong} sbagliate`;
}

function updateShuffleNewButton() {
  const btn = el("toggle-shuffle-new");
  if (!btn) return;
  const on = studySession.shuffleNew;
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Mischia nuove: on" : "Mischia nuove: off";
  btn.classList.toggle("active", on);
}

function updateStudyNavButtons() {
  const prevBtn = el("prev-study-question");
  if (!prevBtn) return;
  prevBtn.disabled = studySession.historyIndex <= 0;
}

function pushStudyHistory(question, correction) {
  if (!question || !question.id) return;
  if (studySession.historyIndex < studySession.history.length - 1) {
    studySession.history = studySession.history.slice(0, studySession.historyIndex + 1);
  }
  const last = studySession.history[studySession.history.length - 1];
  if (last && last.question && last.question.id === question.id) {
    studySession.history[studySession.history.length - 1] = { question, correction };
    studySession.historyIndex = studySession.history.length - 1;
    updateStudyNavButtons();
    return;
  }
  studySession.history.push({ question, correction });
  studySession.historyIndex = studySession.history.length - 1;
  updateStudyNavButtons();
}

function showPreviousStudyQuestion() {
  const status = el("study-status");
  if (studySession.historyIndex <= 0) {
    if (status) status.textContent = "Nessuna domanda precedente in questa sessione.";
    updateStudyNavButtons();
    return;
  }
  studySession.historyIndex -= 1;
  const entry = studySession.history[studySession.historyIndex];
  if (!entry || !entry.question) {
    if (status) status.textContent = "Storico non disponibile.";
    updateStudyNavButtons();
    return;
  }
  currentStudyQuestion = entry.question;
  currentCorrection = entry.correction || null;
  renderStudyQuestion(currentStudyQuestion);
  if (status) {
    status.textContent = `Mostrata precedente (${studySession.historyIndex + 1}/${studySession.history.length})`;
    status.classList.remove("error");
  }
  updateStudyNavButtons();
}

function renderStudyDocumentFilter(rows) {
  const options = rows
    .map((doc) => {
      const title = doc.title || "Documento senza titolo";
      return `<option value="${doc.id}">${title} (${doc.id.slice(0, 8)}...)</option>`;
    })
    .join("");
  const select = el("study-filter-document-id");
  const current = select.value;
  select.innerHTML = `<option value="">Tutti i documenti</option>${options}`;
  if (rows.some((d) => d.id === current)) {
    select.value = current;
  } else {
    select.value = "";
  }
  const simSelect = el("sim-document-id");
  if (simSelect) {
    const simCurrent = simSelect.value;
    simSelect.innerHTML = `<option value="">Tutti i documenti</option>${options}`;
    if (rows.some((d) => d.id === simCurrent)) {
      simSelect.value = simCurrent;
    } else {
      simSelect.value = "";
    }
  }
}

async function refreshDocuments() {
  const docs = await api("/documents?limit=200");
  cachedDocuments = docs;
  renderStudyDocumentFilter(docs);
}

function renderTagPresetOptions() {
  const options = cachedTagPresets
    .map((p) => `<option value="${p.slug}">${p.name}</option>`)
    .join("");
  const studySelect = el("study-filter-tag-preset");
  if (studySelect) {
    const prev = studySelect.value;
    studySelect.innerHTML = `<option value="">Nessun preset</option>${options}`;
    if (cachedTagPresets.some((p) => p.slug === prev)) studySelect.value = prev;
  }
  const simSelect = el("sim-tag-preset");
  if (simSelect) {
    const prev = simSelect.value;
    simSelect.innerHTML = `<option value="">Nessun preset</option>${options}`;
    if (cachedTagPresets.some((p) => p.slug === prev)) simSelect.value = prev;
  }
}

async function refreshTagPresets() {
  cachedTagPresets = await api("/tag-presets");
  renderTagPresetOptions();
}

function renderStudyQuestion(q) {
  const box = el("study-question");
  if (!q) {
    box.innerHTML = "<p class='muted'>Nessuna domanda pronta.</p>";
    return;
  }
  const isMultipleChoice = q.question_type === "multiple_choice";
  const correctOptionId = effectiveCorrectOptionId(q.solution, currentCorrection);
  const opts = (q.options || [])
    .map((o) => {
      const normalizedStoredOption = normalizeOptionId(
        currentCorrection && currentCorrection.correct_option_id ? currentCorrection.correct_option_id : ""
      );
      const checked = normalizedStoredOption && normalizeOptionId(o.id) === normalizedStoredOption ? "checked" : "";
      if (!isMultipleChoice) return `<li><strong>${o.id}</strong> ${o.text}</li>`;
      return `<label class="option-choice">
        <input type="radio" name="mcq-answer" value="${o.id}" ${checked} />
        <span><strong>${o.id}</strong> ${o.text}</span>
      </label>`;
    })
    .join("");
  const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
  const tags = (q.tags || []).map((t) => `<span class="pill">${t}</span>`).join(" ");
  const hasSolution = q.solution && Object.keys(q.solution).length > 0;
  const hasUserCorrection = Boolean(
    currentCorrection &&
      (currentCorrection.correct_option_id ||
        (currentCorrection.answer_payload && Object.keys(currentCorrection.answer_payload).length) ||
        (currentCorrection.explanation_text && String(currentCorrection.explanation_text).trim()))
  );
  const userCorrectionMarkup = hasUserCorrection
    ? `<div class="pre">
         <strong>Correzione salvata</strong><br/>
         ${currentCorrection.correct_option_id ? `Opzione corretta: ${currentCorrection.correct_option_id}<br/>` : ""}
         ${
           currentCorrection.explanation_text
             ? `Spiegazione: ${String(currentCorrection.explanation_text).replaceAll("<", "&lt;").replaceAll(">", "&gt;")}`
             : ""
         }
       </div>`
    : "";
  const solutionMarkup = hasSolution
    ? `<div class="pre"><strong>Soluzione AI estratta</strong><br/><br/>${JSON.stringify(q.solution, null, 2)}</div>`
    : hasUserCorrection
      ? ""
      : "<p class='muted'>Nessuna soluzione AI presente. Per le scelta multipla seleziona l'opzione corretta e salva (spiegazione facoltativa).</p>";
  const mcqActions = isMultipleChoice
    ? `<button id="study-submit-answer">Conferma risposta</button>`
    : "";
  const correctionLabel = correctionStatusLabel(currentCorrection);
  const currentExplanation = currentCorrection && currentCorrection.explanation_text
    ? String(currentCorrection.explanation_text)
    : "";
  box.innerHTML = `
    <div class="question-header">
      <strong>${q.section} ${q.number_in_section}</strong>
      <span class="pill">${q.question_type}</span>
      <span id="correction-status-pill" class="pill">${`Correzione: ${correctionLabel}`}</span>
    </div>
    <div class="question-stem">${q.stem}</div>
    ${tags ? `<div class="row">${tags}</div>` : ""}
    ${opts ? `<ul>${opts}</ul>` : ""}
    ${parts ? `<ul>${parts}</ul>` : ""}
    <div class="row question-actions">
      <button id="study-show-solution">Mostra soluzione</button>
      ${mcqActions}
      <button id="study-mark-correct" disabled>Risposta corretta</button>
      <button id="study-mark-wrong" class="mark-wrong" disabled>Risposta sbagliata</button>
      ${isMultipleChoice ? `<button id="correction-save-option" class="secondary">Salva opzione corretta</button>` : ""}
      <button id="question-discard" class="mark-wrong">Scarta dal database</button>
      <span id="study-answer-status" class="muted small"></span>
    </div>
    <div class="row correction-save-row">
      <textarea id="correction-explanation" class="explanation-input" rows="3" placeholder="${
        isMultipleChoice
          ? "Spiegazione facoltativa..."
          : "Inserisci la correzione / spiegazione per questa domanda..."
      }">${currentExplanation}</textarea>
      <button type="button" id="correction-save" class="secondary">Salva correzione</button>
    </div>
    <div id="study-solution" class="hidden">${userCorrectionMarkup}${solutionMarkup}</div>
  `;

  el("study-show-solution").addEventListener("click", () => {
    el("study-solution").classList.remove("hidden");
    el("study-mark-correct").disabled = false;
    el("study-mark-wrong").disabled = false;
  });
  if (isMultipleChoice) {
    el("study-submit-answer").addEventListener("click", async () => {
      const selected = box.querySelector('input[name="mcq-answer"]:checked');
      const status = el("study-answer-status");
      if (!selected) {
        if (status) status.textContent = "Seleziona un'opzione prima di confermare.";
        return;
      }
      const chosen = normalizeOptionId(selected.value);
      if (!correctOptionId) {
        // Fallback manuale: registra tentativo senza auto-valutazione.
        if (status) status.textContent = "Nessuna opzione corretta salvata per questa domanda.";
        return;
      }
      const isCorrect = chosen === correctOptionId;
      await submitStudyAttempt(isCorrect, { selected_option: selected.value, auto_evaluated: true });
    });
  }
  el("study-mark-correct").addEventListener("click", async () => {
    await submitStudyAttempt(true, { manual_mark: "correct" });
  });
  el("study-mark-wrong").addEventListener("click", async () => {
    await submitStudyAttempt(false, { manual_mark: "wrong" });
  });
  el("question-discard").addEventListener("click", async () => {
    const status = el("study-answer-status");
    const ok = window.confirm("Scartare questa domanda dal database? Non verra' piu' usata in allenamento/simulazioni.");
    if (!ok) return;
    try {
      if (status) status.textContent = "Scarto domanda...";
      await discardQuestion(q.id, true);
      await refreshReviewStats();
      if (status) status.textContent = "Domanda scartata dal database.";
      await loadNextStudyQuestion(true, true);
    } catch (err) {
      if (status) status.textContent = err.message;
    }
  });
  el("correction-save").addEventListener("click", async () => {
    const status = el("study-answer-status");
    const explanationRaw = el("correction-explanation")?.value ?? "";
    const explanationTrimmed = explanationRaw.trim();
    const explanation = explanationTrimmed || null;

    try {
      if (isMultipleChoice) {
        const selected = box.querySelector('input[name="mcq-answer"]:checked');
        if (!selected) {
          if (status) status.textContent = "Seleziona l'opzione corretta prima di salvare.";
          return;
        }
        if (status) status.textContent = "Salvataggio correzione...";
        const selectedLabel = selected.closest("label")?.querySelector("span")?.textContent?.trim() || "";
        const selectedValue = String(selected.value || "").trim();
        const normalizedValue = normalizeOptionId(selectedValue);
        await setQuestionCorrection(q.id, {
          correct_option_id: selectedValue || normalizedValue || null,
          explanation_text: explanation,
          answer_payload: {
            selected_option_raw: selectedValue || null,
            selected_option_normalized: normalizedValue || null,
            selected_option_text: selectedLabel || null
          }
        });
        await refreshReviewStats();
        if (status) {
          status.textContent = explanation
            ? "Correzione salvata (opzione e spiegazione)."
            : "Correzione salvata (solo opzione corretta).";
        }
        setTimeout(() => loadNextStudyQuestion(true, true), 260);
        return;
      }

      if (!explanation) {
        if (status) status.textContent = "Inserisci una correzione testuale prima di salvare.";
        return;
      }
      if (status) status.textContent = "Salvataggio correzione...";
      const existingPayload =
        currentCorrection && currentCorrection.answer_payload && typeof currentCorrection.answer_payload === "object"
          ? currentCorrection.answer_payload
          : {};
      await setQuestionCorrection(q.id, {
        correct_option_id: null,
        explanation_text: explanation,
        answer_payload: Object.keys(existingPayload).length ? existingPayload : {}
      });
      await refreshReviewStats();
      if (status) status.textContent = "Correzione salvata.";
      await loadNextStudyQuestion(true, true);
    } catch (err) {
      if (status) status.textContent = err.message;
    }
  });
}

function buildStudyNextUrl(userId, excludeQuestionId = "", preferNew = false) {
  const query = new URLSearchParams();
  if (studyFilters.documentId) query.set("document_id", studyFilters.documentId);
  if (studyFilters.tag) query.set("tag", studyFilters.tag);
  if (studyFilters.tagPreset) query.set("tag_preset", studyFilters.tagPreset);
  if (studyFilters.questionType) query.set("question_type", studyFilters.questionType);
  if (studyFilters.reviewMode) query.set("review_filter", studyFilters.reviewMode);
  if (excludeQuestionId) query.set("exclude_question_id", excludeQuestionId);
  if (studySession.seenQuestionIds.size) {
    const seenIds = Array.from(studySession.seenQuestionIds).slice(-300);
    query.set("exclude_question_ids", seenIds.join(","));
  }
  if (preferNew) query.set("prefer_new", "true");
  if (studySession.shuffleNew) query.set("shuffle_new", "true");
  const suffix = query.toString();
  return suffix ? `/study/next/${userId}?${suffix}` : `/study/next/${userId}`;
}

function buildReviewStatsUrl(userId) {
  const query = new URLSearchParams();
  if (studyFilters.documentId) query.set("document_id", studyFilters.documentId);
  if (studyFilters.tag) query.set("tag", studyFilters.tag);
  if (studyFilters.tagPreset) query.set("tag_preset", studyFilters.tagPreset);
  if (studyFilters.questionType) query.set("question_type", studyFilters.questionType);
  const suffix = query.toString();
  return suffix ? `/reviews/stats/${userId}?${suffix}` : `/reviews/stats/${userId}`;
}

async function refreshReviewStats() {
  try {
    const userId = await ensureDefaultUser();
    const stats = await api(buildReviewStatsUrl(userId));
    el("review-kpi-total").textContent = String(stats.total || 0);
    el("review-kpi-unreviewed").textContent = String(stats.without_correction || 0);
    el("review-kpi-correct").textContent = String(stats.with_correct_option || 0);
    el("review-kpi-wrong").textContent = String(stats.with_explanation || 0);
  } catch {
    // Keep UI usable even if stats endpoint fails.
  }
}

function applyStudyFilters() {
  studyFilters.documentId = el("study-filter-document-id").value.trim();
  studyFilters.tag = el("study-filter-tag").value.trim();
  studyFilters.tagPreset = el("study-filter-tag-preset").value.trim();
  studyFilters.questionType = el("study-filter-question-type").value.trim();
  studyFilters.reviewMode = el("study-filter-review-mode").value.trim() || "all";

  currentStudyQuestion = null;
  currentCorrection = null;
  studySession.seenQuestionIds.clear();
  studySession.history = [];
  studySession.historyIndex = -1;
  renderStudyQuestion(null);
  updateStudyNavButtons();

  const status = el("study-status");
  const active = [];
  if (studyFilters.documentId) {
    const doc = cachedDocuments.find((d) => d.id === studyFilters.documentId);
    active.push(`documento=${doc ? doc.title || doc.id : studyFilters.documentId}`);
  }
  if (studyFilters.tag) {
    const tagParts = studyFilters.tag
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean);
    active.push(`tag=${tagParts.length > 1 ? tagParts.join(" OR ") : studyFilters.tag}`);
  }
  if (studyFilters.tagPreset) {
    const preset = cachedTagPresets.find((p) => p.slug === studyFilters.tagPreset);
    active.push(`preset=${preset ? preset.name : studyFilters.tagPreset}`);
  }
  if (studyFilters.questionType) active.push(`tipo=${studyFilters.questionType}`);
  if (studyFilters.reviewMode && studyFilters.reviewMode !== "all") {
    active.push(`review=${studyFilters.reviewMode}`);
  }
  status.textContent = active.length ? `Filtri attivi: ${active.join(" | ")}` : "Filtri rimossi";
  status.classList.remove("error");
  refreshReviewStats();
}

async function loadNextStudyQuestion(excludeCurrent = false, preferNew = false) {
  const status = el("study-status");
  try {
    status.textContent = "Caricamento...";
    const userId = await ensureDefaultUser();
    const excludeId = excludeCurrent && currentStudyQuestion ? currentStudyQuestion.id : "";
    const next = await api(buildStudyNextUrl(userId, excludeId, preferNew));
    const q = await api(`/questions/${next.question_id}`);
    const correction = await loadQuestionCorrection(q.id);
    currentCorrection = correction || null;
    currentStudyQuestion = q;
    studySession.seenQuestionIds.add(String(q.id));
    pushStudyHistory(q, currentCorrection);
    renderStudyQuestion(q);
    status.textContent = `Mostrata (${next.due_reason})`;
    status.classList.remove("error");
  } catch (err) {
    if (excludeCurrent && (err.message || "").includes("No questions available")) {
      await loadNextStudyQuestion(false, preferNew);
      return;
    }
    currentStudyQuestion = null;
    currentCorrection = null;
    renderStudyQuestion(null);
    updateStudyNavButtons();
    if ((err.message || "").includes("No questions available")) {
      const applied = [];
      if (studyFilters.documentId) {
        const doc = cachedDocuments.find((d) => d.id === studyFilters.documentId);
        applied.push(`documento=${doc ? doc.title || doc.id : studyFilters.documentId}`);
      }
      if (studyFilters.tag) applied.push(`tag=${studyFilters.tag}`);
      if (studyFilters.tagPreset) {
        const preset = cachedTagPresets.find((p) => p.slug === studyFilters.tagPreset);
        applied.push(`preset=${preset ? preset.name : studyFilters.tagPreset}`);
      }
      if (studyFilters.questionType) applied.push(`tipo=${studyFilters.questionType}`);
      status.textContent = applied.length
        ? `Nessuna domanda trovata con i filtri correnti (${applied.join(" | ")}).`
        : "Nessuna domanda disponibile.";
      status.classList.remove("error");
      return;
    }
    status.textContent = err.message;
    status.classList.add("error");
  }
}

async function submitStudyAttempt(isCorrect, answerPayload = {}) {
  const status = el("study-answer-status");
  if (!currentStudyQuestion) return;
  try {
    if (status) status.textContent = "Salvataggio...";
    const userId = await ensureDefaultUser();
    await api("/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        question_id: currentStudyQuestion.id,
        is_correct: isCorrect,
        grade: isCorrect ? 4 : 1,
        answer_payload: answerPayload
      })
    });
    if (isCorrect) studySession.correct += 1;
    else studySession.wrong += 1;
    updateStudyCounter();
    await loadNextStudyQuestion(false);
  } catch (err) {
    if (status) {
      status.textContent = err.message;
      status.classList.add("error");
    }
  }
}

function renderSimulationQuestion() {
  const box = el("simulation-question");
  const progress = el("simulation-progress");
  if (!customSimulation.questions.length) {
    box.innerHTML = "<p class='muted'>Nessuna simulazione generata.</p>";
    progress.textContent = "";
    return;
  }
  const q = customSimulation.questions[customSimulation.index];
  const opts = (q.options || []).map((o) => `<li><strong>${o.id}</strong> ${o.text}</li>`).join("");
  const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
  const tags = (q.tags || []).map((t) => `<span class="pill">${t}</span>`).join(" ");
  box.innerHTML = `
    <div class="question-header">
      <strong>${q.section} ${q.number_in_section}</strong>
      <span class="pill">${q.question_type}</span>
    </div>
    <div class="question-stem">${q.stem}</div>
    ${tags ? `<div class="row">${tags}</div>` : ""}
    ${opts ? `<ul>${opts}</ul>` : ""}
    ${parts ? `<ul>${parts}</ul>` : ""}
  `;
  progress.textContent = `Domanda ${customSimulation.index + 1} / ${customSimulation.questions.length}`;
}

function readCountInput(id) {
  const raw = (el(id).value || "0").trim();
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

async function generateCustomSimulation() {
  const status = el("simulation-status");
  try {
    const userId = await ensureDefaultUser();
    const payload = {
      multiple_choice_count: readCountInput("sim-mcq-count"),
      open_text_count: readCountInput("sim-open-count"),
      multi_part_open_count: readCountInput("sim-multi-count"),
      tag: el("sim-tag").value.trim() || null,
      tag_preset: el("sim-tag-preset").value.trim() || null,
      document_id: el("sim-document-id").value.trim() || null,
      user_id: userId,
      only_reviewed_correct: Boolean(el("sim-only-reviewed-correct")?.checked)
    };
    if (
      payload.multiple_choice_count +
        payload.open_text_count +
        payload.multi_part_open_count <=
      0
    ) {
      status.textContent = "Inserisci almeno 1 domanda.";
      status.classList.add("error");
      return;
    }
    status.textContent = "Generazione simulazione...";
    status.classList.remove("error");
    const result = await api("/simulations/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    customSimulation.questions = result.questions || [];
    customSimulation.index = 0;
    if (!customSimulation.questions.length) {
      status.textContent = "Nessuna domanda disponibile con i filtri scelti.";
      renderSimulationQuestion();
      return;
    }
    const shortage = result.shortage_by_type || {};
    const shortageParts = Object.keys(shortage).map((k) => `${k}: -${shortage[k]}`);
    status.textContent = shortageParts.length
      ? `Generate ${result.generated_total} domande (disponibilita' ridotta: ${shortageParts.join(", ")}).`
      : `Generate ${result.generated_total} domande.`;
    renderSimulationQuestion();
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  }
}

function simulationNext() {
  if (!customSimulation.questions.length) return;
  customSimulation.index = Math.min(customSimulation.index + 1, customSimulation.questions.length - 1);
  renderSimulationQuestion();
}

function simulationPrev() {
  if (!customSimulation.questions.length) return;
  customSimulation.index = Math.max(customSimulation.index - 1, 0);
  renderSimulationQuestion();
}

window.addEventListener("DOMContentLoaded", async () => {
  el("apply-study-filters").addEventListener("click", applyStudyFilters);
  el("refresh-review-stats").addEventListener("click", refreshReviewStats);
  el("prev-study-question").addEventListener("click", showPreviousStudyQuestion);
  el("next-study-question").addEventListener("click", async () => {
    await loadNextStudyQuestion(true, true);
  });
  el("toggle-shuffle-new").addEventListener("click", () => {
    studySession.shuffleNew = !studySession.shuffleNew;
    updateShuffleNewButton();
  });
  el("reset-study-counter").addEventListener("click", () => {
    studySession.correct = 0;
    studySession.wrong = 0;
    studySession.seenQuestionIds.clear();
    studySession.history = [];
    studySession.historyIndex = -1;
    updateStudyCounter();
    updateStudyNavButtons();
  });
  el("generate-simulation").addEventListener("click", generateCustomSimulation);
  el("simulation-next").addEventListener("click", simulationNext);
  el("simulation-prev").addEventListener("click", simulationPrev);
  await ensureDefaultUser();
  await refreshDocuments();
  await refreshTagPresets();
  updateStudyCounter();
  updateShuffleNewButton();
  updateStudyNavButtons();
  renderStudyQuestion(null);
  renderSimulationQuestion();
  await refreshReviewStats();
  await loadNextStudyQuestion(false);
});
