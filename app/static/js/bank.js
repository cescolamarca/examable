// Question bank: search every question, fix answer keys and explanations, flag bad extractions.
import {
  $,
  $$,
  TYPE_LABELS,
  api,
  attachMenu,
  confirmDialog,
  displayStem,
  getUserId,
  html,
  normalizeOptionId,
  plural,
  render,
  toast,
  toastError,
  withBusy,
} from "./core.js";

const PAGE_SIZE = 25;
const form = $("#filters");
const results = $("#results");
const state = { userId: null, items: [], total: 0, open: new Set(), key: "", sort: "frequency" };

function readUrl() {
  const params = new URLSearchParams(window.location.search);
  form.q.value = params.get("q") || "";
  state.pending = { doc: params.get("doc") || "", preset: params.get("preset") || "" };
  form.type.value = params.get("type") || "";
  state.key = params.get("key") || "";
  state.sort = params.get("sort") || "frequency";
  $("#sort").value = state.sort;
  $$("#key-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === state.key)));
}

function writeUrl() {
  const params = new URLSearchParams();
  const values = { q: form.q.value.trim(), doc: form.doc.value, preset: form.preset.value, type: form.type.value };
  Object.entries({ ...values, key: state.key, sort: state.sort === "frequency" ? "" : state.sort }).forEach(
    ([k, v]) => v && params.set(k, v),
  );
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function queryParams(offset) {
  const params = new URLSearchParams({ user_id: state.userId, limit: PAGE_SIZE, offset, sort: state.sort });
  if (form.q.value.trim()) params.set("search", form.q.value.trim());
  if (form.doc.value) params.set("document_id", form.doc.value);
  if (form.preset.value) params.set("tag_preset", form.preset.value);
  if (form.type.value) params.set("question_type", form.type.value);
  if (state.key === "discarded") params.set("only_discarded", "true");
  else if (state.key) params.set("correction", state.key);
  return params;
}

let requestSeq = 0;
async function load({ append = false } = {}) {
  const seq = ++requestSeq;
  writeUrl();
  if (!append) results.setAttribute("aria-busy", "true");
  try {
    const page = await api(`/questions?${queryParams(append ? state.items.length : 0)}`);
    if (seq !== requestSeq) return; // a newer search started meanwhile
    state.total = page.total;
    state.items = append ? [...state.items, ...page.items] : page.items;
    renderResults();
  } catch (error) {
    toastError(error);
  } finally {
    results.removeAttribute("aria-busy");
  }
}

function keyBadge(q) {
  if (q.is_discarded) return html`<span class="badge badge--danger">Segnalata</span>`;
  if (!q.correction.has_correction) return html`<span class="badge badge--warning badge--dot">Senza risposta</span>`;
  const option = q.correction.correct_option_id;
  return html`<span class="badge badge--success badge--dot">${option ? `Risposta ${option.toUpperCase()}` : "Soluzione salvata"}</span>`;
}

function renderResults() {
  $("#result-count").textContent = plural(state.total, "domanda", "domande");
  $("#more").hidden = state.items.length >= state.total;
  if (!state.items.length) {
    render(
      results,
      html`<div class="card empty">
        <div class="empty__icon" aria-hidden="true">🔎</div>
        <h3>Nessuna domanda trovata</h3>
        <p>Prova a togliere qualche filtro o a cercare un'altra parola.</p>
      </div>`,
    );
    return;
  }
  render(results, state.items.map(renderCard));
  state.items.forEach((q) => state.open.has(q.id) && bindCard(q));
}

function renderCard(q) {
  const open = state.open.has(q.id);
  return html`<article class="qcard ${open ? "is-open" : ""}" data-id="${q.id}">
    <button class="qcard__head" aria-expanded="${open}" data-toggle>
      <span class="grow">
        <span class="qcard__stem">${displayStem(q)}</span>
        <span class="qcard__info">
          <span class="badge">${TYPE_LABELS[q.question_type] || q.question_type}</span>
          ${keyBadge(q)}
          ${q.occurrences_count > 1 ? html`<span class="badge badge--accent">In ${q.occurrences_count} appelli</span>` : ""}
          <span class="tags">${q.tags.filter((t) => t !== "reti").slice(0, 4).map((t) => html`<span class="tag">${t}</span>`)}</span>
        </span>
      </span>
      <span class="qcard__chevron" aria-hidden="true">›</span>
    </button>
    ${open ? renderBody(q) : ""}
  </article>`;
}

function renderBody(q) {
  const key = normalizeOptionId(q.correction.correct_option_id);
  const isMcq = q.question_type === "multiple_choice" && q.options.length;
  return html`<div class="qcard__body">
    ${isMcq
      ? html`<div class="field">
          <span class="field__label">Risposta corretta · tocca un'opzione per impostarla</span>
          <div class="key-options">
            ${q.options.map(
              (o) => html`<button class="key-option" data-key="${o.id}" aria-pressed="${normalizeOptionId(o.id) === key}">
                <span class="option__key">${o.id}</span><span>${o.text}</span>
              </button>`,
            )}
          </div>
        </div>`
      : ""}
    ${q.subparts.length
      ? html`<ol class="question__subparts">${q.subparts.map((s) => html`<li><b>${s.id})</b><span>${s.prompt}</span></li>`)}</ol>`
      : ""}
    <label class="field">
      <span class="field__label">${isMcq ? "Spiegazione" : "Risposta modello"}</span>
      <textarea class="textarea" data-explanation placeholder="${isMcq ? "Perché questa è la risposta giusta (facoltativo)" : "Scrivi la risposta attesa"}">${q.correction.explanation_text || ""}</textarea>
    </label>
    <label class="field">
      <span class="field__label">Tag (separati da virgola)</span>
      <input class="input" data-tags value="${q.tags.join(", ")}" />
    </label>
    <p class="muted small">Compare in: ${q.source_files.join(", ") || "—"}</p>
    <div class="row row--between">
      <div class="row">
        <button class="btn btn--sm" data-save>Salva modifiche</button>
        <button class="btn btn--secondary btn--sm" data-ai>✦ Genera con l'AI</button>
      </div>
      <div class="menu"><button class="btn btn--ghost btn--sm" data-more>Altro ⋯</button></div>
    </div>
  </div>`;
}

function bindCard(q) {
  const card = $(`[data-id="${CSS.escape(String(q.id))}"]`, results);
  const more = card?.querySelector("[data-more]");
  if (!more) return;
  attachMenu(more, [
    {
      label: "Copia il testo",
      icon: "⧉",
      onSelect: async () => {
        const text = [q.stem, "", ...q.options.map((o) => `${o.id}) ${o.text}`)].join("\n").trim();
        await navigator.clipboard.writeText(text).then(
          () => toast("Copiata negli appunti.", "success"),
          () => toast("Impossibile copiare.", "error"),
        );
      },
    },
    q.is_discarded
      ? { label: "Ripristina la domanda", icon: "↺", onSelect: () => setDiscarded(q, false) }
      : { label: "Segnala e rimuovi", icon: "⚑", danger: true, onSelect: () => setDiscarded(q, true) },
  ]);
}

function replaceItem(updated) {
  const index = state.items.findIndex((item) => item.id === updated.id);
  if (index !== -1) state.items[index] = updated;
  renderResults();
}

async function saveCorrection(q, payload) {
  const correction = await api(`/questions/${q.id}/correction`, {
    method: "PUT",
    json: { user_id: state.userId, ...payload },
  });
  replaceItem({
    ...q,
    correction: {
      correct_option_id: correction.correct_option_id,
      explanation_text: correction.explanation_text,
      has_correction: correction.has_correction,
    },
  });
}

async function setDiscarded(q, discarded) {
  if (discarded) {
    const ok = await confirmDialog({
      title: "Segnalare la domanda?",
      message: "Non comparirà più in ripasso e simulazioni. Potrai ripristinarla dal filtro “Segnalate”.",
      confirmLabel: "Segnala",
      danger: true,
    });
    if (!ok) return;
  }
  try {
    await api(`/questions/${q.id}/discard?discarded=${discarded}`, { method: "POST" });
    toast(discarded ? "Domanda segnalata." : "Domanda ripristinata.", "success");
    state.open.delete(q.id);
    await load();
  } catch (error) {
    toastError(error);
  }
}

results.addEventListener("click", async (event) => {
  const card = event.target.closest(".qcard");
  if (!card) return;
  const q = state.items.find((item) => String(item.id) === card.dataset.id);
  if (event.target.closest("[data-toggle]")) {
    if (state.open.has(q.id)) state.open.delete(q.id);
    else state.open.add(q.id);
    renderResults();
    return;
  }
  const keyButton = event.target.closest("[data-key]");
  if (keyButton) {
    const option = normalizeOptionId(keyButton.dataset.key);
    await saveCorrection(q, { correct_option_id: option, answer_payload: { selected_option_normalized: option } })
      .then(() => toast(`Risposta ${option.toUpperCase()} salvata.`, "success"))
      .catch(toastError);
    return;
  }
  const save = event.target.closest("[data-save]");
  if (save) {
    await withBusy(save, async () => {
      const explanation = card.querySelector("[data-explanation]").value.trim();
      const tags = card
        .querySelector("[data-tags]")
        .value.split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      try {
        const tagsChanged = tags.join("|") !== q.tags.join("|");
        if (tagsChanged) await api(`/questions/${q.id}/tags`, { method: "PUT", json: { tags } });
        if (explanation && explanation !== (q.correction.explanation_text || "")) {
          await saveCorrection({ ...q, tags: tagsChanged ? tags.map(slugLike) : q.tags }, { explanation_text: explanation });
        } else if (tagsChanged) {
          replaceItem({ ...q, tags: tags.map(slugLike) });
        }
        toast("Modifiche salvate.", "success");
      } catch (error) {
        toastError(error);
      }
    });
    return;
  }
  const ai = event.target.closest("[data-ai]");
  if (ai) {
    await withBusy(ai, async () => {
      try {
        const result = await api(`/questions/${q.id}/correction/regenerate`, {
          method: "POST",
          json: { user_id: state.userId },
        });
        replaceItem({
          ...q,
          correction: {
            correct_option_id: result.correct_option_id,
            explanation_text: result.explanation_text,
            has_correction: true,
          },
        });
        toast("Risposta generata dall'AI.", "success");
      } catch (error) {
        toastError(error);
      }
    });
  }
});

function slugLike(name) {
  return name
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean)
    .join("-");
}

let debounce;
form.q.addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => load(), 250);
});
form.addEventListener("change", (event) => event.target.name !== "q" && load());
$("#sort").addEventListener("change", (event) => {
  state.sort = event.target.value;
  load();
});
$("#key-filter").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.key = button.dataset.value;
  $$("#key-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  load();
});
$("#more").addEventListener("click", (event) => withBusy(event.currentTarget, () => load({ append: true })));

async function boot() {
  readUrl();
  try {
    state.userId = await getUserId();
    const [documents, presets] = await Promise.all([api("/documents?limit=200"), api("/tag-presets")]);
    const docs = documents.filter((d) => d.questions_count > 0);
    form.doc.insertAdjacentHTML("beforeend", html`${docs.map((d) => html`<option value="${d.id}">${d.title}</option>`)}`.value);
    form.preset.insertAdjacentHTML("beforeend", html`${presets.map((p) => html`<option value="${p.slug}">${p.name}</option>`)}`.value);
    form.preset.hidden = !presets.length;
    form.doc.value = state.pending.doc;
    form.preset.value = state.pending.preset;
    const total = docs.length;
    $("#bank-subtitle").textContent = total
      ? `Tutte le domande estratte da ${plural(total, "documento", "documenti")}, una volta sola anche se ripetute.`
      : "Nessun documento ancora: carica i PDF degli appelli dalla sezione Documenti.";
  } catch (error) {
    toastError(error);
  }
  await load();
}

boot();
