const el = (id) => document.getElementById(id);
let selectedDocumentId = "";
let allQuestions = [];
let userId = null;
let currentJobId = null;
let jobPollHandle = null;

function setResult(target, value, isError = false) {
  target.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  target.classList.toggle("error", isError);
  target.classList.toggle("result-error", isError);
  target.setAttribute("role", isError ? "alert" : "status");
  target.setAttribute("aria-live", "polite");
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
        <td><span class="pill ai-badge" data-doc-badge="${r.id}">AI ...</span></td>
        <td><button data-doc="${r.id}" class="open-doc" ${disabled}>Apri domande</button></td>
      </tr>`;
    })
    .join("");
  wrap.innerHTML = `<table>
    <thead><tr><th>ID</th><th>Titolo</th><th>Stato</th><th>Pagine</th><th>AI</th><th></th></tr></thead>
    <tbody>${body}</tbody>
  </table>`;

  wrap.querySelectorAll(".open-doc").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-doc");
      if (!id) return;
      selectedDocumentId = id;
      el("doc-id").value = id;
      updateGenDocButton();
      await loadQuestions();
    });
  });
  updateAiCoverageBadges(rows);
}

async function updateAiCoverageBadges(rows) {
  if (!userId || !rows || !rows.length) return;
  const wrap = el("documents");
  await Promise.all(
    rows.map(async (r) => {
      try {
        const cov = await api(
          `/corrections/coverage?user_id=${encodeURIComponent(userId)}&document_id=${encodeURIComponent(r.id)}`
        );
        const node = wrap.querySelector(`[data-doc-badge="${r.id}"]`);
        if (node) {
          node.textContent = `AI ${cov.with_correction}/${cov.total}`;
          if (cov.total > 0 && cov.with_correction >= cov.total) {
            node.classList.add("ok");
          } else {
            node.classList.remove("ok");
          }
        }
      } catch {
        /* ignore */
      }
    })
  );
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
    target.classList.remove("error");
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

// ---------- AI correction generation ----------

function updateGenDocButton() {
  const btn = el("ai-gen-doc");
  if (!btn) return;
  const hint = el("ai-gen-selected-hint");
  const jobRunning = currentJobId !== null;
  if (jobRunning || !selectedDocumentId) {
    btn.disabled = true;
  } else {
    btn.disabled = false;
  }
  if (hint) {
    hint.textContent = selectedDocumentId
      ? `Documento selezionato: ${selectedDocumentId}`
      : "Seleziona un documento dalla tabella per generare per documento.";
  }
}

function renderJobUi(job) {
  const progress = el("ai-gen-progress");
  const fill = el("ai-gen-progress-fill");
  const counters = el("ai-gen-counters");
  const done = el("ai-gen-done");
  const failed = el("ai-gen-failed");
  const statusText = el("ai-gen-status-text");
  const cancelBtn = el("ai-gen-cancel");
  const docBtn = el("ai-gen-doc");
  const freqBtn = el("ai-gen-freq");
  if (!job) {
    progress.hidden = true;
    counters.hidden = true;
    cancelBtn.hidden = true;
    docBtn.disabled = !selectedDocumentId;
    freqBtn.disabled = false;
    return;
  }
  const isActive = job.status === "queued" || job.status === "running";
  progress.hidden = false;
  counters.hidden = false;
  const total = Math.max(1, job.total_questions || 1);
  const pct = Math.min(100, Math.round((100 * (job.processed_count || 0)) / total));
  fill.style.width = `${pct}%`;
  done.textContent = `${job.processed_count || 0} / ${job.total_questions || 0}`;
  if ((job.failed_count || 0) > 0) {
    failed.hidden = false;
    failed.textContent = `${job.failed_count} errori`;
  } else {
    failed.hidden = true;
  }
  const modeLabel = job.mode === "frequency" ? "frequenza" : "documento";
  statusText.textContent = `${job.status} - modalita': ${modeLabel}${
    job.model ? ` - modello: ${job.model}` : ""
  }`;
  cancelBtn.hidden = !isActive;
  docBtn.disabled = isActive || !selectedDocumentId;
  freqBtn.disabled = isActive;
}

async function renderFailures(jobId) {
  const wrap = el("ai-gen-failures");
  if (!wrap) return;
  try {
    const failures = await api(`/corrections/jobs/${jobId}/failures`);
    if (!failures.length) {
      wrap.hidden = true;
      wrap.innerHTML = "";
      return;
    }
    const rows = failures
      .slice(0, 200)
      .map(
        (f) =>
          `<li><code>${f.question_id}</code> - ${escapeHtml(f.error)}<br><span class="muted small">${escapeHtml(
            f.stem_preview
          )}</span></li>`
      )
      .join("");
    wrap.innerHTML = `<details><summary>Errori (${failures.length})</summary><ul>${rows}</ul></details>`;
    wrap.hidden = false;
  } catch (err) {
    wrap.hidden = false;
    wrap.innerHTML = `<p class="error">Errori non recuperabili: ${escapeHtml(err.message)}</p>`;
  }
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stopPolling() {
  if (jobPollHandle) {
    clearInterval(jobPollHandle);
    jobPollHandle = null;
  }
}

async function pollJobOnce() {
  if (!currentJobId) return;
  try {
    const job = await api(`/corrections/jobs/${currentJobId}`);
    renderJobUi(job);
    if (job.status !== "queued" && job.status !== "running") {
      stopPolling();
      const finishedJobId = currentJobId;
      currentJobId = null;
      updateGenDocButton();
      await renderFailures(finishedJobId);
      await refreshDocuments();
      const out = el("ai-gen-result");
      const msg =
        job.status === "done"
          ? `Completato: ${job.succeeded_count} risposte salvate, ${job.failed_count} errori.`
          : `Stato finale: ${job.status}${job.error_message ? ` - ${job.error_message}` : ""}`;
      setResult(out, msg, job.status === "error");
    }
  } catch (err) {
    setResult(el("ai-gen-result"), err.message, true);
  }
}

function startPolling() {
  stopPolling();
  jobPollHandle = setInterval(pollJobOnce, 2000);
}

async function startCorrectionJob(mode) {
  const out = el("ai-gen-result");
  if (!userId) {
    setResult(out, "Utente di default non disponibile", true);
    return;
  }
  const body = { user_id: userId, mode };
  if (mode === "document") {
    if (!selectedDocumentId) {
      setResult(out, "Seleziona un documento dalla tabella.", true);
      return;
    }
    body.document_id = selectedDocumentId;
  }
  try {
    setResult(out, "Avvio generazione...");
    const job = await api("/corrections/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    currentJobId = job.id;
    el("ai-gen-failures").hidden = true;
    el("ai-gen-failures").innerHTML = "";
    renderJobUi(job);
    setResult(out, `Job avviato (${job.total_questions} domande in coda).`);
    startPolling();
    await pollJobOnce();
  } catch (err) {
    setResult(out, err.message, true);
  }
}

async function cancelCorrectionJob() {
  if (!currentJobId) return;
  try {
    await api(`/corrections/jobs/${currentJobId}/cancel`, { method: "POST" });
    await pollJobOnce();
  } catch (err) {
    setResult(el("ai-gen-result"), err.message, true);
  }
}

async function reattachToRunningJob() {
  try {
    const res = await fetch("/corrections/jobs/current");
    if (res.status === 204) {
      renderJobUi(null);
      return;
    }
    if (!res.ok) return;
    const job = await res.json();
    if (job && job.id) {
      currentJobId = job.id;
      renderJobUi(job);
      startPolling();
    }
  } catch {
    /* ignore */
  }
}

async function loadDefaultUser() {
  try {
    const u = await api("/users/default");
    userId = u.id;
  } catch {
    userId = null;
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
  el("ai-gen-doc").addEventListener("click", () => startCorrectionJob("document"));
  el("ai-gen-freq").addEventListener("click", () => startCorrectionJob("frequency"));
  el("ai-gen-cancel").addEventListener("click", cancelCorrectionJob);
  await loadDefaultUser();
  await refreshDocuments();
  await loadKpis();
  await reattachToRunningJob();
  updateGenDocButton();
});
