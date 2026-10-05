// Documents: drag-and-drop upload + processing, document list and AI answer-key jobs.
import {
  $,
  $$,
  STATUS_LABELS,
  api,
  attachMenu,
  confirmDialog,
  formatDate,
  getUserId,
  html,
  plural,
  render,
  toast,
  toastError,
} from "./core.js";

const state = { userId: null, documents: [], coverage: new Map(), jobId: null, poll: null };
const queue = [];
let uploading = false;

// ---------- Upload ----------

const dropzone = $("#dropzone");
const fileInput = $("#file-input");

["dragenter", "dragover"].forEach((type) =>
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-over");
  }),
);
["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, () => dropzone.classList.remove("is-over")));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  enqueue([...event.dataTransfer.files]);
});
fileInput.addEventListener("change", () => {
  enqueue([...fileInput.files]);
  fileInput.value = "";
});

function enqueue(files) {
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "upload";
    $("#uploads").prepend(row);
    const item = { file, row };
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadState(item, "error", "Non è un PDF");
      continue;
    }
    setUploadState(item, "waiting", "In coda");
    queue.push(item);
  }
  processQueue();
}

function setUploadState(item, phase, message) {
  const tone = { waiting: "", upload: "badge--accent", process: "badge--accent", done: "badge--success", error: "badge--danger", warning: "badge--warning" }[phase];
  const width = { waiting: 0, upload: 30, process: 70, done: 100, error: 100, warning: 100 }[phase];
  render(
    item.row,
    html`<span class="upload__name">${item.file.name}</span>
      <span class="badge ${tone}">${message}</span>
      <div class="progress ${phase === "done" ? "progress--success" : ""}"><div class="progress__bar" style="width: ${width}%; ${phase === "error" ? "background: var(--danger)" : phase === "warning" ? "background: var(--warning)" : ""}"></div></div>`,
  );
}

async function processQueue() {
  if (uploading) return;
  uploading = true;
  while (queue.length) {
    const item = queue.shift();
    try {
      setUploadState(item, "upload", "Caricamento…");
      const form = new FormData();
      form.append("file", item.file);
      const uploaded = await api("/documents", { method: "POST", form });
      setUploadState(item, "process", "Estrazione delle domande…");
      const result = await api(`/documents/${uploaded.document_id}/process`, { method: "POST" });
      const ocr = result.extraction_method === "ocr" ? " · letto con OCR" : "";
      setUploadState(item, "done", `${plural(result.extracted, "domanda", "domande")}${ocr}`);
    } catch (error) {
      const duplicate = error.status === 409;
      setUploadState(item, duplicate ? "warning" : "error", duplicate ? "Già caricato" : error.message);
    }
    await refresh();
  }
  uploading = false;
}

// ---------- Documents ----------

async function refresh() {
  const [documents, summary] = await Promise.all([api("/documents?limit=500"), api(`/study/summary/${state.userId}`)]);
  state.documents = documents;
  renderStats(summary);
  renderDocuments();
  loadCoverage();
}

function renderStats(summary) {
  const processed = state.documents.filter((d) => d.ingestion_status === "processed").length;
  const pct = summary.total ? Math.round((100 * summary.with_correction) / summary.total) : 0;
  render(
    $("#stats"),
    html`<div class="stat"><div class="stat__value">${state.documents.length}</div><div class="stat__label">documenti (${processed} pronti)</div></div>
      <div class="stat"><div class="stat__value">${summary.total}</div><div class="stat__label">domande uniche</div></div>
      <div class="stat"><div class="stat__value">${state.documents.reduce((n, d) => n + d.questions_count, 0) - summary.total}</div><div class="stat__label">ripetizioni riconosciute</div></div>
      <div class="stat"><div class="stat__value">${pct}%</div><div class="stat__label">con risposta</div></div>`,
  );
  $("#coverage-bar").style.width = `${pct}%`;
  $("#coverage-label").textContent = `${summary.with_correction} su ${summary.total} domande hanno una risposta`;
}

