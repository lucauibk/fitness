export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
export const WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// ------------------------------------------------------------- API
const TIMEOUT_MS = 10000;

/** Haengt das gewaehlte Profil an – ausser die Anfrage bringt schon eins mit. */
function withProfile(path) {
  const url = `/api/${path}`;
  if (profileId === null || /[?&]profile=/.test(url)) return url;
  return url + (url.includes("?") ? "&" : "?") + `profile=${profileId}`;
}

export async function api(path, { method = "GET", body, timeout = TIMEOUT_MS } = {}) {
  let res;
  try {
    // Ohne Timeout haengt ein toter Tailnet-Weg bis zu einer Minute.
    const signal = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(timeout) : undefined;
    res = await fetch(withProfile(path), {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch {
    // Netzwerkfehler: Server aus oder Tailnet weg – main.js zeigt das Banner.
    window.dispatchEvent(new CustomEvent("api-offline"));
    const err = new Error("Server nicht erreichbar.");
    err.offline = true;
    throw err;
  }
  // Antwort vom Service Worker statt vom Pi: letzter Stand aus dem Cache bzw. noch nie geladen
  const cachedAt = res.headers.get("X-Offline-Cache");
  if (cachedAt !== null) {
    window.dispatchEvent(new CustomEvent("api-stale", { detail: cachedAt }));
  } else if (res.headers.get("X-Offline")) {
    window.dispatchEvent(new CustomEvent("api-offline"));
  } else {
    window.dispatchEvent(new CustomEvent("api-online"));
  }
  if (cachedAt !== null && method !== "GET") {
    // Der Worker hat eine alte Kopie geliefert statt zu schreiben. Fuer die Fitness-App ist
    // das kein Fehler: der Aufrufer legt den Eintrag in den Ausgang (siehe outbox.js).
    const err = new Error("Offline – wird später gesendet.");
    err.offline = true;
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? "Ungültige Eingabe." : data.detail;
    const err = new Error(detail || `Fehler ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ------------------------------------------------------------ Datum
const pad = (n) => String(n).padStart(2, "0");
export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
/** "2026-09-21" als lokaler Tag. new Date("2026-09-21") waere UTC-Mitternacht – westlich
 *  von UTC schon der Vortag. Volle Zeitstempel gehen unveraendert durch. */
export const parseYmd = (s) => (/^\d{4}-\d{2}-\d{2}$/.test(s || "")
  ? new Date(Number(s.slice(0, 4)), Number(s.slice(5, 7)) - 1, Number(s.slice(8, 10))) : new Date(s));

/** "Heute 10:00", "Morgen 23:59", "Mi 16.09.", "Gestern", "Mo 3.11.2027" */
export function relDate(iso, { time = true } = {}) {
  if (!iso) return "ohne Datum";
  const d = new Date(iso);
  const days = Math.round((startOfDay(d) - startOfDay(new Date())) / 86400000);
  const t = time && !(d.getHours() === 0 && d.getMinutes() === 0) ? ` ${hm(d)}` : "";
  if (days === 0) return `Heute${t}`;
  if (days === 1) return `Morgen${t}`;
  if (days === -1) return `Gestern${t}`;
  if (days === 2) return `Übermorgen${t}`;
  const wd = WEEKDAYS[(d.getDay() + 6) % 7];
  const year = d.getFullYear() !== new Date().getFullYear() ? d.getFullYear() : "";
  return `${wd} ${d.getDate()}.${d.getMonth() + 1}.${year}${days > 0 && days < 7 ? t : ""}`;
}

export function ago(iso) {
  if (!iso) return "nie";
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} min`;
  if (min < 1440) return `vor ${Math.round(min / 60)} h`;
  return `vor ${Math.round(min / 1440)} T.`;
}

/** Leerzustand mit optionaler Aktion: action = { label, attrs } */
export function empty(title, text = "", action = null) {
  return `<div class="empty">
    <p class="empty-title">${title}</p>
    ${text ? `<p>${text}</p>` : ""}
    ${action ? `<button type="button" class="btn" ${action.attrs}>${action.label}</button>` : ""}
  </div>`;
}

export const skeleton = (rows = 3) => `<div class="skeleton">${
  Array.from({ length: rows }, () => '<div class="sk-row"><i></i><i></i></div>').join("")}</div>`;

// --------------------------------------------------------- Profile
export const safeColor = (c) => (/^#[0-9a-f]{3,8}$/i.test(c || "") ? c : "var(--faint)");

export const GROUPS = { brust: "Brust", ruecken: "Rücken", beine: "Beine",
  schultern: "Schultern", arme: "Arme", rumpf: "Rumpf" };

/** Gewicht menschenlesbar. Ohne Zusatzgewicht (Klimmzuege) bleibt das Feld leer. */
export const fmtWeight = (w, unit) => (w == null
  ? (unit === "koerper" ? "Körpergewicht" : "–")
  : `${String(w).replace(".", ",")} kg`);

/** "4×8 @ 60 kg" – oder "8/8/7 @ 60 kg", wenn die Saetze sich unterscheiden. */
export function setsSummary(sets, unit) {
  if (!sets?.length) return "";
  const reps = sets.map((s) => s.reps ?? 0);
  const same = reps.every((r) => r === reps[0]);
  const label = same ? `${sets.length}×${reps[0]}` : reps.join("/");
  const weights = [...new Set(sets.map((s) => s.weight).filter((w) => w != null))];
  if (!weights.length) return label;
  const w = weights.length === 1 ? weights[0] : Math.max(...weights);
  return `${label} @ ${fmtWeight(w, unit)}`;
}

/** Urteil ueber eine fertige Einheit, wie es der Server rechnet: Punktzahl, was gestimmt
 *  hat und was zum perfekten Training gefehlt hat. Eingetippt wird davon nichts – die
 *  Bewertung kommt aus den geloggten Saetzen, dem Trainingstag und der letzten Einheit. */
export function feedbackHtml(fb) {
  if (!fb) return "";
  const tone = fb.score >= 85 ? "gut" : fb.score >= 50 ? "mittel" : "schwach";
  const li = (t, name) => `<li>${icon(name === "bad" ? "warn" : "check")}<span>${esc(t)}</span></li>`;
  // Erst was gestimmt hat, dann hoechstens drei Hinweise (die wichtigsten, der Server
  // sortiert nach Gewicht) – eine lange Maengelliste nach einer guten Einheit liest sich
  // wie ein Tadel.
  const bad = (fb.missing || []).map((m) => m.text);
  const [top, rest] = [bad.slice(0, 3), bad.slice(3)];
  return `<div class="feedback tone-${tone}">
    <div class="feedback-score">
      <span class="big-number">${fb.score}</span>
      <span>von 100 · <b>${esc(fb.label)}</b></span>
    </div>
    ${fb.good?.length ? `<p class="feedback-lead">Das hat gestimmt</p>
      <ul class="feedback-list good">${fb.good.map((t) => li(t, "good")).join("")}</ul>` : ""}
    ${top.length ? `<p class="feedback-lead">Darauf achten</p>
      <ul class="feedback-list bad">${top.map((t) => li(t, "bad")).join("")}</ul>
      ${rest.length ? `<details class="field-more feedback-more"><summary>+ ${rest.length} weitere</summary>
        <ul class="feedback-list bad">${rest.map((t) => li(t, "bad")).join("")}</ul></details>` : ""}`
      : `<p class="feedback-lead">${icon("trophy")} Nichts auszusetzen.</p>`}
  </div>`;
}

/** Das gewaehlte Profil haengt an jeder Anfrage; gefiltert wird auf dem Server. */
let profileId = null;
export const setProfile = (id) => { profileId = id ? Number(id) : null; };
export const getProfile = () => profileId;

// ------------------------------------------------------ Local prefs
export const prefs = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(`fit.${key}`); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  /** Gibt zurueck, ob es geklappt hat (privater Modus oder voller Speicher: nein). */
  set(key, value) {
    try { localStorage.setItem(`fit.${key}`, JSON.stringify(value)); return true; } catch { return false; }
  },
};

// ------------------------------------------------- Angefangene Formulare
// Jede Eingabe markiert ihr Formular. Ansichten, die sich beim Zurueckkehren in die App
// neu laden, lassen markierte Formulare in Ruhe – ein Blick in die Uhr-App soll keine
// halb eingetragene Cardio-Einheit kosten. Nach dem Speichern: delete form.dataset.dirty.
document.addEventListener("input", (e) => {
  const form = e.target.closest?.("form");
  if (form) form.dataset.dirty = "1";
}, true);
export const hasDirtyForm = (root) => Boolean(root?.querySelector("form[data-dirty]"));

// ------------------------------------------------------------ Sheet
let lastFocus = null;
let ignorePop = false;
let popDone = null;   // loest auf, sobald das history.back() aus closeSheet() durch ist

/** Wer nach dem Schliessen eines Sheets navigiert, muss hierauf warten: closeSheet() geht
 *  per history.back() einen Schritt zurueck, und das passiert asynchron – ein sofort
 *  gesetzter location.hash wuerde von diesem Zurueck gleich wieder verschluckt. */
export const sheetClosed = () => popDone?.promise ?? Promise.resolve();
const coarse = () => window.matchMedia("(pointer: coarse)").matches;

/**
 * Oeffnet ein Bottom-Sheet (Laptop: Dialog).
 * title: Ueberschrift, body: HTML, footer: HTML fuer die feste Fusszeile.
 * Gibt das Sheet-Element zurueck.
 */
export function openSheet({ title = "", body = "", footer = "", wide = false } = {}) {
  const replacing = Boolean(document.getElementById("sheet"));
  closeSheet(false, { replacing: true });
  if (!replacing) {
    lastFocus = document.activeElement;
    // Eigener Verlaufseintrag: Zurueck (Android, Wischgeste) schliesst das Sheet statt die Seite.
    if (!history.state?.sheet) history.pushState({ sheet: true }, "");
  }
  const wrap = document.createElement("div");
  wrap.id = "sheet";
  wrap.innerHTML = `<div class="sheet-backdrop"></div>
    <div class="sheet ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
      <div class="sheet-grip" aria-hidden="true"></div>
      <div class="sheet-head">
        <h2 id="sheetTitle">${title}</h2>
        <button type="button" class="icon-btn" data-close aria-label="Schließen">${icon("x")}</button>
      </div>
      <div class="sheet-body">${body}</div>
      ${footer ? `<div class="sheet-foot">${footer}</div>` : ""}
    </div>`;
  document.body.appendChild(wrap);
  document.body.classList.add("sheet-open");
  const sheet = wrap.querySelector(".sheet");
  wrap.querySelector(".sheet-backdrop").addEventListener("click", () => requestClose());
  wrap.addEventListener("click", (e) => {
    const t = e.target.closest("[data-close]");
    // Links mit Ziel schliessen ueber den Hashwechsel, sonst wuerde history.back() die Navigation schlucken.
    if (t && !t.matches("a[href]")) closeSheet();
  });
  // Eingaben merken, damit ein versehentliches Wegtippen nichts verwirft
  const markDirty = (e) => { if (e.target.closest("form")) wrap.dataset.dirty = "1"; };
  wrap.addEventListener("input", markDirty);
  wrap.addEventListener("change", markDirty);

  // Nach unten wischen schliesst (nur am Griff/Kopf, damit Scrollen im Inhalt geht)
  let y0 = null;
  const head = wrap.querySelector(".sheet-head");
  [wrap.querySelector(".sheet-grip"), head].forEach((el) => {
    el.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      if (y0 === null) return;
      const dy = Math.max(0, e.touches[0].clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    el.addEventListener("touchend", (e) => {
      if (y0 === null) return;
      const dy = e.changedTouches[0].clientY - y0;
      y0 = null;
      sheet.style.transform = "";
      if (dy > 90) requestClose();
    });
  });

  // Fokus sofort setzen (nicht erst im naechsten Frame – in Hintergrund-Tabs kaeme der nie).
  // Wer den Inhalt spaeter ersetzt, ruft focusSheet() erneut auf.
  focusSheet(sheet);
  requestAnimationFrame(() => wrap.classList.add("open"));
  return sheet;
}

/** Kleines Aktionsmenue als Sheet: seltene Aktionen hinter einem "⋯" statt einer Knopfreihe.
 *  items: [{ label, icon, run, danger }] – run laeuft nach dem Schliessen. */
export function actionMenu(title, items) {
  const list = items.filter(Boolean);
  const sheet = openSheet({
    title,
    body: `<div class="menu-list">${list.map((it, i) => `<button type="button"
      class="menu-item ${it.danger ? "danger" : ""}" data-menu-item="${i}">${icon(it.icon || "right")} ${esc(it.label)}</button>`).join("")}</div>`,
  });
  sheet.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-menu-item]");
    if (!b) return;
    closeSheet();
    list[Number(b.dataset.menuItem)].run();
  });
  return sheet;
}

export function focusSheet(sheet = document.querySelector("#sheet .sheet")) {
  if (!sheet) return;
  // Am Handy nur in Anlege-Formularen ([autofocus]) direkt die Tastatur oeffnen
  const selector = coarse() ? "[autofocus]"
    : "[autofocus], .sheet-body input:not([type=hidden]):not([type=radio]):not([hidden]), .sheet-body select, .sheet-body textarea";
  const first = sheet.querySelector(selector);
  if (first) return first.focus({ preventScroll: true });
  sheet.setAttribute("tabindex", "-1");
  (coarse() ? sheet : sheet.querySelector("[data-close]") || sheet).focus({ preventScroll: true });
}

/**
 * Schliesst das Sheet. fromHistory: Aufruf aus popstate (Eintrag ist schon weg);
 * replacing: ein neues Sheet ersetzt das alte (Eintrag bleibt).
 */
export function closeSheet(restoreFocus = true, { fromHistory = false, replacing = false } = {}) {
  const el = document.getElementById("sheet");
  if (!el) return;
  el.remove();
  if (replacing) return;
  document.body.classList.remove("sheet-open");
  if (!fromHistory && history.state?.sheet) {
    ignorePop = true;
    let resolve;
    popDone = { promise: new Promise((r) => { resolve = r; }), resolve };
    history.back();
  }
  if (restoreFocus && lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
}

/** Schliessen per Backdrop, Wischen, Esc oder Zurueck: bei Eingaben erst nachfragen. */
function requestClose(opts) {
  const wrap = document.getElementById("sheet");
  if (!wrap) return;
  if (!wrap.dataset.dirty) return closeSheet(true, opts);
  askDiscard(wrap);
}

function askDiscard(wrap) {
  const sheet = wrap.querySelector(".sheet");
  if (sheet.querySelector("[data-discard-bar]")) return;
  const bar = document.createElement("div");
  bar.className = "sheet-foot confirm-bar";
  bar.dataset.discardBar = "";
  bar.innerHTML = `<span class="grow">Eingaben verwerfen?</span>
    <button type="button" class="btn ghost" data-keep>Behalten</button>
    <button type="button" class="btn danger" data-discard>Verwerfen</button>`;
  const foot = sheet.querySelector(".sheet-foot");
  if (foot) foot.hidden = true;
  sheet.appendChild(bar);
  bar.querySelector("[data-keep]").addEventListener("click", () => {
    bar.remove();
    if (foot) foot.hidden = false;
    focusSheet(sheet);
  });
  bar.querySelector("[data-discard]").addEventListener("click", () => closeSheet());
  bar.querySelector("[data-keep]").focus({ preventScroll: true });
}

window.addEventListener("popstate", () => {
  if (ignorePop) {
    ignorePop = false;
    popDone?.resolve();
    popDone = null;
    return;
  }
  // Zurueck waehrend des Scanners: nur den Scanner schliessen, das Sheet darunter bleibt –
  // dessen Verlaufseintrag wieder herstellen, er wurde gerade verbraucht.
  const scanner = document.querySelector(".scanner");
  if (scanner) {
    if (document.getElementById("sheet")) history.pushState({ sheet: true }, "");
    scanner.dispatchEvent(new CustomEvent("scanner-close"));
    return;
  }
  const wrap = document.getElementById("sheet");
  if (!wrap) return;
  if (wrap.dataset.dirty) {
    history.pushState({ sheet: true }, "");
    askDiscard(wrap);
  } else {
    closeSheet(true, { fromHistory: true });
  }
});

document.addEventListener("keydown", (e) => {
  const sheet = document.querySelector("#sheet .sheet");
  if (!sheet) return;
  if (document.querySelector(".scanner")) return;   // der Scanner liegt darueber und regelt das selbst
  if (e.key === "Escape") {
    if (e.target.matches?.("select")) return; // Esc schliesst hier nur die Auswahlliste
    e.preventDefault();
    requestClose();
    return;
  }
  if (e.key !== "Tab") return;
  // Fokus im Sheet halten
  const items = [...sheet.querySelectorAll("a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => el.offsetParent !== null);
  if (!items.length) return;
  const [first, last] = [items[0], items[items.length - 1]];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

// Zeilen mit role=button (divs) wie Buttons bedienbar machen: Enter und Leertaste
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches?.('[role="button"]:not(button):not(a)')) {
    e.preventDefault();
    e.target.click();
  }
});

// iOS: Tastatur ueberdeckt das Sheet – Hoehe der Tastatur als CSS-Variable
if (window.visualViewport) {
  const kb = () => {
    const vv = window.visualViewport;
    const h = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    document.documentElement.style.setProperty("--kb", `${h}px`);
  };
  window.visualViewport.addEventListener("resize", kb);
  window.visualViewport.addEventListener("scroll", kb);
}

// ------------------------------------------------------------ Toast
/** toast("Erledigt", { action: "Rückgängig", onAction: fn, error: false, sticky: false })
 *  Fehler bleiben laenger und lassen sich wegtippen; sticky bleibt, bis man es wegtippt –
 *  fuer Meldungen, bei denen etwas nicht gespeichert wurde. */
export function toast(message, { action, onAction, error = false, duration, sticky = false } = {}) {
  const box = document.getElementById("toasts");
  const t = document.createElement("div");
  t.className = `toast${error ? " error" : ""}`;
  t.setAttribute("role", error ? "alert" : "status");
  t.innerHTML = `<span>${esc(message)}</span>${action ? `<button type="button" data-action>${esc(action)}</button>` : ""}${
    error || sticky ? `<button type="button" class="toast-close" data-close aria-label="Meldung schließen">${icon("x")}</button>` : ""}`;
  box.appendChild(t);
  const remove = () => { t.classList.add("leave"); setTimeout(() => t.remove(), 200); };
  const ms = duration ?? (action ? 5000 : error ? 7000 : 2800);
  let timer = sticky ? null : setTimeout(remove, ms);
  // Beim Zeigen/Fokussieren anhalten, damit "Rückgängig" erreichbar bleibt
  const pause = () => clearTimeout(timer);
  const resume = () => { clearTimeout(timer); if (!sticky) timer = setTimeout(remove, 2000); };
  t.addEventListener("mouseenter", pause);
  t.addEventListener("mouseleave", resume);
  t.addEventListener("focusin", pause);
  t.addEventListener("focusout", resume);
  t.querySelector("[data-close]")?.addEventListener("click", () => { clearTimeout(timer); remove(); });
  t.querySelector("[data-action]")?.addEventListener("click", async () => {
    clearTimeout(timer);
    remove();
    try { await onAction?.(); } catch (e) { toast(e.message, { error: true }); }
  });
}

// --------------------------------------------------- Loeschen mit Rueckgaengig
// Der Server kennt kein Papierkorb-Loeschen: Eintrag sofort ausblenden, erst nach Ablauf
// des Toasts wirklich loeschen. Ansichten filtern mit isPendingDelete().
const pendingDeletes = new Map();
export const isPendingDelete = (path) => pendingDeletes.has(path);
const dataChanged = () => window.dispatchEvent(new CustomEvent("data-changed"));

export function deleteLater(path, message, { onDone } = {}) {
  // Profil jetzt festhalten: wechselt man in den 5 Sekunden das Profil, soll die Löschung trotzdem
  // beim ursprünglichen Profil ankommen (sonst 403 nach "gelöscht").
  const bound = withProfile(path).replace(/^\/api\//, "");
  const commit = async () => {
    if (!pendingDeletes.has(path)) return;
    pendingDeletes.delete(path);
    try { await api(bound, { method: "DELETE" }); } catch (e) { toast(e.message, { error: true }); }
    onDone?.();
    dataChanged();
  };
  pendingDeletes.set(path, setTimeout(commit, 5200));
  dataChanged();
  toast(message, {
    action: "Rückgängig",
    duration: 5000,
    onAction: () => {
      clearTimeout(pendingDeletes.get(path));
      pendingDeletes.delete(path);
      dataChanged();
    },
  });
}

window.addEventListener("pagehide", () => {
  for (const [path, timer] of pendingDeletes) {
    clearTimeout(timer);
    // withProfile: alle Loesch-Routen verlangen das Profil, sonst lehnt der Server ab
    fetch(withProfile(path), { method: "DELETE", keepalive: true }).catch(() => {});
  }
  pendingDeletes.clear();
});

// ---------------------------------------------------------- Formulare
/** Formularfelder als Objekt; leere Strings werden null. */
export function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "radio") { if (el.checked) out[el.name] = el.value; }
    // Leere Pflicht-Zahlenfelder weglassen statt null zu schicken – null wuerde den Wert loeschen.
    else if (el.type === "number") { if (el.value !== "") out[el.name] = Number(el.value); else if (!el.required) out[el.name] = null; }
    else if (el.type === "datetime-local") out[el.name] = el.value ? new Date(el.value).toISOString() : null;
    else out[el.name] = el.value.trim() === "" ? null : el.value.trim();
  }
  return out;
}

/** Fehler am Formular anzeigen; nennt die API ein Feld ('due_at'), wird es markiert. */
export function formError(form, err) {
  let box = form.querySelector(".form-error");
  if (!box) {
    box = document.createElement("p");
    box.className = "form-error";
    box.setAttribute("role", "alert");
    form.prepend(box);
  }
  box.textContent = err.message;
  form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
  const field = /'(\w+)'/.exec(err.message)?.[1];
  const input = field && form.querySelector(`[name="${field}"]`);
  if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
}

/** Fehler als Toast statt unbehandelter Promise-Rejection. */
export const guard = (fn) => async (...args) => {
  try { return await fn(...args); } catch (e) { toast(e.message, { error: true }); }
};

/**
 * Submit-Handler, der waehrend der Anfrage alle Speichern-Knoepfe sperrt –
 * ein Doppeltipp legt sonst zwei Eintraege an.
 */
export function submitOnce(form, fn) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (form.dataset.busy) return;
    const buttons = [...form.querySelectorAll("[type=submit]"),
      ...(form.id ? document.querySelectorAll(`[type=submit][form="${form.id}"]`) : [])];
    form.dataset.busy = "1";
    buttons.forEach((b) => { b.disabled = true; b.setAttribute("aria-busy", "true"); });
    try { await fn(ev); } finally {
      delete form.dataset.busy;
      buttons.forEach((b) => { b.disabled = false; b.removeAttribute("aria-busy"); });
    }
  });
}

/** Knopf waehrend einer Aktion sperren. */
export async function busy(button, fn) {
  if (button.disabled) return undefined;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await fn(); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
}

/** Loeschen mit zweitem Tipp statt Browser-Dialog. confirm: Text beim zweiten Tipp. */
export function armDelete(button, action, label = "Löschen", confirm = "Wirklich löschen?") {
  let armed = false;
  let timer;
  button.addEventListener("click", guard(async () => {
    if (!armed) {
      armed = true;
      button.classList.add("armed");
      button.lastChild.textContent = ` ${confirm}`;
      timer = setTimeout(() => { armed = false; button.classList.remove("armed"); button.lastChild.textContent = ` ${label}`; }, 3000);
      return;
    }
    clearTimeout(timer);
    await action();
  }));
}
