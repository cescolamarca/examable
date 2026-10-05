// Study page: daily review (SM-2), simulations, history and the shared session player.
import {
  $,
  $$,
  TYPE_LABELS,
  api,
  attachMenu,
  confirmDialog,
  correctOptionOf,
  displayStem,
  formatRelative,
  getUserId,
  html,
  isTypingTarget,
  normalizeOptionId,
  plural,
  render,
  toast,
  toastError,
  withBusy,
} from "./core.js";

const REVIEW_LENGTHS = [10, 20, 30];
const home = $("#home");
const sessionRoot = $("#session");

const state = {
  userId: null,
  summary: null,
  documents: [],
  presets: [],
  reviewPreset: "",
  reviewLength: 20,
  onlyWithKey: false,
};

// ======================================================================
// Home
// ======================================================================

async function loadHome() {
  state.userId = await getUserId();
  const [documents, presets] = await Promise.all([api("/documents?limit=200"), api("/tag-presets")]);
  state.documents = documents.filter((d) => d.ingestion_status === "processed" && d.questions_count > 0);
  state.presets = presets;
  await refreshSummary();
  renderBuilderChips();
  await loadHistory();
}

async function refreshSummary() {
  const query = state.reviewPreset ? `?tag_preset=${encodeURIComponent(state.reviewPreset)}` : "";
  state.summary = await api(`/study/summary/${state.userId}${query}`);
  if (!state.reviewPreset) state.bankSummary = state.summary;
  renderReviewCard();
  renderProgressCard();
}

function renderReviewCard() {
  const card = $("#review-card");
  card.removeAttribute("aria-busy");
  const s = state.summary;
  const bank = state.bankSummary || s;

  if (!bank.total) {
    render(
      card,
      html`<div class="empty">
        <div class="empty__icon" aria-hidden="true">📄</div>
        <h3>La banca domande è vuota</h3>
        <p>Carica i PDF degli appelli passati: le domande verranno estratte e unite automaticamente.</p>
        <a class="btn" href="/ingest">Carica documenti</a>
      </div>`,
    );
    return;
  }

  const planned = Math.min(s.due + s.new, state.reviewLength);
  const presetOptions = state.presets.map(
    (p) => html`<option value="${p.slug}" ${p.slug === state.reviewPreset ? "selected" : ""}>${p.name}</option>`,
  );
  render(
    card,
    html`
      <div class="card__header" style="margin-bottom: 0">
        <div>
          <h2>Ripasso di oggi</h2>
          <p class="muted small">Ripetizione dilazionata: ogni domanda torna quando stai per dimenticarla.</p>
        </div>
        ${state.presets.length
          ? html`<select class="select" id="review-preset" style="width: auto" aria-label="Modulo">
              <option value="">Tutti i moduli</option>
              ${presetOptions}
            </select>`
          : ""}
      </div>
      ${planned
        ? html`<div>
            <div class="review-card__count"><strong>${planned}</strong><span class="muted">domande</span></div>
            <div class="review-card__breakdown" style="margin-top: 6px">
              <span><i class="legend-dot" style="background: var(--danger)"></i>${plural(s.due, "da ripassare", "da ripassare")}</span>
              <span><i class="legend-dot" style="background: var(--accent)"></i>${plural(s.new, "nuova", "nuove")} disponibili</span>
            </div>
          </div>`
        : html`<div>
            <div class="review-card__count"><strong>✓</strong><span class="muted">Sei in pari</span></div>
            <p class="muted small" style="margin-top: 6px">
              ${s.next_due_at ? `Il prossimo ripasso è ${formatRelative(s.next_due_at)}.` : "Nessuna domanda in questo modulo."}
            </p>
          </div>`}
      <div class="row">
        <button class="btn btn--lg" id="start-review" ${planned ? "" : "disabled"}>Inizia ripasso</button>
        <div class="segmented" role="group" aria-label="Lunghezza del ripasso">
          ${REVIEW_LENGTHS.map(
            (n) => html`<button type="button" data-length="${n}" aria-pressed="${n === state.reviewLength}">${n}</button>`,
          )}
        </div>
      </div>
    `,
  );
  $("#start-review").addEventListener("click", startReview);
  $("#review-preset")?.addEventListener("change", async (event) => {
    state.reviewPreset = event.target.value;
    await refreshSummary().catch(toastError);
  });
  $$("[data-length]", card).forEach((button) =>
    button.addEventListener("click", () => {
      state.reviewLength = Number(button.dataset.length);
      renderReviewCard();
    }),
  );
}