function renderDocuments() {
  const root = $("#documents");
  root.removeAttribute("aria-busy");
  if (!state.documents.length) {
    render(
      root,
      html`<div class="empty">
        <div class="empty__icon" aria-hidden="true">📄</div>
        <h3>Nessun documento</h3>
        <p>Trascina qui sopra i PDF degli appelli passati per creare la banca domande.</p>
      </div>`,
    );
    return;
  }
  render(
    root,
    html`<table class="table">
      <thead><tr><th>Documento</th><th class="hide-sm">Stato</th><th class="num hide-sm">Pagine</th><th class="num">Domande</th><th class="hide-sm">Risposte</th><th></th></tr></thead>
      <tbody>
        ${state.documents.map((d) => {
          const status = STATUS_LABELS[d.ingestion_status] || { label: d.ingestion_status, tone: "" };
          const ocr = /OCR/i.test(d.ingestion_error || "") && d.ingestion_status === "processed";
          return html`<tr data-doc="${d.id}">
            <td>
              <div class="doc-title">${d.title}</div>
              <div class="subtle small">${formatDate(d.created_at)}${ocr ? " · letto con OCR" : ""}
                ${d.ingestion_status === "processed" ? "" : html`<span class="show-sm"> · ${status.label}</span>`}</div>
            </td>
            <td class="hide-sm"><span class="badge badge--${status.tone}" title="${d.ingestion_status === "error" ? d.ingestion_error || "" : ""}">${status.label}</span></td>
            <td class="num hide-sm">${d.pages ?? "—"}</td>
            <td class="num">${d.ingestion_status === "processed" ? html`<a href="/bank?doc=${d.id}">${d.questions_count}</a>` : "—"}</td>
            <td class="hide-sm" data-coverage><span class="subtle small">…</span></td>
            <td class="num"><div class="menu"><button class="btn btn--ghost btn--icon btn--sm" aria-label="Azioni su ${d.title}">⋯</button></div></td>
          </tr>`;
        })}
      </tbody>
    </table>`,
  );
  $$("tr[data-doc]", root).forEach((row) => {
    const doc = state.documents.find((d) => d.id === row.dataset.doc);
    const processed = doc.ingestion_status === "processed";
    attachMenu(row.querySelector(".menu button"), [
      ...(processed
        ? [
            { label: "Vedi le domande", icon: "☰", onSelect: () => (window.location.href = `/bank?doc=${doc.id}`) },
            { label: "Genera risposte mancanti con l'AI", icon: "✦", onSelect: () => startJob("document", doc.id) },
            { label: "Rigenera tutte le risposte con l'AI", icon: "↻", onSelect: () => regenerateAll(doc) },
            { label: "Ricalcola i tag", icon: "#", onSelect: () => recomputeTags(doc) },
          ]
        : []),
      { label: processed ? "Elabora di nuovo" : "Elabora", icon: "⚙", onSelect: () => reprocess(doc) },
      { label: "Elimina", icon: "🗑", danger: true, onSelect: () => deleteDocument(doc) },
    ]);
  });
}

async function loadCoverage() {
  await Promise.all(
    state.documents
      .filter((d) => d.ingestion_status === "processed")
      .map(async (d) => {
        const cell = $(`tr[data-doc="${d.id}"] [data-coverage]`);
        try {
          const coverage = await api(`/corrections/coverage?user_id=${state.userId}&document_id=${d.id}`);
          const pct = coverage.total ? (100 * coverage.with_correction) / coverage.total : 0;
          if (cell)
            render(
              cell,
              html`<div class="row" style="flex-wrap: nowrap">
                <div class="progress progress--success" style="width: 70px"><div class="progress__bar" style="width: ${pct}%"></div></div>
                <span class="small muted">${coverage.with_correction}/${coverage.total}</span>
              </div>`,
            );
        } catch {
          if (cell) cell.textContent = "—";
        }
      }),
  );
}

async function reprocess(doc) {
  toast(`Elaboro “${doc.title}”…`);
  try {
    const result = await api(`/documents/${doc.id}/process`, { method: "POST" });
    toast(`${doc.title}: ${plural(result.extracted, "domanda estratta", "domande estratte")}.`, "success");
  } catch (error) {
    toastError(error);
  }
  await refresh();
}

async function recomputeTags(doc) {
  try {
    const result = await api(`/tagging/recompute/document/${doc.id}`, { method: "POST" });
    toast(`Tag aggiornati su ${plural(result.questions_tagged, "domanda", "domande")}.`, "success");
  } catch (error) {
    toastError(error);
  }
}

