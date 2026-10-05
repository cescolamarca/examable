// Shared helpers for every page: API access, safe HTML templating, toasts, dialogs, labels.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

// ---------- Safe templating ----------
// `html` escapes every interpolated value unless it is itself the result of `html`
// (or wrapped in `raw`). Question text comes from PDFs and LLMs, so nothing is
// ever inserted as markup by accident.

class SafeHtml {
  constructor(value) {
    this.value = value;
  }
  toString() {
    return this.value;
  }
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderValue(value) {
  if (value === null || value === undefined || value === false) return "";
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(renderValue).join("");
  return escapeHtml(value);
}

export function html(strings, ...values) {
  let out = "";
  strings.forEach((chunk, i) => {
    out += chunk;
    if (i < values.length) out += renderValue(values[i]);
  });
  return new SafeHtml(out);
}

export const raw = (value) => new SafeHtml(String(value));

export function render(target, template) {
  target.innerHTML = renderValue(template);
}

// ---------- API ----------

const ADMIN_TOKEN_KEY = "examableAdminToken";

function readToken() {
  try {
    return localStorage.getItem(ADMIN_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function storeToken(token) {
  try {
    if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
    else localStorage.removeItem(ADMIN_TOKEN_KEY);
  } catch {
    // Storage may be unavailable (private mode): the token is asked again next time.
  }
}

// The API speaks English; translate the messages users can actually run into.
const MESSAGES = [
  [/Only PDF files are supported/, "Si possono caricare solo file PDF."],
  [/not a valid PDF/, "Il file non è un PDF valido."],
  [/upload limit/, "Il file supera il limite di dimensione."],
  [/Document already ingested/, "Questo documento è già stato caricato."],
  [/Parsing failed/, "Non è stato possibile leggere le domande da questo PDF."],
  [/Source file missing/, "Il file originale non è più disponibile: caricalo di nuovo."],
  [/No questions available/, "Nessuna domanda disponibile."],
  [/MULTIMODAL_API_KEY/, "Le funzioni AI non sono configurate: imposta MULTIMODAL_API_KEY sul server."],
  [/generation is disabled/, "La generazione delle risposte con l'AI è disattivata sul server."],
  [/No pending questions/, "Tutte le domande hanno già una risposta."],
  [/another_job_running/, "C'è già una generazione in corso."],
  [/AI regeneration failed|non ha prodotto/, "L'AI non è riuscita a generare una risposta. Riprova più tardi."],
  [/Invalid admin token/, "Token amministratore non valido."],
  [/Admin token required/, "Serve il token amministratore."],
  [/Failed to fetch|NetworkError/, "Impossibile contattare il server."],
];

export function translateError(message) {
  const match = MESSAGES.find(([pattern]) => pattern.test(message));
  return match ? match[1] : message;
}

/** Exercise stems start with their heading ("ESERCIZIO 2 ..."), which the type badge already shows. */
export function displayStem(question) {
  return question.stem.replace(/^\s*ESERCIZIO\s+\d+[.:)]?\s*/i, "") || question.stem;
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Call the JSON API. `json` is sent as the request body; `form` as multipart.
 * Protected endpoints answer 401/403 when ADMIN_TOKEN is set on the server: the
 * user is asked for the token once and the request is retried.
 */
export async function api(url, { method = "GET", json, form, retried = false } = {}) {
  const headers = new Headers();
  const token = readToken();
  if (token) headers.set("X-Admin-Token", token);
  let body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  } else if (form) {
    body = form;
  }
  const response = await fetch(url, { method, headers, body });
  if (response.status === 204) return null;
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { detail: text };
  }

  if ((response.status === 401 || response.status === 403) && !retried) {
    if (response.status === 403) storeToken("");
    const entered = await promptDialog({
      title: "Serve il token amministratore",
      message:
        response.status === 403
          ? "Il token salvato non è valido. Inseriscilo di nuovo per continuare."
          : "Caricamenti, elaborazione e funzioni AI sono protetti. Inserisci il token impostato sul server.",
      placeholder: "ADMIN_TOKEN",
      confirmLabel: "Continua",
      type: "password",
    });
    if (entered) {
      storeToken(entered.trim());
      return api(url, { method, json, form, retried: true });
    }
  }

  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.code || `Errore ${response.status}`;
    throw new ApiError(translateError(message), response.status, detail);
  }
  return payload;
}

let userIdPromise = null;

/** The app runs in single-user mode: every client shares the default user. */
export function getUserId() {
  userIdPromise ??= api("/users/default").then((user) => user.id);
  return userIdPromise;
}

// ---------- Feedback ----------

export function toast(message, type = "info", { timeout = 4200 } = {}) {
  const region = document.getElementById("toasts");
  if (!region) return;
  const node = document.createElement("div");
  node.className = `toast toast--${type}`;
  node.setAttribute("role", type === "error" ? "alert" : "status");
  node.textContent = message;
  region.append(node);
  setTimeout(() => node.remove(), timeout);
}

export function toastError(error) {
  toast(translateError(error?.message || String(error)), "error", { timeout: 6500 });
}

/** Show a spinner on `button` while `task` runs; returns the task result. */
export async function withBusy(button, task) {
  if (!button) return task();
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    return await task();
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
}

function openDialog(content, { onOpen } = {}) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "dialog";
    render(dialog, content);
    document.body.append(dialog);
    const close = (value) => {
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close(null);
    });
    dialog.addEventListener("click", (event) => {
      const action = event.target.closest("[data-dialog]");
      if (action) close(action.dataset.dialog);
      else if (event.target === dialog) close(null);
    });
    dialog.showModal();
    onOpen?.(dialog, close);
  });
}

