const el = (id) => document.getElementById(id);
let defaultUserId = null;
let currentStudyQuestion = null;
const studyFilters = { documentId: "", tag: "" };
const studySession = { correct: 0, wrong: 0 };
let cachedDocuments = [];

function setResult(target, value, isError = false) {
  target.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  target.classList.toggle("error", isError);
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

function updateStudyCounter() {
  el("study-counter").textContent = `Sessione: ${studySession.correct} corrette / ${studySession.wrong} sbagliate`;
}

async function uploadFile(ev) {
  ev.preventDefault();
  const fileInput = el("pdf-file");
  const out = el("upload-result");
  if (!fileInput.files.length) {
    setResult(out, "Seleziona un PDF", true);
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  try {
    setResult(out, "Upload in corso...");
    const result = await api("/documents", { method: "POST", body: form });
    el("doc-id").value = result.document_id;
    setResult(out, result);
    await refreshDocuments();
  } catch (err) {
    setResult(out, err.message, true);
  }
}

async function processDocument() {
  const id = el("doc-id").value.trim();
  const out = el("process-result");
  if (!id) {
    setResult(out, "Inserisci document_id", true);
    return;
  }
  try {
    setResult(out, "Processing...");
    const result = await api(`/documents/${id}/process`, { method: "POST" });
    setResult(out, result);
    await refreshDocuments();
    await loadQuestions(id);
  } catch (err) {
    setResult(out, err.message, true);
  }
}

function renderDocs(rows) {
  cachedDocuments = rows;
  const wrap = el("documents");
  if (!rows.length) {
    wrap.innerHTML = "<p class='muted'>Nessun documento.</p>";
    renderStudyDocumentFilter(rows);
    return;
  }
  const body = rows
    .map((r) => {
      const statusClass = r.ingestion_status === "processed" ? "ok" : "warn";
      return `<tr>
        <td><code>${r.id}</code></td>
        <td>${r.title || ""}</td>
        <td><span class="pill ${statusClass}">${r.ingestion_status}</span></td>
        <td>${r.pages ?? "-"}</td>
        <td><button data-doc="${r.id}" class="open-doc">Apri</button></td>
      </tr>`;
    })
    .join("");
  wrap.innerHTML = `<table>
    <thead><tr><th>ID</th><th>Titolo</th><th>Stato</th><th>Pagine</th><th></th></tr></thead>
    <tbody>${body}</tbody>
  </table>`;

  wrap.querySelectorAll(".open-doc").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-doc");
      el("doc-id").value = id;
      await loadQuestions(id);
    });
  });
  renderStudyDocumentFilter(rows);
}

function renderStudyDocumentFilter(rows) {
  const select = el("study-filter-document-id");
  const current = select.value;
  const options = rows
    .map((doc) => {
      const title = doc.title || "Documento senza titolo";
      return `<option value="${doc.id}">${title} (${doc.id.slice(0, 8)}...)</option>`;
    })
    .join("");
  select.innerHTML = `<option value="">Tutti i documenti</option>${options}`;
  if (rows.some((d) => d.id === current)) {
    select.value = current;
  } else {
    select.value = "";
  }
}

async function refreshDocuments() {
  const docs = await api("/documents?limit=200");
  renderDocs(docs);
}

function renderQuestions(items) {
  const target = el("questions");
  if (!items.length) {
    target.innerHTML = "<p class='muted'>Nessuna domanda estratta.</p>";
    return;
  }
  target.innerHTML = items
    .map((q) => {
      const opts = (q.options || [])
        .map((o) => `<li><strong>${o.id}</strong> ${o.text}</li>`)
        .join("");
      const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
      return `<div class="question-item">
        <div><strong>${q.section} ${q.number_in_section}</strong> - ${q.question_type} - conf ${q.confidence}</div>
        <div>${q.stem}</div>
        ${opts ? `<ul>${opts}</ul>` : ""}
        ${parts ? `<ul>${parts}</ul>` : ""}
        <div class="row question-actions">
          <button class="mark-correct" data-qid="${q.id}">Segna corretta</button>
          <button class="mark-wrong" data-qid="${q.id}">Segna sbagliata</button>
          <span id="status-${q.id}" class="muted small"></span>
        </div>
      </div>`;
    })
    .join("");

  target.querySelectorAll(".mark-correct").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await markAttempt(btn.getAttribute("data-qid"), true);
    });
  });
  target.querySelectorAll(".mark-wrong").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await markAttempt(btn.getAttribute("data-qid"), false);
    });
  });
}

async function markAttempt(questionId, isCorrect) {
  const status = el(`status-${questionId}`);
  try {
    status.textContent = "Salvataggio...";
    const userId = await ensureDefaultUser();
    await api("/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        question_id: questionId,
        is_correct: isCorrect,
        grade: isCorrect ? 4 : 1
      })
    });
    status.textContent = isCorrect ? "Registrata come corretta" : "Registrata come sbagliata";
    status.classList.remove("error");
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  }
}

async function loadQuestions(id) {
  try {
    const items = await api(`/documents/${id}/questions?limit=1000`);
    renderQuestions(items);
  } catch (err) {
    setResult(el("process-result"), err.message, true);
  }
}

