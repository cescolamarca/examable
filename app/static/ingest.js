const el = (id) => document.getElementById(id);
let selectedDocumentId = "";
let allQuestions = [];

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
    selectedDocumentId = result.document_id;
    setResult(out, result);
    await refreshDocuments();
    await loadKpis();
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
    selectedDocumentId = id;
    setResult(out, result);
    await refreshDocuments();
    await loadKpis();
    await loadQuestions();
  } catch (err) {
    setResult(out, err.message, true);
  }
}

function renderDocs(rows) {
  const wrap = el("documents");
  if (!rows.length) {
    wrap.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">📄</div>
        <h3>Nessun documento disponibile</h3>
        <p>Carica un PDF per iniziare il flusso di ingest.</p>
      </div>
    `;
    return;
  }
  const body = rows
    .map((r) => {
      const statusClass = r.ingestion_status === "processed" ? "ok" : "warn";
      const disabled = r.ingestion_status === "processed" ? "" : "disabled";
      return `<tr>
        <td><code>${r.id}</code></td>
        <td>${r.title || ""}</td>
        <td><span class="pill ${statusClass}">${r.ingestion_status}</span></td>
        <td>${r.pages ?? "-"}</td>
        <td><button data-doc="${r.id}" class="open-doc" ${disabled}>Apri domande</button></td>
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
      if (!id) return;
      selectedDocumentId = id;
      el("doc-id").value = id;
      await loadQuestions();
    });
  });
}

async function refreshDocuments() {
  const docs = await api("/documents?limit=200");
  renderDocs(docs);
}

function questionToSearchText(q) {
  const options = (q.options || []).map((o) => `${o.id} ${o.text}`).join(" ");
  const subparts = (q.subparts || []).map((s) => `${s.id} ${s.prompt}`).join(" ");
  const tags = (q.tags || []).join(" ");
  return `${q.stem || ""} ${options} ${subparts} ${tags}`.toLowerCase();
}

function applyQuestionFilters() {
  const type = el("questions-type-filter").value.trim();
  const search = el("questions-search").value.trim().toLowerCase();
  const filtered = allQuestions.filter((q) => {
    if (type && q.question_type !== type) return false;
    if (!search) return true;
    return questionToSearchText(q).includes(search);
  });
  renderQuestions(filtered);
}

function renderQuestions(items) {
  const target = el("questions");
  if (!items.length) {
    target.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">🔎</div>
        <h3>Nessuna domanda trovata</h3>
        <p>Prova a cambiare documento, tipo o testo di ricerca.</p>
      </div>
    `;
    return;
  }
  target.innerHTML = items
    .map((q, idx) => {
      const opts = (q.options || []).map((o) => `<li><strong>${o.id}</strong> ${o.text}</li>`).join("");
      const parts = (q.subparts || []).map((s) => `<li>${s.id}) ${s.prompt}</li>`).join("");
      const tags = (q.tags || []).map((t) => `<span class="pill">${t}</span>`).join(" ");
      return `<div class="question-item reveal-item" style="animation-delay:${Math.min(idx * 28, 320)}ms">
        <div><strong>${q.section} ${q.number_in_section}</strong> - ${q.question_type} - conf ${q.confidence}</div>
        <div>${q.stem}</div>
        ${tags ? `<div class="row">${tags}</div>` : ""}
        ${opts ? `<ul>${opts}</ul>` : ""}
        ${parts ? `<ul>${parts}</ul>` : ""}
      </div>`;
    })
    .join("");
}

async function loadQuestions() {
  const out = el("process-result");
  if (!selectedDocumentId) {
    allQuestions = [];
    renderQuestions(allQuestions);
    setResult(out, "Seleziona un documento dalla tabella e poi carica le domande.");
    return;
  }
  try {
    const query = new URLSearchParams();
    query.set("limit", "1000");
    allQuestions = await api(`/documents/${selectedDocumentId}/questions?${query.toString()}`);
    applyQuestionFilters();
  } catch (err) {
    setResult(out, err.message, true);
  }
}

async function loadKpis() {
  try {
    const kpi = await api("/stats/kpi");
    el("kpi-total-docs").textContent = `${kpi.total_documents ?? 0}`;
    el("kpi-processed-docs").textContent = `${kpi.processed_documents ?? 0}`;
    if (kpi.avg_quality === null || kpi.avg_quality === undefined) {
      el("kpi-avg-quality").textContent = "-";
    } else {
      el("kpi-avg-quality").textContent = `${(Number(kpi.avg_quality) * 100).toFixed(1)}%`;
    }
  } catch {
    el("kpi-total-docs").textContent = "-";
    el("kpi-processed-docs").textContent = "-";
    el("kpi-avg-quality").textContent = "-";
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

async function recomputeTags() {
  const out = el("tagging-result");
  const manualDocId = el("tagging-doc-id").value.trim();
  const docId = manualDocId || selectedDocumentId;
  if (!docId) {
    setResult(out, "Seleziona o inserisci un document_id.", true);
    return;
  }
  try {
    const useAi = el("tagging-use-ai").checked;
    setResult(out, "Ricalcolo tagging in corso...");
    const query = useAi ? "?use_ai=true" : "";
    const result = await api(`/tagging/recompute/document/${docId}${query}`, { method: "POST" });
    setResult(out, result);
    await loadQuestions();
  } catch (err) {
    setResult(out, err.message, true);
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  el("upload-form").addEventListener("submit", uploadFile);
  el("process-btn").addEventListener("click", processDocument);
  el("refresh-docs").addEventListener("click", refreshDocuments);
  el("reload-questions").addEventListener("click", loadQuestions);
  el("questions-search").addEventListener("input", applyQuestionFilters);
  el("questions-type-filter").addEventListener("change", applyQuestionFilters);
  el("recompute-tags").addEventListener("click", recomputeTags);
  el("load-sim-report").addEventListener("click", loadSimulationReport);
  await refreshDocuments();
  await loadKpis();
});
