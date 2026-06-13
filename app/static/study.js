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
const customSimulation = { questions: [], index: 0, simulationId: null };
const simulationCorrectionCache = new Map();
const immersionSession = {
  questions: [],
  index: 0,
  answered: false,
  selectedOptionId: "",
  correctCount: 0,
  wrongCount: 0,
  wrongQuestions: []
};

function setInlineStatus(target, message, isError = false) {
  if (!target) return;
  target.textContent = message;
  target.classList.toggle("error", isError);
  target.setAttribute("role", isError ? "alert" : "status");
  target.setAttribute("aria-live", "polite");
}

function buildMcqCopyText(question) {
  if (!question || question.question_type !== "multiple_choice") return "";
  const stem = String(question.stem || "").trim();
  const opts = Array.isArray(question.options) ? question.options : [];

  const lines = [];
  if (stem) lines.push(stem);
  lines.push("");

  for (const o of opts) {
    const id = String(o?.id ?? "").trim();
    const text = String(o?.text ?? "").trim();
    if (!id && !text) continue;
    lines.push(`${id} ${text}`.trim());
    lines.push("");
  }

  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines.join("\n");
}

async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }

  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "true");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.top = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return ok;
}

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
  el("study-counter").textContent = `${studySession.correct}✓  ${studySession.wrong}✗`;
}