function renderProgressCard() {
  const card = $("#progress-card");
  card.removeAttribute("aria-busy");
  const s = state.bankSummary || state.summary;
  if (!s.total) {
    render(card, html`<h2>I tuoi progressi</h2><p class="muted">Appariranno qui dopo le prime risposte.</p>`);
    return;
  }
  const learning = Math.max(0, s.total - s.new - s.mastered);
  const pct = (n) => `${(100 * n) / s.total}%`;
  render(
    card,
    html`
      <div>
        <h2>I tuoi progressi</h2>
        <p class="muted small">Su ${plural(s.total, "domanda", "domande")} della banca</p>
      </div>
      <div class="progress-split" aria-hidden="true">
        <span style="width: ${pct(s.mastered)}; background: var(--success)"></span>
        <span style="width: ${pct(learning)}; background: var(--accent)"></span>
      </div>
      <div class="review-card__breakdown">
        <span><i class="legend-dot" style="background: var(--success)"></i>${s.mastered} imparate</span>
        <span><i class="legend-dot" style="background: var(--accent)"></i>${learning} in corso</span>
        <span><i class="legend-dot" style="background: var(--surface-3)"></i>${s.new} mai viste</span>
      </div>
      <div class="grid grid--2" style="gap: 10px; margin-top: 4px">
        <div class="stat" style="padding: 0">
          <div class="stat__value">${s.answered_today}</div>
          <div class="stat__label">risposte oggi${s.answered_today ? ` · ${s.correct_today} giuste` : ""}</div>
        </div>
        <div class="stat" style="padding: 0">
          <div class="stat__value">${s.with_correction}<span class="subtle" style="font-size: 1rem">/${s.total}</span></div>
          <div class="stat__label">con risposta salvata</div>
        </div>
      </div>
    `,
  );
  state.onlyWithKey = s.with_correction >= 10;
  const onlyKey = $("#builder-form [name=only_reviewed_correct]");
  if (onlyKey && !onlyKey.dataset.touched) onlyKey.checked = state.onlyWithKey;
  $("#sim-scope").textContent = `${plural(s.total, "domanda", "domande")} · ${s.with_correction} con risposta`;
}

function renderBuilderChips() {
  render(
    $("#doc-chips"),
    state.documents.length
      ? state.documents.map(
          (d) => html`<button type="button" class="chip" data-id="${d.id}" aria-pressed="false">${d.title}
            <span class="subtle">${d.questions_count}</span></button>`,
        )
      : html`<span class="subtle small">Nessun documento elaborato.</span>`,
  );
  $("#preset-row").hidden = !state.presets.length;
  render(
    $("#preset-chips"),
    state.presets.map((p) => html`<button type="button" class="chip" data-slug="${p.slug}" aria-pressed="false">${p.name}</button>`),
  );
}

function setupBuilder() {
  const form = $("#builder-form");
  form.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
    const step = event.target.closest("[data-step]");
    if (step) {
      const input = step.parentElement.querySelector("input");
      input.value = Math.max(0, Math.min(200, Number(input.value || 0) + Number(step.dataset.step)));
    }
    const priority = event.target.closest("#priority button");
    if (priority) $$("#priority button").forEach((b) => b.setAttribute("aria-pressed", String(b === priority)));
  });
  form.only_reviewed_correct.addEventListener("change", (event) => (event.target.dataset.touched = "1"));
  form.exhaustive.addEventListener("change", () => {
    $$("#counts input, #counts button").forEach((node) => (node.disabled = form.exhaustive.checked));
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      exhaustive: form.exhaustive.checked,
      randomize: form.randomize.checked,
      only_reviewed_correct: form.only_reviewed_correct.checked,
      priority_mode: $("#priority [aria-pressed=true]").dataset.value,
      document_ids: $$("#doc-chips [aria-pressed=true]").map((c) => c.dataset.id),
      tag_presets: $$("#preset-chips [aria-pressed=true]").map((c) => c.dataset.slug),
    };
    for (const name of ["multiple_choice_count", "open_text_count", "multi_part_open_count"]) {
      payload[name] = payload.exhaustive ? 0 : Number(form[name].value || 0);
    }
    if (!payload.exhaustive && !payload.multiple_choice_count + payload.open_text_count + payload.multi_part_open_count) {
      toast("Scegli almeno una domanda.", "error");
      return;
    }
    await withBusy(form.querySelector("[type=submit]"), () => startSimulation(payload));
  });

  $$("#quick-sims [data-preset]").forEach((button) =>
    button.addEventListener("click", () => withBusy(button, () => startSimulation(PRESETS[button.dataset.preset]()))),
  );
}

const PRESETS = {
  exam: () => ({ multiple_choice_count: 10, open_text_count: 2, only_reviewed_correct: state.onlyWithKey }),
  weak: () => ({ multiple_choice_count: 15, priority_mode: "frequently_mistaken", only_reviewed_correct: state.onlyWithKey }),
  unseen: () => ({ multiple_choice_count: 15, priority_mode: "never_viewed", only_reviewed_correct: state.onlyWithKey }),
};

function describeSimulation(sim) {
  const c = sim.config || {};
  if (c.priority_mode === "frequently_mistaken") return "Punti deboli";
  if (c.priority_mode === "never_viewed") return "Mai viste";
  if (sim.exhaustive) return "Tutte le domande";
  const parts = [];
  if (c.multiple_choice_count) parts.push(`${c.multiple_choice_count} scelta multipla`);
  if (c.open_text_count) parts.push(plural(c.open_text_count, "aperta", "aperte"));
  if (c.multi_part_open_count) parts.push(plural(c.multi_part_open_count, "esercizio", "esercizi"));
  return parts.length ? parts.join(" + ") : "Ripasso degli errori";
}