async function loadSimulationReport() {
  const target = el("sim-report");
  try {
    const data = await api("/reports/simulation");
    const summary = {
      total_pdfs: data.total_pdfs,
      processed_ok: data.processed_ok,
      processed_failed: data.processed_failed,
      multimodal_used_count: data.multimodal_used_count,
      avg_extraction_quality: data.avg_extraction_quality
    };
    target.textContent = JSON.stringify(summary, null, 2);
  } catch (err) {
    target.textContent = err.message;
    target.classList.add("error");
  }
}

function renderStudyQuestion(q) {
  const box = el("study-question");
  if (!q) {
    box.innerHTML = "<p class='muted'>Nessuna domanda pronta.</p>";
    return;
  }
  const opts = (q.options || []).map((o) => `<li><strong>${o.id}</strong> ${o.text}</li>`).join("");
  const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
  const hasSolution = q.solution && Object.keys(q.solution).length > 0;
  const solutionMarkup = hasSolution
    ? `<pre class="pre">${JSON.stringify(q.solution, null, 2)}</pre>`
    : "<p class='muted'>Soluzione non disponibile.</p>";
  box.innerHTML = `
    <div><strong>${q.section} ${q.number_in_section}</strong> - ${q.question_type}</div>
    <div>${q.stem}</div>
    ${opts ? `<ul>${opts}</ul>` : ""}
    ${parts ? `<ul>${parts}</ul>` : ""}
    <div class="row question-actions">
      <button id="study-show-solution">Mostra soluzione</button>
      <button id="study-mark-correct" disabled>Segna corretta</button>
      <button id="study-mark-wrong" class="mark-wrong" disabled>Segna sbagliata</button>
      <span id="study-answer-status" class="muted small"></span>
    </div>
    <div id="study-solution" class="hidden">
      ${solutionMarkup}
    </div>
  `;

  el("study-show-solution").addEventListener("click", () => {
    el("study-solution").classList.remove("hidden");
    el("study-mark-correct").disabled = false;
    el("study-mark-wrong").disabled = false;
  });
  el("study-mark-correct").addEventListener("click", async () => {
    await submitStudyAttempt(true);
  });
  el("study-mark-wrong").addEventListener("click", async () => {
    await submitStudyAttempt(false);
  });
}

function buildStudyNextUrl(userId) {
  const query = new URLSearchParams();
  if (studyFilters.documentId) query.set("document_id", studyFilters.documentId);
  if (studyFilters.tag) query.set("tag", studyFilters.tag);
  const suffix = query.toString();
  return suffix ? `/study/next/${userId}?${suffix}` : `/study/next/${userId}`;
}

function applyStudyFilters() {
  studyFilters.documentId = el("study-filter-document-id").value.trim();
  studyFilters.tag = el("study-filter-tag").value.trim();
  currentStudyQuestion = null;
  renderStudyQuestion(null);
  const status = el("study-status");
  const active = [];
  if (studyFilters.documentId) {
    const doc = cachedDocuments.find((d) => d.id === studyFilters.documentId);
    const label = doc ? doc.title || doc.id : studyFilters.documentId;
    active.push(`documento=${label}`);
  }
  if (studyFilters.tag) active.push(`tag=${studyFilters.tag}`);
  status.textContent = active.length ? `Filtri attivi: ${active.join(" | ")}` : "Filtri rimossi";
  status.classList.remove("error");
}

async function loadNextStudyQuestion() {
  const status = el("study-status");
  try {
    status.textContent = "Caricamento...";
    const userId = await ensureDefaultUser();
    const next = await api(buildStudyNextUrl(userId));
    const q = await api(`/questions/${next.question_id}`);
    currentStudyQuestion = q;
    renderStudyQuestion(q);
    status.textContent = `Mostrata (${next.due_reason})`;
    status.classList.remove("error");
  } catch (err) {
    currentStudyQuestion = null;
    renderStudyQuestion(null);
    if ((err.message || "").includes("No questions available")) {
      const applied = [];
      if (studyFilters.documentId) {
        const doc = cachedDocuments.find((d) => d.id === studyFilters.documentId);
        applied.push(`documento=${doc ? doc.title || doc.id : studyFilters.documentId}`);
      }
      if (studyFilters.tag) applied.push(`tag=${studyFilters.tag}`);
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

async function submitStudyAttempt(isCorrect) {
  const status = el("study-answer-status");
  if (!currentStudyQuestion) return;
  try {
    status.textContent = "Salvataggio...";
    const userId = await ensureDefaultUser();
    await api("/attempts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        question_id: currentStudyQuestion.id,
        is_correct: isCorrect,
        grade: isCorrect ? 4 : 1
      })
    });
    status.textContent = isCorrect ? "Corretta registrata" : "Errore registrato";
    if (isCorrect) studySession.correct += 1;
    else studySession.wrong += 1;
    updateStudyCounter();
    await loadNextStudyQuestion();
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  el("upload-form").addEventListener("submit", uploadFile);
  el("process-btn").addEventListener("click", processDocument);
  el("refresh-docs").addEventListener("click", refreshDocuments);
  el("load-sim-report").addEventListener("click", loadSimulationReport);
  el("next-study-question").addEventListener("click", loadNextStudyQuestion);
  el("apply-study-filters").addEventListener("click", applyStudyFilters);
  el("reset-study-counter").addEventListener("click", () => {
    studySession.correct = 0;
    studySession.wrong = 0;
    updateStudyCounter();
  });
  await ensureDefaultUser();
  await refreshDocuments();
  updateStudyCounter();
  renderStudyQuestion(null);
});