function updateShuffleNewButton() {
  const btn = el("toggle-shuffle-new");
  if (!btn) return;
  const on = studySession.shuffleNew;
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Mischia: on" : "Mischia: off";
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
    setInlineStatus(status, `Mostrata precedente (${studySession.historyIndex + 1}/${studySession.history.length})`);
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
  const simSelect = el("sim-document-ids");
  if (simSelect) {
    const previouslySelected = Array.from(simSelect.selectedOptions).map((o) => o.value);
    simSelect.innerHTML = options;
    Array.from(simSelect.options).forEach((o) => {
      if (previouslySelected.includes(o.value)) o.selected = true;
    });
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
  const simSelect = el("sim-tag-presets");
  if (simSelect) {
    const previouslySelected = Array.from(simSelect.selectedOptions).map((o) => o.value);
    simSelect.innerHTML = options;
    Array.from(simSelect.options).forEach((o) => {
      if (previouslySelected.includes(o.value)) o.selected = true;
    });
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
  const correctionLabel = correctionStatusLabel(currentCorrection);
  const currentExplanation = currentCorrection && currentCorrection.explanation_text
    ? String(currentCorrection.explanation_text)
    : "";
  box.innerHTML = `
    <div class="question-header">
      <strong>${q.section} ${q.number_in_section}</strong>
      <span class="pill">${q.question_type}</span>
      ${tags ? tags : ""}
    </div>
    <div class="question-stem">${q.stem}</div>
    ${opts ? `<ul>${opts}</ul>` : ""}
    ${parts ? `<ul>${parts}</ul>` : ""}
    <div class="question-primary-actions">
      ${isMultipleChoice ? `<button id="study-submit-answer">Conferma risposta</button>` : ""}
      <button id="study-show-solution">Mostra risposta</button>
      <button id="study-mark-correct" class="mark-correct" disabled>Ho capito ✓</button>
      <button id="study-mark-wrong" class="mark-wrong" disabled>Non ho capito ✗</button>
      <span id="study-answer-status" class="muted small"></span>
    </div>
    <div id="study-solution" class="hidden">${userCorrectionMarkup}${solutionMarkup}</div>
    <details class="correction-details">
      <summary>Aggiungi / modifica correzione <span class="muted small">(${correctionLabel})</span></summary>
      <div class="correction-body">
        ${isMultipleChoice ? `<button id="correction-save-option" class="secondary">Salva opzione corretta</button>` : ""}
        <textarea id="correction-explanation" class="explanation-input" rows="3" placeholder="${
          isMultipleChoice
            ? "Spiegazione facoltativa..."
            : "Inserisci la correzione / spiegazione per questa domanda..."
        }">${currentExplanation}</textarea>
        <div class="row">
          <button type="button" id="correction-save" class="secondary">Salva correzione</button>
          ${isMultipleChoice ? `<button id="study-copy-question" class="btn-ghost" type="button">Copia domanda</button>` : ""}
          <button id="question-discard" class="btn-ghost danger-ghost">Scarta dal database</button>
        </div>
      </div>
    </details>
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

    el("study-copy-question").addEventListener("click", async () => {
      const status = el("study-answer-status");
      try {
        const text = buildMcqCopyText(q);
        if (!text) {
          setInlineStatus(status, "Niente da copiare per questa domanda.", true);
          return;
        }
        await copyTextToClipboard(text);
        setInlineStatus(status, "Copiato negli appunti.");
      } catch (err) {
        setInlineStatus(status, `Copia fallita: ${err?.message || err}`, true);
      }
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
  setInlineStatus(status, active.length ? `Filtri attivi: ${active.join(" | ")}` : "Filtri rimossi");
  refreshReviewStats();
}

async function loadNextStudyQuestion(excludeCurrent = false, preferNew = false) {
  const status = el("study-status");
  try {
    setInlineStatus(status, "Caricamento...");
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
    setInlineStatus(status, `Mostrata (${next.due_reason})`);
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
      setInlineStatus(
        status,
        applied.length
          ? `Nessuna domanda trovata con i filtri correnti (${applied.join(" | ")}).`
          : "Nessuna domanda disponibile."
      );
      return;
    }
    setInlineStatus(status, err.message, true);
  }
}

async function submitStudyAttempt(isCorrect, answerPayload = {}) {
  const status = el("study-answer-status");
  if (!currentStudyQuestion) return;
  try {
    if (status) setInlineStatus(status, "Salvataggio...");
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
      setInlineStatus(status, err.message, true);
    }
  }
}

async function submitImmersionAttempt(questionId, isCorrect, answerPayload = {}) {
  const userId = await ensureDefaultUser();
  await api("/attempts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      question_id: questionId,
      is_correct: isCorrect,
      grade: isCorrect ? 4 : 1,
      answer_payload: answerPayload,
      simulation_id: customSimulation.simulationId || null
    })
  });
}

async function getBestCorrectionOptionId(question) {
  const fromSolution = extractCorrectOptionId(question?.solution || {});
  if (!question?.id) return fromSolution || "";
  if (simulationCorrectionCache.has(question.id)) {
    return simulationCorrectionCache.get(question.id) || "";
  }
  // The user correction wins over the embedded solution (consistent with
  // effectiveCorrectOptionId), so an on-the-fly edit survives a refresh.
  let resolved = "";
  try {
    const correction = await loadQuestionCorrection(question.id);
    resolved = normalizeOptionId(correction?.correct_option_id || "");
  } catch {
    resolved = "";
  }
  if (!resolved) resolved = fromSolution || "";
  simulationCorrectionCache.set(question.id, resolved);
  return resolved;
}

function renderImmersionBaseState() {
  const tag = el("immersion-question-tag");
  const title = el("immersion-question-text");
  const optionsWrap = el("immersion-options");
  const feedback = el("immersion-feedback");
  const progress = el("simulation-progress");
  const progressText = el("immersion-progress-text");
  const progressBar = el("immersion-progress-bar");
  const score = el("immersion-score");
  const sessionTitle = el("immersion-session-title");
  const flagMenu = el("immersion-question-flag");
  if (!immersionSession.questions.length) {
    if (tag) tag.textContent = "Simulazione";
    if (flagMenu) flagMenu.innerHTML = "";
    if (title) title.textContent = "Genera una simulazione per iniziare la modalità full immersion.";
    if (optionsWrap) {
      optionsWrap.innerHTML = `
        <button type="button" class="immersion-option" disabled>
          <span class="immersion-option-letter">A</span>
          <span class="immersion-option-body">
            <strong>Sessione non avviata</strong>
            <span>Configura i parametri e premi "Genera simulazione".</span>
          </span>
        </button>
      `;
    }
    if (feedback) feedback.classList.add("hidden");
    if (progress) progress.textContent = "";
    if (progressText) progressText.textContent = "0/0";
    if (progressBar) progressBar.style.width = "0%";
    if (score) score.textContent = "Punteggio: 0 corrette / 0 sbagliate";
    if (sessionTitle) sessionTitle.textContent = "Sessione immersiva";
  }
}

async function handleImmersionOptionClick(optionId) {
  if (!immersionSession.questions.length) return;
  const q = immersionSession.questions[immersionSession.index];
  if (!q || q.question_type !== "multiple_choice") return;

  const isShowSolution = optionId === "";
  if (immersionSession.answered && !isShowSolution) return;
  
  const previouslyAnswered = immersionSession.answered;
  immersionSession.answered = true;
  if (!previouslyAnswered) {
    immersionSession.selectedOptionId = optionId;
  }

  const normalizedSelected = previouslyAnswered ? normalizeOptionId(immersionSession.selectedOptionId) : normalizeOptionId(optionId);
  const correctOptionRaw = await getBestCorrectionOptionId(q);
  const correctOption = correctOptionRaw ? normalizeOptionId(correctOptionRaw) : "";
  const hasKnownCorrect = Boolean(correctOption);
  const isCorrect = hasKnownCorrect && normalizedSelected === correctOption;

  if (!previouslyAnswered) {
    if (isCorrect) immersionSession.correctCount += 1;
    else if (hasKnownCorrect) {
      immersionSession.wrongCount += 1;
      immersionSession.wrongQuestions.push({ question: q, selectedOption: optionId, correctOption: correctOption });
    } else {
      immersionSession.wrongQuestions.push({ question: q, selectedOption: optionId, correctOption: null });
    }
    updateWrongAnswersUI();
  }

  await renderAnswerFeedback(q, { normalizedSelected, isShowSolution, previouslyAnswered, isCorrect, hasKnownCorrect, correctOption });

  if (hasKnownCorrect && !previouslyAnswered) {
    await submitImmersionAttempt(q.id, isCorrect, {
      simulation_mode: "immersion",
      selected_option: optionId,
      selected_option_normalized: normalizedSelected,
      auto_evaluated: true
    });
  }
}

async function renderAnswerFeedback(q, { normalizedSelected, isShowSolution, previouslyAnswered, isCorrect, hasKnownCorrect, correctOption }) {
  let explanation = "";
  if (q.solution) {
    explanation = q.solution.explanation || q.solution.comment || q.solution.reasoning || q.solution.spiegazione || "";
  }
  if (!explanation) {
    try {
      const correction = await loadQuestionCorrection(q.id);
      if (correction && correction.explanation_text) {
        explanation = correction.explanation_text;
      }
    } catch (e) {}
  }

  const feedback = el("immersion-feedback");
  const feedbackTitle = el("immersion-feedback-title");
  const feedbackBody = el("immersion-feedback-body");
  if (feedback) {
    feedback.classList.remove("hidden", "error");
    if (!hasKnownCorrect || (!isCorrect && !isShowSolution) || (!previouslyAnswered && isShowSolution)) {
      feedback.classList.add("error");
    }
  }

  const feedbackIcon = feedback ? feedback.querySelector(".immersion-feedback-icon") : null;
  if (feedbackIcon) {
    if (!previouslyAnswered && isShowSolution) feedbackIcon.textContent = "✗";
    else feedbackIcon.textContent = isCorrect ? "✓" : "✗";
  }

  if (feedbackTitle) {
    if (!hasKnownCorrect) {
      feedbackTitle.textContent = "Correzione non disponibile";
    } else if ((!previouslyAnswered || !immersionSession.selectedOptionId) && isShowSolution) {
      feedbackTitle.textContent = "Soluzione mostrata";
    } else if (isCorrect) {
      feedbackTitle.textContent = "Risposta corretta!";
    } else {
      feedbackTitle.textContent = "Risposta sbagliata";
    }
  }

  if (feedbackBody) {
    let msg = "";
    if (!hasKnownCorrect) {
      msg = "Per questa domanda non è presente una correzione salvata. Salvala dalla revisione classica per abilitare l'autovalutazione.";
    } else if ((!previouslyAnswered || !immersionSession.selectedOptionId) && isShowSolution) {
      msg = `La risposta corretta è la ${correctOption.toUpperCase()}.`;
    } else if (isCorrect) {
      msg = "Ottimo! Continua con la prossima domanda mantenendo il ritmo.";
    } else {
      msg = `Risposta attesa: ${correctOption.toUpperCase()}. Rileggi il prompt e prova la successiva.`;
    }

    if (explanation) {
      msg += `\n\n📝 Spiegazione:\n${explanation}`;
    }

    feedbackBody.textContent = msg;
    feedbackBody.style.whiteSpace = "pre-wrap";
  }

  const optionButtons = Array.from(document.querySelectorAll(".immersion-option[data-option-id]"));
  for (const btn of optionButtons) {
    const id = normalizeOptionId(btn.getAttribute("data-option-id") || "");
    btn.classList.add("is-locked");
    if (normalizedSelected && id === normalizedSelected) btn.classList.add("is-selected");
    if (hasKnownCorrect && id === correctOption) btn.classList.add("is-correct");
    if (hasKnownCorrect && id === normalizedSelected && !isCorrect) btn.classList.add("is-wrong");
  }
}

function renderSimulationQuestion() {
  const progress = el("simulation-progress");
  const progressText = el("immersion-progress-text");
  const progressBar = el("immersion-progress-bar");
  const tag = el("immersion-question-tag");
  const title = el("immersion-question-text");
  const optionsWrap = el("immersion-options");
  const score = el("immersion-score");
  const sessionTitle = el("immersion-session-title");
  const legacyBox = el("simulation-question");

  if (!immersionSession.questions.length) {
    renderImmersionBaseState();
    if (legacyBox) legacyBox.innerHTML = "<p class='muted'>Nessuna simulazione generata.</p>";
    return;
  }

  const q = immersionSession.questions[immersionSession.index];
  const total = immersionSession.questions.length;
  const indexHuman = immersionSession.index + 1;
  const ratio = total > 0 ? Math.round((indexHuman / total) * 100) : 0;
  if (progress) progress.textContent = `Domanda ${indexHuman} / ${total}`;
  if (progressText) progressText.textContent = `${indexHuman}/${total}`;
  if (progressBar) progressBar.style.width = `${ratio}%`;
  if (score) {
    score.textContent = `Punteggio: ${immersionSession.correctCount} corrette / ${immersionSession.wrongCount} sbagliate`;
  }
  if (sessionTitle) sessionTitle.textContent = "Full Immersion Simulation";
  if (tag) {
    const tagList = Array.isArray(q.tags) ? q.tags : [];
    const tagPills = tagList.length ? tagList.map(t => `<span class="pill">${t}</span>`).join(" ") : "";
    tag.innerHTML = `${q.question_type} · ${q.section} ${q.number_in_section}${tagPills ? " " + tagPills : ""}`;
  }
  const flagMenu = el("immersion-question-flag");
  if (flagMenu) {
    flagMenu.innerHTML = flagMenuMarkup(q.id);
    const flagSelect = flagMenu.querySelector("select");
    if (flagSelect) {
      flagSelect.addEventListener("change", async () => {
        if (flagSelect.value === "discard") {
          await flagQuestionAndRemove(q.id);
        } else if (flagSelect.value === "edit-correct") {
          await editCorrectAnswerInline(q.id);
        } else if (flagSelect.value === "regen-ai") {
          await regenerateCorrectAnswerInline(q.id);
        }
        flagSelect.value = "";
      });
    }
  }
  if (title) title.textContent = String(q.stem || "");

  const feedback = el("immersion-feedback");
  if (feedback) {
    if (!immersionSession.answered) {
      feedback.classList.remove("error");
      feedback.classList.add("hidden");
    }
  }

  if (q.question_type === "multiple_choice" && Array.isArray(q.options) && q.options.length) {
    optionsWrap.innerHTML = q.options
      .map((o) => {
        const optionId = String(o?.id || "").trim();
        const optionText = String(o?.text || "").trim();
        const label = optionId || "?";
        return `
          <button type="button" class="immersion-option" data-option-id="${optionId}">
            <span class="immersion-option-letter">${label}</span>
            <span class="immersion-option-body">
              <strong>${optionText || "Opzione"}</strong>
              <span>${optionId ? `Scelta ${optionId}` : "Seleziona questa opzione"}</span>
            </span>
          </button>
        `;
      })
      .join("") + `
        <div style="grid-column: 1 / -1; margin-top: 6px;">
          <button type="button" class="secondary" id="immersion-show-mcq-solution" style="width: 100%; justify-content: center;">
            📖 Non lo so / Mostra soluzione
          </button>
        </div>
      `;
    Array.from(optionsWrap.querySelectorAll(".immersion-option[data-option-id]")).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-option-id") || "";
        await handleImmersionOptionClick(id);
      });
    });
    const showSolutionBtn = el("immersion-show-mcq-solution");
    if (showSolutionBtn) {
      showSolutionBtn.addEventListener("click", async () => {
        await handleImmersionOptionClick("");
      });
    }
  } else {
    const subparts = Array.isArray(q.subparts) ? q.subparts : [];
    const subpartsMarkup = subparts.length
      ? `<ul>${subparts.map((s) => `<li>${s.id || ""}) ${s.prompt || ""}</li>`).join("")}</ul>`
      : "";
    optionsWrap.innerHTML = `
      <div class="immersion-option is-selected is-locked" role="group" aria-label="Domanda aperta">
        <span class="immersion-option-letter">✎</span>
        <span class="immersion-option-body">
          <strong>Risposta aperta</strong>
          <span>Rispondi mentalmente e poi controlla la correzione.</span>
          ${subpartsMarkup}
        </span>
      </div>
      <button type="button" class="immersion-option" id="immersion-show-correction">
        <span class="immersion-option-letter">📖</span>
        <span class="immersion-option-body">
          <strong>Mostra correzione</strong>
          <span>Fai clic per visualizzare la spiegazione e/o la risposta corretta.</span>
        </span>
      </button>
    `;
    const showBtn = el("immersion-show-correction");
    if (showBtn) {
      showBtn.addEventListener("click", async () => {
        const feedbackEl = el("immersion-feedback");
        const feedbackTitle = el("immersion-feedback-title");
        const feedbackBody = el("immersion-feedback-body");
        const feedbackIcon = feedbackEl ? feedbackEl.querySelector(".immersion-feedback-icon") : null;
        try {
          const correction = await loadQuestionCorrection(q.id);
          if (feedbackEl) feedbackEl.classList.remove("hidden", "error");
          if (feedbackIcon) feedbackIcon.textContent = "📖";
          if (correction && correction.has_correction) {
            const parts = [];
            if (correction.correct_option_id) parts.push(`Opzione corretta: ${correction.correct_option_id}`);
            if (correction.explanation_text) parts.push(correction.explanation_text);
            if (feedbackTitle) feedbackTitle.textContent = "Correzione salvata";
            if (feedbackBody) feedbackBody.textContent = parts.length ? parts.join(" — ") : "Presente ma senza dettagli.";
          } else {
            if (feedbackEl) feedbackEl.classList.add("error");
            if (feedbackIcon) feedbackIcon.textContent = "✗";
            if (feedbackTitle) feedbackTitle.textContent = "Nessuna correzione";
            if (feedbackBody) feedbackBody.textContent = "Nessuna correzione salvata per questa domanda. Usa la revisione classica per inserirla.";
          }
        } catch {
          if (feedbackEl) { feedbackEl.classList.remove("hidden"); feedbackEl.classList.add("error"); }
          if (feedbackIcon) feedbackIcon.textContent = "✗";
          if (feedbackTitle) feedbackTitle.textContent = "Errore";
          if (feedbackBody) feedbackBody.textContent = "Impossibile caricare la correzione.";
        }
        showBtn.disabled = true;
        showBtn.classList.add("is-locked");
      });
    }
  }

  if (legacyBox) {
    const opts = (q.options || []).map((o) => `<li><strong>${o.id}</strong> ${o.text}</li>`).join("");
    const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
    legacyBox.innerHTML = `
      <div class="question-header">
        <strong>${q.section} ${q.number_in_section}</strong>
        <span class="pill">${q.question_type}</span>
      </div>
      <div class="question-stem">${q.stem}</div>
      ${opts ? `<ul>${opts}</ul>` : ""}
      ${parts ? `<ul>${parts}</ul>` : ""}
    `;
  }
}

function readCountInput(id) {
  const raw = (el(id).value || "0").trim();
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function getMultiSelectValues(id) {
  const select = el(id);
  if (!select) return [];
  return Array.from(select.selectedOptions).map((o) => o.value).filter(Boolean);
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
      document_ids: getMultiSelectValues("sim-document-ids"),
      tag_presets: getMultiSelectValues("sim-tag-presets"),
      priority_mode: el("sim-priority-mode")?.value || "none",
      randomize: Boolean(el("sim-randomize")?.checked),
      user_id: userId,
      only_reviewed_correct: Boolean(el("sim-only-reviewed-correct")?.checked)
    };
    if (
      payload.multiple_choice_count +
        payload.open_text_count +
        payload.multi_part_open_count <=
      0
    ) {
      setInlineStatus(status, "Inserisci almeno 1 domanda.", true);
      return;
    }
    setInlineStatus(status, "Generazione simulazione...");
    const result = await api("/simulations/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    customSimulation.questions = result.questions || [];
    customSimulation.index = 0;
    customSimulation.simulationId = result.simulation_id || null;
    immersionSession.questions = customSimulation.questions;
    immersionSession.index = 0;
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
    immersionSession.correctCount = 0;
    immersionSession.wrongCount = 0;
    immersionSession.wrongQuestions = [];
    simulationCorrectionCache.clear();
    updateWrongAnswersUI();

    if (!customSimulation.questions.length) {
      setInlineStatus(status, "Nessuna domanda disponibile con i filtri scelti.");
      renderSimulationQuestion();
      return;
    }
    const shortage = result.shortage_by_type || {};
    const shortageParts = Object.keys(shortage).map((k) => `${k}: -${shortage[k]}`);
    setInlineStatus(
      status,
      shortageParts.length
        ? `Generate ${result.generated_total} domande (disponibilità ridotta: ${shortageParts.join(", ")}).`
        : `Generate ${result.generated_total} domande.`
    );
    renderSimulationQuestion();
    updateSimulationUrl();
    // Auto-switch to simulation tab
    if (typeof switchStudyTab === "function") switchStudyTab("sim");
  } catch (err) {
    setInlineStatus(status, err.message, true);
  }
}

async function createSimulationFromWrong() {
  const status = el("wrong-create-sim-status");
  const showStatus = (msg, isError = false) => {
    if (!status) return;
    status.textContent = msg;
    status.classList.remove("hidden");
    status.classList.toggle("error", isError);
  };

  // Snapshot the ids before loading the new simulation resets wrongQuestions.
  const questionIds = immersionSession.wrongQuestions
    .map((w) => w.question && w.question.id)
    .filter(Boolean);
  if (!questionIds.length) {
    showStatus("Nessun errore da cui creare una simulazione.", true);
    return;
  }

  let result;
  try {
    showStatus("Creazione simulazione dagli errori...");
    const userId = await ensureDefaultUser();
    result = await api("/simulations/from-questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, question_ids: questionIds, randomize: false })
    });
  } catch (err) {
    showStatus(err.message, true);
    return;
  }

  customSimulation.questions = result.questions || [];
  customSimulation.index = 0;
  customSimulation.simulationId = result.simulation_id || null;
  immersionSession.questions = customSimulation.questions;
  immersionSession.index = 0;
  immersionSession.answered = false;
  immersionSession.selectedOptionId = "";
  immersionSession.correctCount = 0;
  immersionSession.wrongCount = 0;
  immersionSession.wrongQuestions = [];
  simulationCorrectionCache.clear();
  updateWrongAnswersUI();

  if (!customSimulation.questions.length) {
    showStatus("Nessuna delle domande errate è più disponibile.", true);
    renderSimulationQuestion();
    return;
  }

  showStatus("");
  if (status) status.classList.add("hidden");
  renderSimulationQuestion();
  updateSimulationUrl();
  if (typeof switchStudyTab === "function") switchStudyTab("sim");
}

async function generateExhaustiveSimulation() {
  const status = el("simulation-status");
  try {
    const userId = await ensureDefaultUser();
    const payload = {
      multiple_choice_count: 0,
      open_text_count: 0,
      multi_part_open_count: 0,
      tag: el("sim-tag").value.trim() || null,
      document_ids: getMultiSelectValues("sim-document-ids"),
      tag_presets: getMultiSelectValues("sim-tag-presets"),
      priority_mode: el("sim-priority-mode")?.value || "none",
      randomize: Boolean(el("sim-randomize")?.checked),
      user_id: userId,
      only_reviewed_correct: Boolean(el("sim-only-reviewed-correct")?.checked),
      exhaustive: true
    };
    setInlineStatus(status, "Generazione simulazione esaustiva...");
    const result = await api("/simulations/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    customSimulation.questions = result.questions || [];
    customSimulation.index = 0;
    customSimulation.simulationId = result.simulation_id || null;
    immersionSession.questions = customSimulation.questions;
    immersionSession.index = 0;
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
    immersionSession.correctCount = 0;
    immersionSession.wrongCount = 0;
    immersionSession.wrongQuestions = [];
    simulationCorrectionCache.clear();
    updateWrongAnswersUI();

    if (!customSimulation.questions.length) {
      setInlineStatus(status, "Nessuna domanda disponibile con i filtri scelti.");
      renderSimulationQuestion();
      return;
    }
    setInlineStatus(status, `Simulazione esaustiva: ${result.generated_total} domande caricate.`);
    renderSimulationQuestion();
    updateSimulationUrl();
    if (typeof switchStudyTab === "function") switchStudyTab("sim");
  } catch (err) {
    setInlineStatus(status, err.message, true);
  }
}

function updateWrongAnswersUI() {
  const wrongBtn = el("tab-btn-wrong");
  const badge = el("wrong-count-badge");
  const list = el("wrong-answers-list");
  const emptyState = el("wrong-answers-empty");
  const createSimBtn = el("wrong-create-sim");
  const count = immersionSession.wrongQuestions.length;

  if (wrongBtn) {
    if (count > 0) wrongBtn.classList.remove("hidden");
    else wrongBtn.classList.add("hidden");
  }
  if (createSimBtn) createSimBtn.classList.toggle("hidden", count === 0);
  if (badge) badge.textContent = String(count);
  if (!list) return;

  if (count === 0) {
    list.innerHTML = "";
    if (emptyState) emptyState.classList.remove("hidden");
    return;
  }
  if (emptyState) emptyState.classList.add("hidden");

  list.innerHTML = immersionSession.wrongQuestions.map((entry, idx) => {
    const q = entry.question;
    const tags = (q.tags || []).map(t => `<span class="pill">${t}</span>`).join(" ");
    const correctLabel = entry.correctOption ? `Corretta: <strong>${entry.correctOption.toUpperCase()}</strong>` : "<em>Correzione non disponibile</em>";
    const selectedLabel = entry.selectedOption ? `Selezionata: <strong>${entry.selectedOption.toUpperCase()}</strong>` : "";
    const optionsList = (q.options || []).map(o => {
      const normId = normalizeOptionId(o.id);
      let cls = "";
      if (entry.correctOption && normId === entry.correctOption) cls = "ok";
      else if (entry.selectedOption && normId === normalizeOptionId(entry.selectedOption)) cls = "warn";
      return `<div class="pill ${cls}"><strong>${o.id}</strong> ${o.text}</div>`;
    }).join(" ");
    return `
      <div class="question-item reveal-item" style="animation-delay:${idx * 40}ms;">
        <div class="question-header">
          <strong>${q.section} ${q.number_in_section}</strong>
          <span class="pill">${q.question_type}</span>
          ${tags}
          ${flagTrashButtonMarkup(q.id)}
        </div>
        <div class="question-stem">${q.stem}</div>
        <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap;">${optionsList}</div>
        <div style="margin-top:10px;" class="muted small">${selectedLabel}${selectedLabel && correctLabel ? " · " : ""}${correctLabel}</div>
      </div>
    `;
  }).join("");

  Array.from(list.querySelectorAll("[data-flag-question]")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      await flagQuestionAndRemove(btn.getAttribute("data-flag-question"));
    });
  });
}

function formatSimulationConfigSummary(sim) {
  const cfg = sim.config || {};
  const parts = [];
  if (cfg.document_ids && cfg.document_ids.length) parts.push(`${cfg.document_ids.length} documenti`);
  if (cfg.tag_presets && cfg.tag_presets.length) parts.push(`${cfg.tag_presets.length} preset`);
  if (sim.exhaustive) {
    return ["Esaustiva", ...parts].join(" · ");
  }
  const counts = [];
  if (cfg.multiple_choice_count) counts.push(`${cfg.multiple_choice_count} MC`);
  if (cfg.open_text_count) counts.push(`${cfg.open_text_count} aperte`);
  if (cfg.multi_part_open_count) counts.push(`${cfg.multi_part_open_count} multi`);
  return [counts.join(" + ") || "Simulazione", ...parts].join(" · ");
}

async function loadSimulationHistory() {
  const list = el("simulation-history-list");
  const emptyState = el("simulation-history-empty");
  const reviewBox = el("simulation-review");
  if (!list) return;
  try {
    const userId = await ensureDefaultUser();
    const sims = await api(`/simulations?user_id=${userId}&limit=20`);
    if (reviewBox) reviewBox.innerHTML = "";
    if (!sims.length) {
      list.innerHTML = "";
      if (emptyState) emptyState.classList.remove("hidden");
      return;
    }
    if (emptyState) emptyState.classList.add("hidden");
    list.innerHTML = sims.map((sim) => {
      const date = new Date(sim.created_at).toLocaleString("it-IT");
      const summary = formatSimulationConfigSummary(sim);
      const score = `${sim.correct_count}/${sim.answered_count} corrette su ${sim.generated_total} domande`;
      return `
        <div class="question-item">
          <div class="question-header">
            <strong>${date}</strong>
            <span class="pill">${summary}</span>
          </div>
          <div class="muted small">${score}</div>
          <div class="row" style="margin-top:8px;">
            <button type="button" class="secondary" data-review-simulation="${sim.id}">Rivedi</button>
            <button type="button" class="btn-icon danger-ghost" data-delete-simulation="${sim.id}" title="Elimina simulazione" aria-label="Elimina simulazione">🗑️</button>
          </div>
        </div>
      `;
    }).join("");
    Array.from(list.querySelectorAll("[data-review-simulation]")).forEach((btn) => {
      btn.addEventListener("click", () => reviewSimulation(btn.getAttribute("data-review-simulation")));
    });
    Array.from(list.querySelectorAll("[data-delete-simulation]")).forEach((btn) => {
      btn.addEventListener("click", () => deleteSimulationFromHistory(btn.getAttribute("data-delete-simulation")));
    });
  } catch (err) {
    list.innerHTML = `<p class="muted small">${err.message}</p>`;
  }
}

async function reviewSimulation(simulationId) {
  const reviewBox = el("simulation-review");
  if (!reviewBox) return;
  reviewBox.innerHTML = `<p class="muted small">Caricamento...</p>`;
  try {
    const sim = await api(`/simulations/${simulationId}`);
    const items = (sim.questions || []).map((q, idx) => {
      const tags = (q.tags || []).map((t) => `<span class="pill">${t}</span>`).join(" ");
      const attempt = q.attempt;
      const correctOptionId = effectiveCorrectOptionId(q.solution, null);
      const correctNorm = correctOptionId ? normalizeOptionId(correctOptionId) : "";
      const selectedRaw = attempt?.answer_payload?.selected_option;
      const selectedNorm = selectedRaw ? normalizeOptionId(selectedRaw) : "";
      let answerInfo = "<em>Non risposta</em>";
      if (attempt) {
        const selectedLabel = selectedRaw ? `Risposta data: <strong>${String(selectedRaw).toUpperCase()}</strong>` : "";
        const resultLabel = attempt.is_correct ? "✓ Corretta" : "✗ Sbagliata";
        answerInfo = `${selectedLabel ? selectedLabel + " · " : ""}${resultLabel}`;
      }
      const optionsList = (q.options || []).map((o) => {
        const normId = normalizeOptionId(o.id);
        let cls = "";
        if (correctNorm && normId === correctNorm) cls = "ok";
        else if (selectedNorm && normId === selectedNorm && attempt && !attempt.is_correct) cls = "warn";
        return `<div class="pill ${cls}"><strong>${o.id}</strong> ${o.text}</div>`;
      }).join(" ");
      return `
        <div class="question-item reveal-item" style="animation-delay:${idx * 30}ms;">
          <div class="question-header">
            <strong>${q.section} ${q.number_in_section}</strong>
            <span class="pill">${q.question_type}</span>
            ${tags}
          </div>
          <div class="question-stem">${q.stem}</div>
          ${optionsList ? `<div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap;">${optionsList}</div>` : ""}
          <div class="muted small" style="margin-top:10px;">${answerInfo}</div>
        </div>
      `;
    }).join("");
    reviewBox.innerHTML = `<h3>Domande della simulazione</h3>${items}`;
  } catch (err) {
    reviewBox.innerHTML = `<p class="muted small">${err.message}</p>`;
  }
}

async function deleteSimulationFromHistory(simulationId) {
  const ok = window.confirm("Eliminare definitivamente questa simulazione dalla cronologia? L'azione non può essere annullata.");
  if (!ok) return;
  try {
    await api(`/simulations/${simulationId}`, { method: "DELETE" });
  } catch (err) {
    alert(err.message);
    return;
  }

  const reviewBox = el("simulation-review");
  if (reviewBox) reviewBox.innerHTML = "";

  if (customSimulation.simulationId === simulationId) {
    customSimulation.simulationId = null;
    updateSimulationUrl();
  }

  await loadSimulationHistory();
}

function flagMenuMarkup(questionId) {
  return `
    <select class="flag-menu" data-flag-question="${questionId}" title="Azioni domanda" aria-label="Azioni domanda">
      <option value="" selected disabled>⋯</option>
      <option value="edit-correct">✏️ Modifica risposta corretta</option>
      <option value="regen-ai">🤖 Rigenera risposta (AI)</option>
      <option value="discard">🚩 Segnala / rimuovi domanda</option>
    </select>
  `;
}

function flagTrashButtonMarkup(questionId) {
  return `<button type="button" class="btn-icon danger-ghost flag-trash-btn" data-flag-question="${questionId}" title="Rimuovi domanda" aria-label="Rimuovi domanda">🗑️</button>`;
}

async function editCorrectAnswerInline(questionId) {
  const q = immersionSession.questions.find((item) => item.id === questionId);
  if (!q) return;
  if (q.question_type !== "multiple_choice" || !Array.isArray(q.options) || !q.options.length) {
    alert("La modifica della risposta corretta è disponibile solo per le domande a scelta multipla.");
    return;
  }

  const optionsById = new Map();
  for (const o of q.options) {
    const normId = normalizeOptionId(o?.id);
    if (normId) optionsById.set(normId, String(o?.id || "").trim());
  }
  const optionList = q.options
    .map((o) => `${o?.id}) ${String(o?.text || "").trim()}`)
    .join("\n");

  const current = await getBestCorrectionOptionId(q);
  const answer = window.prompt(
    `Quale opzione è quella corretta?\n\n${optionList}\n\nInserisci la lettera dell'opzione corretta.`,
    current ? current.toUpperCase() : ""
  );
  if (answer === null) return;

  const normalized = normalizeOptionId(answer);
  if (!normalized || !optionsById.has(normalized)) {
    alert("Opzione non valida. Scegli una delle lettere disponibili.");
    return;
  }
  const rawOptionId = optionsById.get(normalized) || normalized;

  try {
    await setQuestionCorrection(q.id, {
      correct_option_id: rawOptionId,
      explanation_text: null,
      answer_payload: {
        selected_option_raw: rawOptionId,
        selected_option_normalized: normalized
      }
    });
  } catch (err) {
    alert(err.message);
    return;
  }

  // Make the new key take effect immediately and survive a refresh.
  simulationCorrectionCache.set(q.id, normalized);
  const wrongEntry = immersionSession.wrongQuestions.find((w) => w.question.id === q.id);
  if (wrongEntry) wrongEntry.correctOption = normalized;

  // The current attempt was scored against the old key; reset so the user can
  // re-answer against the corrected option (we don't retroactively rescore).
  if (immersionSession.questions[immersionSession.index]?.id === q.id) {
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
  }

  updateWrongAnswersUI();
  renderSimulationQuestion();
}