async function loadHistory() {
  const root = $("#history");
  const sims = await api(`/simulations?user_id=${state.userId}&limit=10`);
  root.removeAttribute("aria-busy");
  if (!sims.length) {
    render(
      root,
      html`<div class="empty">
        <div class="empty__icon" aria-hidden="true">🗂</div>
        <h3>Nessuna simulazione</h3>
        <p>Le simulazioni che avvii compaiono qui: potrai riprenderle o rivedere gli errori.</p>
      </div>`,
    );
    return;
  }
  render(
    root,
    html`<ul class="list">
      ${sims.map((sim) => {
        const done = sim.answered_count >= sim.generated_total;
        const score = sim.answered_count ? Math.round((100 * sim.correct_count) / sim.answered_count) : null;
        const tone = score === null ? "" : score >= 70 ? "badge--success" : score >= 50 ? "badge--warning" : "badge--danger";
        return html`<li class="list-item" data-sim="${sim.id}">
          <div class="list-item__main">
            <div class="list-item__title">${describeSimulation(sim)}</div>
            <div class="list-item__meta">
              ${formatRelative(sim.created_at)} · ${plural(sim.generated_total, "domanda", "domande")}
              ${done ? "" : ` · ${plural(sim.answered_count, "risposta", "risposte")}`}
            </div>
          </div>
          ${score === null ? "" : html`<span class="badge ${tone}">${sim.correct_count}/${sim.answered_count} giuste</span>`}
          <button class="btn btn--secondary btn--sm" data-open="${done ? "results" : "resume"}">
            ${done ? "Risultati" : "Riprendi"}
          </button>
          <div class="menu"><button class="btn btn--ghost btn--icon btn--sm" aria-label="Altre azioni">⋯</button></div>
        </li>`;
      })}
    </ul>`,
  );
  $$("[data-sim]", root).forEach((row) => {
    const id = row.dataset.sim;
    const open = row.querySelector("[data-open]");
    open.addEventListener("click", () =>
      withBusy(open, () => openSimulation(id, { showResults: open.dataset.open === "results" })),
    );
    attachMenu(row.querySelector(".menu button"), [
      { label: "Rivedi dall'inizio", icon: "↺", onSelect: () => openSimulation(id, { startAt: 0 }) },
      {
        label: "Elimina",
        icon: "🗑",
        danger: true,
        onSelect: async () => {
          const ok = await confirmDialog({
            title: "Eliminare la simulazione?",
            message: "Le risposte date restano nelle statistiche del ripasso.",
            confirmLabel: "Elimina",
            danger: true,
          });
          if (!ok) return;
          await api(`/simulations/${id}`, { method: "DELETE" }).catch(toastError);
          await loadHistory();
        },
      },
    ]);
  });
}

// ======================================================================
// Session player (shared by daily review and simulations)
// ======================================================================

let session = null;
const correctionCache = new Map();

function newSession(kind, title, questions = []) {
  return {
    kind,
    title,
    simulationId: null,
    questions,
    index: 0,
    answers: new Map(),
    picking: false,
    finished: false,
    review: { limit: 0, seen: new Set(), exhausted: false, loading: false },
  };
}

function getCorrection(question) {
  if (!correctionCache.has(question.id)) {
    const promise = api(`/questions/${question.id}/correction?user_id=${state.userId}`).catch(() => null);
    correctionCache.set(question.id, promise);
  }
  return correctionCache.get(question.id);
}

function prefetch(question) {
  if (question) getCorrection(question);
}

async function startSimulation(partial) {
  const payload = {
    multiple_choice_count: 0,
    open_text_count: 0,
    multi_part_open_count: 0,
    randomize: true,
    priority_mode: "none",
    ...partial,
    user_id: state.userId,
  };
  const result = await api("/simulations/custom", { method: "POST", json: payload }).catch((error) => {
    toastError(error);
    return null;
  });
  if (!result) return;
  if (!result.questions.length) {
    toast(
      payload.only_reviewed_correct
        ? "Nessuna domanda con risposta salvata corrisponde ai filtri. Disattiva l'opzione o aggiungi le risposte nella Banca domande."
        : "Nessuna domanda corrisponde ai filtri scelti.",
      "error",
      { timeout: 7000 },
    );
    return;
  }
  const short = Object.values(result.shortage_by_type || {}).reduce((a, b) => a + b, 0);
  if (short) toast(`Disponibili solo ${result.generated_total} domande su ${result.requested_total} richieste.`);
  session = newSession("simulation", describeSimulation({ config: payload, exhaustive: payload.exhaustive }), result.questions);
  session.simulationId = result.simulation_id;
  enterSession();
}