async function deleteDocument(doc) {
  const ok = await confirmDialog({
    title: `Eliminare “${doc.title}”?`,
    message: "Le domande presenti solo in questo documento verranno eliminate; quelle che compaiono anche in altri appelli restano.",
    confirmLabel: "Elimina",
    danger: true,
  });
  if (!ok) return;
  try {
    const result = await api("/admin/delete-documents", { method: "POST", json: { document_ids: [doc.id] } });
    const removed = result.deleted[0]?.questions ?? 0;
    toast(`Documento eliminato (${plural(removed, "domanda rimossa", "domande rimosse")}).`, "success");
  } catch (error) {
    toastError(error);
  }
  await refresh();
}

// ---------- AI answer keys ----------

async function regenerateAll(doc) {
  const ok = await confirmDialog({
    title: "Rigenerare tutte le risposte?",
    message: `L'AI riscriverà la risposta di ogni domanda di “${doc.title}”, anche di quelle che hai già corretto a mano.`,
    confirmLabel: "Rigenera tutto",
    danger: true,
  });
  if (ok) await startJob("document", doc.id, true);
}

async function startJob(mode, documentId = null, overwrite = false) {
  try {
    const job = await api("/corrections/jobs", {
      method: "POST",
      json: { user_id: state.userId, mode, document_id: documentId, overwrite },
    });
    toast(`Generazione avviata: ${plural(job.total_questions, "domanda", "domande")} in coda.`);
    trackJob(job);
  } catch (error) {
    toastError(error);
  }
}

function trackJob(job) {
  state.jobId = job.id;
  renderJob(job);
  clearInterval(state.poll);
  state.poll = setInterval(pollJob, 2000);
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/corrections/jobs/${state.jobId}`);
    renderJob(job);
    if (!["queued", "running"].includes(job.status)) {
      clearInterval(state.poll);
      state.jobId = null;
      const failures = job.failed_count ? await api(`/corrections/jobs/${job.id}/failures`) : [];
      renderJob(job, failures);
      toast(
        job.status === "done"
          ? `Generazione completata: ${plural(job.succeeded_count, "risposta salvata", "risposte salvate")}.`
          : `Generazione ${job.status === "cancelled" ? "annullata" : "interrotta"}.`,
        job.status === "done" ? "success" : "info",
      );
      await refresh();
    }
  } catch (error) {
    toastError(error);
  }
}

function renderJob(job, failures = []) {
  const box = $("#job");
  const active = ["queued", "running"].includes(job.status);
  $("#ai-fill").disabled = active;
  box.hidden = false;
  const pct = job.total_questions ? (100 * job.processed_count) / job.total_questions : 0;
  const label = {
    queued: "In coda",
    running: "In corso",
    done: "Completata",
    cancelled: "Annullata",
    interrupted: "Interrotta",
    error: "Errore",
  }[job.status];
  render(
    box,
    html`<div class="row row--between">
        <strong>Generazione ${job.mode === "frequency" ? "per frequenza" : "del documento"} · ${label}</strong>
        ${active ? html`<button class="btn btn--secondary btn--sm" id="job-cancel" ${job.cancel_requested ? "disabled" : ""}>
          ${job.cancel_requested ? "Annullamento…" : "Annulla"}</button>` : html`<button class="btn btn--ghost btn--sm" id="job-close">Chiudi</button>`}
      </div>
      <div class="progress"><div class="progress__bar" style="width: ${pct}%"></div></div>
      <span class="small muted">${job.processed_count} di ${job.total_questions} · ${job.succeeded_count} salvate${job.failed_count ? ` · ${job.failed_count} errori` : ""} · modello ${job.model}</span>
      ${job.error_message ? html`<span class="small" style="color: var(--danger)">${job.error_message}</span>` : ""}
      ${failures.length
        ? html`<details><summary class="small">Mostra gli errori (${failures.length})</summary>
            <ul class="failures">${failures.map((f) => html`<li><b>${f.error}</b><br /><span class="muted">${f.stem_preview}</span></li>`)}</ul>
          </details>`
        : ""}`,
  );
  $("#job-cancel")?.addEventListener("click", async () => {
    await api(`/corrections/jobs/${job.id}/cancel`, { method: "POST" }).catch(toastError);
    pollJob();
  });
  $("#job-close")?.addEventListener("click", () => (box.hidden = true));
}

$("#ai-fill").addEventListener("click", () => startJob("frequency"));

async function boot() {
  try {
    state.userId = await getUserId();
    await refresh();
    const current = await api("/corrections/jobs/current");
    if (current) trackJob(current);
  } catch (error) {
    toastError(error);
  }
}

boot();