export async function confirmDialog({ title, message, confirmLabel = "Conferma", danger = false }) {
  const result = await openDialog(html`
    <div class="dialog__body">
      <h2>${title}</h2>
      ${message ? html`<p class="muted">${message}</p>` : ""}
    </div>
    <div class="dialog__actions">
      <button class="btn btn--secondary" data-dialog="cancel">Annulla</button>
      <button class="btn ${danger ? "btn--danger" : ""}" data-dialog="ok" autofocus>${confirmLabel}</button>
    </div>
  `);
  return result === "ok";
}

export function promptDialog({ title, message, placeholder = "", confirmLabel = "OK", type = "text" }) {
  return openDialog(
    html`
      <form class="dialog__body" method="dialog">
        <h2>${title}</h2>
        ${message ? html`<p class="muted">${message}</p>` : ""}
        <input class="input" name="value" type="${type}" placeholder="${placeholder}" autocomplete="off" required />
      </form>
      <div class="dialog__actions">
        <button class="btn btn--secondary" data-dialog="cancel">Annulla</button>
        <button class="btn" data-submit>${confirmLabel}</button>
      </div>
    `,
    {
      onOpen(dialog, close) {
        const input = dialog.querySelector("input");
        const submit = () => (input.value.trim() ? close(input.value) : input.focus());
        dialog.querySelector("form").addEventListener("submit", (event) => {
          event.preventDefault();
          submit();
        });
        dialog.querySelector("[data-submit]").addEventListener("click", submit);
        input.focus();
      },
    },
  ).then((value) => (value === "cancel" ? null : value));
}

/** A small popover menu; `items` are {label, icon, danger, onSelect}. */
export function attachMenu(trigger, items) {
  const wrap = trigger.closest(".menu");
  let list = null;
  const closeMenu = () => {
    list?.remove();
    list = null;
    trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", onDocumentClick, true);
  };
  const onDocumentClick = (event) => {
    if (!wrap.contains(event.target)) closeMenu();
  };
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.addEventListener("click", () => {
    if (list) return closeMenu();
    list = document.createElement("div");
    list.className = "menu__list";
    list.setAttribute("role", "menu");
    render(
      list,
      items.map(
        (item, i) => html`<button class="menu__item ${item.danger ? "menu__item--danger" : ""}" role="menuitem"
          data-index="${i}"><span aria-hidden="true">${item.icon || ""}</span>${item.label}</button>`,
      ),
    );
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-index]");
      if (!button) return;
      closeMenu();
      items[Number(button.dataset.index)].onSelect();
    });
    list.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeMenu();
        trigger.focus();
      }
    });
    wrap.append(list);
    trigger.setAttribute("aria-expanded", "true");
    list.querySelector("button")?.focus();
    document.addEventListener("click", onDocumentClick, true);
  });
}

// ---------- Domain labels and formatting ----------

export const TYPE_LABELS = {
  multiple_choice: "Scelta multipla",
  open_text: "Domanda aperta",
  multi_part_open: "Esercizio",
};

export const STATUS_LABELS = {
  uploaded: { label: "Caricato", tone: "warning" },
  processing: { label: "In elaborazione", tone: "accent" },
  processed: { label: "Pronto", tone: "success" },
  error: { label: "Errore", tone: "danger" },
};

export function normalizeOptionId(value) {
  return String(value ?? "")
    .trim()
    .replace(/[).:]/g, "")
    .toLowerCase();
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

export function formatDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" });
}

export function formatRelative(iso) {
  if (!iso) return "";
  const diff = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("it", { numeric: "auto" });
  const steps = [
    [60_000, "second", 1000],
    [3_600_000, "minute", 60_000],
    [86_400_000, "hour", 3_600_000],
    [604_800_000, "day", 86_400_000],
    [Infinity, "week", 604_800_000],
  ];
  const [, unit, size] = steps.find(([limit]) => abs < limit);
  return rtf.format(Math.round(diff / size), unit);
}

/** Correct option for a question: the user's answer key, then any solution embedded in the bank. */
export function correctOptionOf(question, correction) {
  const fromCorrection = normalizeOptionId(
    correction?.correct_option_id ||
      correction?.answer_payload?.selected_option_normalized ||
      correction?.answer_payload?.selected_option_raw,
  );
  if (fromCorrection) return fromCorrection;
  const solution = question?.solution || {};
  for (const key of ["correct_option", "correctOption", "correct_answer", "correctAnswer", "answer", "option"]) {
    const value = normalizeOptionId(solution[key]);
    if (value) return value;
  }
  return "";
}

export function isTypingTarget(element) {
  return Boolean(element?.closest?.("input, textarea, select, [contenteditable='true']"));
}