async function openSimulation(id, { startAt = null, showResults = false } = {}) {
  const sim = await api(`/simulations/${id}`).catch((error) => {
    toastError(error);
    return null;
  });
  if (!sim) return;
  if (!sim.questions.length) {
    toast("Le domande di questa simulazione non sono più disponibili.", "error");
    return;
  }
  session = newSession("simulation", describeSimulation(sim), sim.questions);
  session.simulationId = sim.id;
  sim.questions.forEach((q) => {
    if (!q.attempt) return;
    const selected = q.attempt.answer_payload?.selected_option || null;
    session.answers.set(q.id, {
      selected,
      correct: q.attempt.is_correct,
      dontKnow: q.question_type === "multiple_choice" && !selected,
      revealed: true,
    });
  });
  const firstOpen = sim.questions.findIndex((q) => !session.answers.has(q.id));
  const start = startAt ?? (firstOpen === -1 ? 0 : firstOpen);
  session.index = Math.min(Math.max(0, start), sim.questions.length - 1);
  if (showResults || (startAt === null && firstOpen === -1)) session.finished = true;
  enterSession();
}

async function startReview() {
  session = newSession("review", "Ripasso di oggi");
  session.review.limit = Math.min(state.summary.due + state.summary.new, state.reviewLength);
  session.review.preset = state.reviewPreset;
  enterSession({ loadFirst: true });
}

function enterSession({ loadFirst = false } = {}) {
  correctionCache.clear();
  home.hidden = true;
  sessionRoot.hidden = false;
  document.body.classList.add("in-session");
  window.scrollTo(0, 0);
  if (loadFirst) loadNextReviewQuestion();
  else renderSession();
}

async function exitSession() {
  session = null;
  sessionRoot.hidden = true;
  render(sessionRoot, "");
  home.hidden = false;
  document.body.classList.remove("in-session");
  updateUrl();
  await Promise.all([refreshSummary(), loadHistory()]).catch(toastError);
}

function updateUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("question");
  if (session?.kind === "simulation" && session.simulationId && !session.finished) {
    url.searchParams.set("sim", session.simulationId);
    url.searchParams.set("q", String(session.index + 1));
  } else {
    url.searchParams.delete("sim");
    url.searchParams.delete("q");
  }
  window.history.replaceState(null, "", url);
}

async function loadNextReviewQuestion() {
  const review = session.review;
  if (review.loading) return;
  if (session.questions.length >= review.limit) {
    finishSession();
    return;
  }
  review.loading = true;
  renderSession();
  try {
    const params = new URLSearchParams({ review_filter: "all" });
    if (review.preset) params.set("tag_preset", review.preset);
    if (review.seen.size) params.set("exclude_question_ids", [...review.seen].join(","));
    const next = await api(`/study/next/${state.userId}?${params}`).catch((error) => {
      if (error.status === 404) return null;
      throw error;
    });
    // "scheduled" means nothing is due or new any more: today's review is over.
    if (!next || next.due_reason === "scheduled") {
      review.loading = false;
      finishSession();
      return;
    }
    const question = await api(`/questions/${next.question_id}`);
    question.dueReason = next.due_reason;
    review.seen.add(question.id);
    session.questions.push(question);
    session.index = session.questions.length - 1;
    prefetch(question);
  } catch (error) {
    toastError(error);
  } finally {
    review.loading = false;
  }
  renderSession();
}

function currentQuestion() {
  return session?.questions[session.index] || null;
}

function scores() {
  let correct = 0;
  let wrong = 0;
  for (const answer of session.answers.values()) {
    if (answer.correct === true) correct += 1;
    else if (answer.correct === false) wrong += 1;
  }
  return { correct, wrong };
}

async function recordAttempt(question, isCorrect, grade, payload) {
  await api("/attempts", {
    method: "POST",
    json: {
      user_id: state.userId,
      question_id: question.id,
      is_correct: isCorrect,
      grade,
      answer_payload: { mode: session.kind, ...payload },
      simulation_id: session.kind === "simulation" ? session.simulationId : null,
    },
  }).catch(toastError);
}

async function answer(optionId) {
  const question = currentQuestion();
  if (!question || session.answers.has(question.id)) return;
  const key = correctOptionOf(question, await getCorrection(question));
  const selected = normalizeOptionId(optionId);
  const correct = key ? selected === key : null;
  session.answers.set(question.id, { selected, correct, dontKnow: false, revealed: true });
  renderSession();
  if (key) await recordAttempt(question, correct, correct ? 4 : 1, { selected_option: selected });
}

async function dontKnow() {
  const question = currentQuestion();
  if (!question || session.answers.has(question.id)) return;
  session.answers.set(question.id, { selected: null, correct: false, dontKnow: true, revealed: true });
  renderSession();
  await recordAttempt(question, false, 0, { selected_option: null, dont_know: true });
}

function reveal() {
  const question = currentQuestion();
  if (!question || session.answers.has(question.id)) return;
  session.answers.set(question.id, { selected: null, correct: null, revealed: true, selfAssessed: false });
  renderSession();
}