async function regenerateCorrectAnswerInline(questionId) {
  const q = immersionSession.questions.find((item) => item.id === questionId);
  if (!q) return;
  const ok = window.confirm("Rigenerare con AI la risposta corretta? Sovrascriverà la correzione attuale.");
  if (!ok) return;

  const feedback = el("immersion-feedback");
  const feedbackTitle = el("immersion-feedback-title");
  const feedbackBody = el("immersion-feedback-body");
  const feedbackIcon = feedback ? feedback.querySelector(".immersion-feedback-icon") : null;
  if (feedback) feedback.classList.remove("hidden", "error");
  if (feedbackIcon) feedbackIcon.textContent = "🤖";
  if (feedbackTitle) feedbackTitle.textContent = "Rigenerazione AI in corso…";
  if (feedbackBody) {
    feedbackBody.textContent = "Attendi qualche secondo.";
    feedbackBody.style.whiteSpace = "pre-wrap";
  }

  let result;
  try {
    const userId = await ensureDefaultUser();
    result = await api(`/questions/${questionId}/correction/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId })
    });
  } catch (err) {
    alert(err.message);
    renderSimulationQuestion();
    return;
  }

  // The new correction wins over the embedded solution; refresh cache + wrong list.
  const normalized = normalizeOptionId(result.correct_option_id || "");
  simulationCorrectionCache.set(q.id, normalized);
  const wrongEntry = immersionSession.wrongQuestions.find((w) => w.question.id === q.id);
  if (wrongEntry) wrongEntry.correctOption = normalized || null;

  // Reset the current question so the new key applies on the next answer.
  if (immersionSession.questions[immersionSession.index]?.id === q.id) {
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
  }

  updateWrongAnswersUI();
  renderSimulationQuestion();
}

async function flagQuestionAndRemove(questionId) {
  const ok = window.confirm("Segnalare questa domanda? Verrà rimossa da questa sessione e non apparirà più in simulazioni o errori.");
  if (!ok) return;
  try {
    await discardQuestion(questionId, true);
  } catch (err) {
    alert(err.message);
    return;
  }

  const idx = immersionSession.questions.findIndex((q) => q.id === questionId);
  if (idx !== -1) {
    immersionSession.questions.splice(idx, 1);
    if (idx < immersionSession.index) immersionSession.index -= 1;
    const total = immersionSession.questions.length;
    immersionSession.index = total ? Math.min(immersionSession.index, total - 1) : 0;
    customSimulation.index = immersionSession.index;
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
  }

  immersionSession.wrongQuestions = immersionSession.wrongQuestions.filter((w) => w.question.id !== questionId);

  updateWrongAnswersUI();
  renderSimulationQuestion();
  updateSimulationUrl();
}

function updateSimulationUrl() {
  const url = new URL(window.location.href);
  if (customSimulation.simulationId && immersionSession.questions.length) {
    url.searchParams.set("sim", customSimulation.simulationId);
    url.searchParams.set("question", String(immersionSession.index + 1));
  } else {
    url.searchParams.delete("sim");
    url.searchParams.delete("question");
  }
  window.history.replaceState(null, "", url);
}

async function restoreSimulationFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const simId = params.get("sim");
  if (!simId) return false;

  try {
    const sim = await api(`/simulations/${simId}`);
    customSimulation.questions = sim.questions || [];
    customSimulation.index = 0;
    customSimulation.simulationId = sim.id;
    immersionSession.questions = customSimulation.questions;
    immersionSession.index = 0;
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
    immersionSession.correctCount = 0;
    immersionSession.wrongCount = 0;
    immersionSession.wrongQuestions = [];
    simulationCorrectionCache.clear();

    for (const q of immersionSession.questions) {
      const attempt = q.attempt;
      if (!attempt) continue;
      if (attempt.is_correct) {
        immersionSession.correctCount += 1;
      } else {
        immersionSession.wrongCount += 1;
        const correctOptionId = await getBestCorrectionOptionId(q);
        immersionSession.wrongQuestions.push({
          question: q,
          selectedOption: attempt.answer_payload?.selected_option || null,
          correctOption: correctOptionId ? normalizeOptionId(correctOptionId) : null
        });
      }
    }
    updateWrongAnswersUI();

    const total = immersionSession.questions.length;
    if (!total) {
      updateSimulationUrl();
      return false;
    }

    let questionParam = Number.parseInt(params.get("question") || "1", 10);
    if (!Number.isFinite(questionParam) || questionParam < 1) questionParam = 1;
    const index = Math.min(Math.max(questionParam - 1, 0), total - 1);
    immersionSession.index = index;
    customSimulation.index = index;

    renderSimulationQuestion();

    const currentAttempt = immersionSession.questions[index]?.attempt;
    if (currentAttempt) {
      const q = immersionSession.questions[index];
      immersionSession.answered = true;
      immersionSession.selectedOptionId = currentAttempt.answer_payload?.selected_option || "";
      if (q.question_type === "multiple_choice") {
        const normalizedSelected = normalizeOptionId(immersionSession.selectedOptionId);
        const correctOptionRaw = await getBestCorrectionOptionId(q);
        const correctOption = correctOptionRaw ? normalizeOptionId(correctOptionRaw) : "";
        const hasKnownCorrect = Boolean(correctOption);
        await renderAnswerFeedback(q, {
          normalizedSelected,
          isShowSolution: false,
          previouslyAnswered: true,
          isCorrect: currentAttempt.is_correct,
          hasKnownCorrect,
          correctOption
        });
      }
    }

    if (typeof switchStudyTab === "function") switchStudyTab("sim");
    updateSimulationUrl();
    return true;
  } catch (err) {
    const url = new URL(window.location.href);
    url.searchParams.delete("sim");
    url.searchParams.delete("question");
    window.history.replaceState(null, "", url);
    return false;
  }
}

function simulationNext() {
  if (!immersionSession.questions.length) return;
  immersionSession.index = Math.min(immersionSession.index + 1, immersionSession.questions.length - 1);
  customSimulation.index = immersionSession.index;
  immersionSession.answered = false;
  immersionSession.selectedOptionId = "";
  renderSimulationQuestion();
  updateSimulationUrl();
}

function simulationPrev() {
  if (!immersionSession.questions.length) return;
  immersionSession.index = Math.max(immersionSession.index - 1, 0);
  customSimulation.index = immersionSession.index;
  immersionSession.answered = false;
  immersionSession.selectedOptionId = "";
  renderSimulationQuestion();
  updateSimulationUrl();
}

function initStudyAccordion() {
  const cards = Array.from(document.querySelectorAll("[data-collapse-card]"));
  const toggles = Array.from(document.querySelectorAll(".collapse-toggle[data-collapse-target]"));
  if (!cards.length || !toggles.length) return;

  const setOpenCard = (targetId) => {
    for (const card of cards) {
      const toggle = card.querySelector(".collapse-toggle[data-collapse-target]");
      const panel = toggle ? document.getElementById(toggle.getAttribute("data-collapse-target") || "") : null;
      const open = Boolean(panel && panel.id === targetId);
      card.classList.toggle("is-open", open);
      if (toggle) {
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.textContent = open ? "Comprimi" : "Apri";
      }
    }
  };

  for (const toggle of toggles) {
    toggle.addEventListener("click", () => {
      const panelId = toggle.getAttribute("data-collapse-target") || "";
      const isOpen = toggle.getAttribute("aria-expanded") === "true";
      if (isOpen) {
        // Keep one panel visible at all times: ignore collapse if already open.
        return;
      }
      setOpenCard(panelId);
    });
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  initStudyAccordion();
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
  el("generate-exhaustive").addEventListener("click", generateExhaustiveSimulation);
  el("wrong-create-sim")?.addEventListener("click", createSimulationFromWrong);
  el("immersion-next")?.addEventListener("click", simulationNext);
  el("immersion-finish")?.addEventListener("click", () => {
    if (immersionSession.wrongQuestions.length > 0) {
      // Show review tab before clearing
      updateWrongAnswersUI();
      if (typeof switchStudyTab === "function") switchStudyTab("wrong");
    }
    immersionSession.questions = [];
    immersionSession.index = 0;
    immersionSession.answered = false;
    immersionSession.selectedOptionId = "";
    immersionSession.correctCount = 0;
    immersionSession.wrongCount = 0;
    // Keep wrongQuestions for review — only clear on new simulation
    customSimulation.questions = [];
    customSimulation.index = 0;
    customSimulation.simulationId = null;
    renderSimulationQuestion();
    updateSimulationUrl();
    setInlineStatus(el("simulation-status"), "Sessione immersione chiusa.");
  });
  el("simulation-next").addEventListener("click", simulationNext);
  el("simulation-prev").addEventListener("click", simulationPrev);
  await ensureDefaultUser();
  await refreshDocuments();
  await refreshTagPresets();
  updateStudyCounter();
  updateShuffleNewButton();
  updateStudyNavButtons();
  renderStudyQuestion(null);
  const restored = await restoreSimulationFromUrl();
  if (!restored) {
    renderSimulationQuestion();
  }
  await refreshReviewStats();
  await loadNextStudyQuestion(false);
});