async function selfAssess(knew) {
  const question = currentQuestion();
  const current = session.answers.get(question?.id);
  if (!current || current.selfAssessed) return;
  session.answers.set(question.id, { ...current, correct: knew, selfAssessed: true });
  renderSession();
  await recordAttempt(question, knew, knew ? 4 : 1, { self_assessed: true });
}

async function saveKey(optionId) {
  const question = currentQuestion();
  const selected = normalizeOptionId(optionId);
  const correction = await api(`/questions/${question.id}/correction`, {
    method: "PUT",
    json: { user_id: state.userId, correct_option_id: selected, answer_payload: { selected_option_normalized: selected } },
  }).catch((error) => {
    toastError(error);
    return null;
  });
  if (!correction) return;
  correctionCache.set(question.id, Promise.resolve(correction));
  session.picking = false;
  const current = session.answers.get(question.id);
  if (current && current.selected && current.correct === null) {
    // Answered before a key existed: score it now and feed the scheduler.
    const correct = current.selected === selected;
    session.answers.set(question.id, { ...current, correct });
    await recordAttempt(question, correct, correct ? 4 : 1, { selected_option: current.selected });
  } else if (current?.selected) {
    session.answers.set(question.id, { ...current, correct: current.selected === selected });
  }
  toast("Risposta corretta salvata.", "success");
  renderSession();
}

async function regenerateWithAi() {
  const question = currentQuestion();
  toast("Genero la risposta con l'AI…");
  try {
    const result = await api(`/questions/${question.id}/correction/regenerate`, {
      method: "POST",
      json: { user_id: state.userId },
    });
    correctionCache.set(question.id, Promise.resolve(result));
    const current = session.answers.get(question.id);
    const key = normalizeOptionId(result.correct_option_id);
    if (current?.selected && key) {
      const correct = current.selected === key;
      if (current.correct === null) await recordAttempt(question, correct, correct ? 4 : 1, { selected_option: current.selected });
      session.answers.set(question.id, { ...current, correct });
    }
    toast("Risposta generata dall'AI.", "success");
    renderSession();
  } catch (error) {
    toastError(error);
  }
}

async function discardCurrent() {
  const question = currentQuestion();
  const ok = await confirmDialog({
    title: "Segnalare la domanda?",
    message: "Verrà esclusa da ripasso e simulazioni (potrai ripristinarla dalla Banca domande).",
    confirmLabel: "Segnala e rimuovi",
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/questions/${question.id}/discard`, { method: "POST" });
  } catch (error) {
    toastError(error);
    return;
  }
  session.answers.delete(question.id);
  session.questions.splice(session.index, 1);
  toast("Domanda segnalata.", "success");
  if (session.kind === "review") {
    session.review.limit = Math.max(0, session.review.limit - 1);
    session.index = Math.max(0, session.questions.length - 1);
    await loadNextReviewQuestion();
    return;
  }
  if (!session.questions.length) {
    await exitSession();
    return;
  }
  session.index = Math.min(session.index, session.questions.length - 1);
  renderSession();
}

async function copyQuestion() {
  const q = currentQuestion();
  const lines = [q.stem, "", ...q.options.map((o) => `${o.id}) ${o.text}`), ...q.subparts.map((s) => `${s.id}) ${s.prompt}`)];
  try {
    await navigator.clipboard.writeText(lines.join("\n").trim());
    toast("Domanda copiata negli appunti.", "success");
  } catch {
    toast("Impossibile copiare negli appunti.", "error");
  }
}

function goNext() {
  if (session.kind === "review") {
    loadNextReviewQuestion();
    return;
  }
  if (session.index >= session.questions.length - 1) {
    finishSession();
    return;
  }
  session.index += 1;
  session.picking = false;
  prefetch(session.questions[session.index + 1]);
  renderSession();
}

function goPrev() {
  if (session.kind !== "simulation" || session.index === 0) return;
  session.index -= 1;
  session.picking = false;
  renderSession();
}

function finishSession() {
  session.finished = true;
  renderSession();
}

// ---------- Rendering ----------

function renderSession() {
  if (!session) return;
  updateUrl();
  if (session.finished) {
    renderSummary();
    return;
  }
  const question = currentQuestion();
  const total = session.kind === "review" ? session.review.limit : session.questions.length;
  const position = Math.min(session.index + 1, total);
  const { correct, wrong } = scores();
  const top = html`
    <div class="session__top">
      <div class="session__bar">
        <button class="btn btn--ghost btn--sm" data-action="exit" title="Esci (Esc)">✕ Esci</button>
        <span class="session__title grow">${session.title}</span>
        <span class="session__score" aria-label="Punteggio">
          <span class="badge badge--success">✓ ${correct}</span>
          <span class="badge badge--danger">✗ ${wrong}</span>
        </span>
        <span class="session__counter">${position} / ${total}</span>
      </div>
      <div class="progress"><div class="progress__bar" style="width: ${(100 * (session.index + (question && session.answers.has(question.id) ? 1 : 0))) / Math.max(1, total)}%"></div></div>
    </div>`;

  if (!question || session.review.loading) {
    render(
      sessionRoot,
      html`${top}
        <div class="question stack" aria-busy="true">
          <div class="skeleton" style="height: 18px; width: 30%"></div>
          <div class="skeleton" style="height: 56px"></div>
          ${[1, 2, 3, 4].map(() => html`<div class="skeleton" style="height: 54px"></div>`)}
        </div>`,
    );
    return;
  }

  getCorrection(question).then((correction) => {
    if (currentQuestion() !== question || session.finished) return;
    renderQuestion(top, question, correction);
  });
}

function renderQuestion(top, question, correction) {
  const answered = session.answers.get(question.id);
  const key = correctOptionOf(question, correction);
  const isMcq = question.question_type === "multiple_choice" && question.options.length > 0;
  const explanation = correction?.explanation_text || question.solution?.explanation || "";
  const lastOfSimulation = session.kind === "simulation" && session.index === session.questions.length - 1;
  const lastOfReview = session.kind === "review" && session.questions.length >= session.review.limit;

  const optionButtons = isMcq
    ? question.options.map((option, i) => {
        const id = normalizeOptionId(option.id);
        let cls = "";
        let mark = "";
        if (session.picking) cls = "is-picking";
        else if (answered) {
          if (key && id === key) {
            cls = "is-correct";
            mark = "✓";
          } else if (id === answered.selected) {
            cls = key ? "is-wrong" : "is-selected";
            mark = key ? "✗" : "";
          } else cls = "is-dim";
        }
        const locked = answered && !session.picking;
        return html`<button class="option ${cls}" data-option="${option.id}" ${locked ? "disabled" : ""}
          aria-keyshortcuts="${String.fromCharCode(97 + i)} ${i + 1}">
          <span class="option__key">${option.id}</span>
          <span class="option__text">${option.text}</span>
          ${mark ? html`<span class="option__result" aria-hidden="true">${mark}</span>` : ""}
        </button>`;
      })
    : "";

  const textOf = (id) => question.options.find((o) => normalizeOptionId(o.id) === id)?.text || "";
  let feedback = "";
  if (session.picking) {
    feedback = html`<div class="feedback feedback--neutral" role="status">
      <span class="feedback__title">Tocca l'opzione corretta</span>
      <span class="muted">Verrà salvata come risposta per le prossime volte.</span>
      <div class="row"><button class="btn btn--secondary btn--sm" data-action="cancel-pick">Annulla</button></div>
    </div>`;
  } else if (answered && isMcq) {
    if (!key) {
      feedback = html`<div class="feedback feedback--neutral" role="status">
        <span class="feedback__title">Questa domanda non ha ancora una risposta salvata</span>
        <span class="muted">${answered.selected ? `Hai scelto la ${answered.selected.toUpperCase()}. ` : ""}Se sai qual è quella giusta, segnala tu la risposta corretta: servirà per le prossime volte.</span>
        <div class="row">
          <button class="btn btn--sm" data-action="pick">Segna la risposta corretta</button>
          <button class="btn btn--secondary btn--sm" data-action="ai">Genera con l'AI</button>
        </div>
      </div>`;
    } else {
      const right = answered.correct === true;
      feedback = html`<div class="feedback ${right ? "feedback--success" : "feedback--danger"}" role="status">
        <span class="feedback__title">${right ? "Risposta corretta" : answered.dontKnow ? "Ecco la soluzione" : "Risposta sbagliata"}</span>
        ${right ? "" : html`<span>La risposta giusta è la <b>${key.toUpperCase()}</b>: ${textOf(key)}</span>`}
        ${explanation ? html`<p class="feedback__explanation">${explanation}</p>` : ""}
      </div>`;
    }
  } else if (answered && !isMcq) {
    const hasKey = Boolean(explanation || correction?.correct_option_id);
    feedback = html`<div class="feedback ${hasKey ? "feedback--neutral" : "feedback--neutral"}" role="status">
      <span class="feedback__title">${hasKey ? "Soluzione" : "Nessuna soluzione salvata"}</span>
      <p class="feedback__explanation">${explanation ||
        "Non c'è ancora una risposta modello: puoi scriverla nella Banca domande o generarla con l'AI."}</p>
      ${hasKey ? "" : html`<div class="row"><button class="btn btn--secondary btn--sm" data-action="ai">Genera con l'AI</button></div>`}
      ${answered.selfAssessed
        ? html`<span class="muted small">${answered.correct ? "Segnata come saputa." : "Segnata come da rivedere."}</span>`
        : html`<div class="row" style="margin-top: 4px">
            <span class="muted small grow">Come è andata?</span>
            <button class="btn btn--sm" style="background: var(--success)" data-action="knew">La sapevo <kbd>1</kbd></button>
            <button class="btn btn--sm btn--danger" data-action="didnt">Non la sapevo <kbd>2</kbd></button>
          </div>`}
    </div>`;
  }

  const meta = html`<div class="row">
    <span class="badge">${TYPE_LABELS[question.question_type] || question.question_type}</span>
    ${question.occurrences_count > 1
      ? html`<span class="badge badge--accent" title="${question.source_files.join(", ")}">In ${question.occurrences_count} appelli</span>`
      : ""}
    ${question.dueReason === "due" ? html`<span class="badge badge--danger badge--dot">Da ripassare</span>` : ""}
    ${question.dueReason === "new" ? html`<span class="badge badge--dot">Nuova</span>` : ""}
  </div>`;

  let primary;
  if (isMcq && !answered) {
    primary = html`<button class="btn btn--ghost" data-action="dont-know">Non lo so <kbd>S</kbd></button>
      ${session.kind === "simulation"
        ? html`<button class="btn btn--secondary" data-action="next">${lastOfSimulation ? "Vedi risultati" : "Salta"}</button>`
        : ""}`;
  } else if (!isMcq && !answered) {
    primary = html`${session.kind === "simulation"
        ? html`<button class="btn btn--ghost" data-action="next">${lastOfSimulation ? "Vedi risultati" : "Salta"}</button>`
        : ""}
      <button class="btn" data-action="reveal">Mostra soluzione <kbd>↵</kbd></button>`;
  } else {
    const label = lastOfSimulation || lastOfReview ? "Vedi risultati" : "Prossima";
    const waiting = !isMcq && answered && !answered.selfAssessed;
    primary = html`<button class="btn" data-action="next" ${waiting ? "disabled" : ""}>${label} →</button>`;
  }

  render(
    sessionRoot,
    html`${top}
      <article class="question" aria-live="polite">
        <div class="question__meta">
          ${meta}
          <div class="menu"><button class="btn btn--ghost btn--icon btn--sm" data-menu aria-label="Azioni sulla domanda">⋯</button></div>
        </div>
        <h2 class="question__stem">${displayStem(question)}</h2>
        ${question.subparts.length
          ? html`<ol class="question__subparts">
              ${question.subparts.map((s) => html`<li><b>${s.id})</b><span>${s.prompt}</span></li>`)}
            </ol>`
          : ""}
        ${isMcq ? html`<div class="options">${optionButtons}</div>` : ""}
        ${feedback}
      </article>
      <div class="session__actions">
        <div class="row">
          ${session.kind === "simulation"
            ? html`<button class="btn btn--ghost" data-action="prev" ${session.index === 0 ? "disabled" : ""}>← Indietro</button>`
            : ""}
        </div>
        <span class="shortcuts">${isMcq ? html`<kbd>A</kbd>–<kbd>${String.fromCharCode(64 + question.options.length)}</kbd> rispondi · ` : ""}<kbd>↵</kbd> avanti · <kbd>Esc</kbd> esci</span>
        <div class="row">${primary}</div>
      </div>`,
  );

  attachMenu($("[data-menu]", sessionRoot), [
    ...(isMcq ? [{ label: "Modifica risposta corretta", icon: "✎", onSelect: () => setPicking(true) }] : []),
    { label: "Genera risposta con l'AI", icon: "✦", onSelect: regenerateWithAi },
    { label: "Copia il testo", icon: "⧉", onSelect: copyQuestion },
    { label: "Segnala e rimuovi", icon: "⚑", danger: true, onSelect: discardCurrent },
  ]);
}

function setPicking(value) {
  session.picking = value;
  renderSession();
}

function renderSummary() {
  const answeredCount = session.answers.size;
  const { correct, wrong } = scores();
  const total = session.questions.length;
  const unanswered = total - answeredCount;
  const pct = correct + wrong ? Math.round((100 * correct) / (correct + wrong)) : 0;
  const mistakes = session.questions.filter((q) => session.answers.get(q.id)?.correct === false);

  render(
    sessionRoot,
    html`<div class="session__top">
        <div class="session__bar">
          <button class="btn btn--ghost btn--sm" data-action="exit">✕ Chiudi</button>
          <span class="session__title grow">${session.title}</span>
        </div>
      </div>
      <section class="summary">
        <div class="summary__ring" style="--pct: ${pct}" data-label="${pct}%" role="img" aria-label="${pct}% di risposte giuste"></div>
        <h1>${total ? `${correct} su ${correct + wrong} giuste` : "Nessuna domanda"}</h1>
        <p class="muted" style="margin-top: 6px">
          ${plural(wrong, "sbagliata", "sbagliate")}${unanswered > 0 ? ` · ${plural(unanswered, "senza risposta", "senza risposta")}` : ""}
          ${session.kind === "review" ? " · le domande sbagliate torneranno tra 10 minuti" : ""}
        </p>
        <div class="summary__actions">
          ${mistakes.length ? html`<button class="btn btn--lg" data-action="retry">Ripeti gli errori (${mistakes.length})</button>` : ""}
          ${session.kind === "simulation" && unanswered > 0
            ? html`<button class="btn btn--secondary btn--lg" data-action="resume">Completa le domande saltate</button>`
            : ""}
          <button class="btn btn--secondary btn--lg" data-action="exit">Torna allo studio</button>
        </div>
        ${mistakes.length
          ? html`<div class="section-title"><h2>Da rivedere</h2></div>
              <div class="mistakes" id="mistakes">${mistakes.map((q) => html`<div class="mistake" data-q="${q.id}">
                <div class="mistake__stem">${displayStem(q)}</div>
                <div class="mistake__answers muted small">Carico la soluzione…</div>
              </div>`)}</div>`
          : correct
            ? html`<p class="muted">Nessun errore: ottimo lavoro!</p>`
            : ""}
      </section>`,
  );

  mistakes.forEach(async (q) => {
    const correction = await getCorrection(q);
    const key = correctOptionOf(q, correction);
    const given = session.answers.get(q.id);
    const textOf = (id) => q.options.find((o) => normalizeOptionId(o.id) === id)?.text || "";
    const node = $(`[data-q="${CSS.escape(String(q.id))}"] .mistake__answers`, sessionRoot);
    if (!node) return;
    render(
      node,
      html`${q.question_type === "multiple_choice"
        ? html`<div class="answer-line answer-line--wrong">✗ ${given?.selected ? html`La tua risposta: <b>${given.selected.toUpperCase()}</b> ${textOf(given.selected)}` : "Non risposta"}</div>
            ${key ? html`<div class="answer-line answer-line--right">✓ Risposta giusta: <b>${key.toUpperCase()}</b> ${textOf(key)}</div>` : ""}`
        : html`<div class="answer-line answer-line--wrong">✗ Segnata come da rivedere</div>`}
      ${correction?.explanation_text ? html`<p style="color: var(--text); margin-top: 4px">${correction.explanation_text}</p>` : ""}`,
    );
    node.classList.remove("muted");
  });
}

async function retryMistakes() {
  const ids = session.questions.filter((q) => session.answers.get(q.id)?.correct === false).map((q) => q.id);
  const result = await api("/simulations/from-questions", {
    method: "POST",
    json: { user_id: state.userId, question_ids: ids, randomize: true },
  }).catch((error) => {
    toastError(error);
    return null;
  });
  if (!result) return;
  session = newSession("simulation", "Ripasso degli errori", result.questions);
  session.simulationId = result.simulation_id;
  enterSession();
}

// ---------- Events ----------

sessionRoot.addEventListener("click", (event) => {
  if (!session) return;
  const option = event.target.closest("[data-option]");
  if (option && !option.disabled) {
    if (session.picking) saveKey(option.dataset.option);
    else answer(option.dataset.option);
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  const handlers = {
    exit: exitSession,
    next: goNext,
    prev: goPrev,
    "dont-know": dontKnow,
    reveal,
    knew: () => selfAssess(true),
    didnt: () => selfAssess(false),
    pick: () => setPicking(true),
    "cancel-pick": () => setPicking(false),
    ai: regenerateWithAi,
    retry: retryMistakes,
    resume: () => {
      session.finished = false;
      session.index = Math.max(0, session.questions.findIndex((q) => !session.answers.has(q.id)));
      renderSession();
    },
  };
  handlers[action]?.();
});

document.addEventListener("keydown", (event) => {
  if (!session || event.metaKey || event.ctrlKey || event.altKey) return;
  if (isTypingTarget(event.target) || document.querySelector("dialog[open], .menu__list")) return;
  const key = event.key.toLowerCase();
  if (key === "escape") {
    event.preventDefault();
    exitSession();
    return;
  }
  if (session.finished) return;
  const question = currentQuestion();
  if (!question) return;
  const answered = session.answers.get(question.id);
  const isMcq = question.question_type === "multiple_choice" && question.options.length > 0;

  if (isMcq && (!answered || session.picking)) {
    const index = /^[1-9]$/.test(key) ? Number(key) - 1 : /^[a-z]$/.test(key) ? key.charCodeAt(0) - 97 : -1;
    const option = question.options[index];
    if (option) {
      event.preventDefault();
      if (session.picking) saveKey(option.id);
      else answer(option.id);
      return;
    }
  }
  if (key === "s" && isMcq && !answered) {
    event.preventDefault();
    dontKnow();
  } else if ((key === "enter" || key === "arrowright") && !isMcq && !answered) {
    event.preventDefault();
    reveal();
  } else if (!isMcq && answered && !answered.selfAssessed && (key === "1" || key === "2")) {
    event.preventDefault();
    selfAssess(key === "1");
  } else if ((key === "enter" || key === "arrowright") && answered && (isMcq || answered.selfAssessed)) {
    event.preventDefault();
    goNext();
  } else if (key === "arrowleft") {
    event.preventDefault();
    goPrev();
  }
});

// ---------- Boot ----------

async function boot() {
  setupBuilder();
  try {
    await loadHome();
  } catch (error) {
    toastError(error);
    return;
  }
  const params = new URLSearchParams(window.location.search);
  const simId = params.get("sim");
  if (simId) {
    const position = Number.parseInt(params.get("q") || params.get("question") || "", 10);
    await openSimulation(simId, { startAt: Number.isFinite(position) && position > 0 ? position - 1 : null });
  }
}

boot();
