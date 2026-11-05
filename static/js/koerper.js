/** Körper: Gewicht, Körperfett, Muskelmasse, Umfänge – manuell oder aus einem
 *  Screenshot der Studio-Körperanalyse (ViCoach), den die KI ausliest. */
import { $, api, armDelete, closeSheet, deleteLater, esc, formData, formError, getProfile,
  icon, isPendingDelete, openSheet, skeleton, submitOnce, toast, parseYmd, prefs } from "./util.js";
import * as outbox from "./outbox.js";
import { sparkline } from "./fortschritt.js";

let root;
let data = null;

const UNITS = { weight_kg: "kg", body_fat_pct: "%", muscle_mass_kg: "kg", water_pct: "%",
  visceral_fat: "", bmr_kcal: "kcal", bio_age: "J.", waist_cm: "cm", hip_cm: "cm",
  chest_cm: "cm", arm_cm: "cm", thigh_cm: "cm", neck_cm: "cm" };
const MAIN = ["weight_kg", "body_fat_pct", "muscle_mass_kg", "water_pct", "visceral_fat",
  "bmr_kcal", "bio_age"];
const GIRTHS = ["waist_cm", "hip_cm", "chest_cm", "arm_cm", "thigh_cm", "neck_cm"];
const fmt = (v, key) => `${Number(v).toLocaleString("de-AT", { maximumFractionDigits: 1 })}${UNITS[key] ? ` ${UNITS[key]}` : ""}`;
const dm = (d) => parseYmd(d).toLocaleDateString("de-AT", { day: "numeric", month: "short" });

const signed = (v, key) => `${v > 0 ? "+" : "−"}${fmt(Math.abs(v), key)}`;

/** Veränderung als Text; unter der Anzeigegenauigkeit (0,1) gilt „unverändert“. */
function delta(v, key) {
  return v === null || v === undefined ? null : Math.abs(v) < 0.1 ? "±0" : signed(v, key);
}

function trendLines(key) {
  const m = data.trend?.metrics?.[key];
  if (!m) return "";
  const parts = [];
  if (m.since_last) parts.push(`${delta(m.since_last.delta, key)} seit letzter Messung`);
  const w4 = delta(m.w4, key), w12 = delta(m.w12, key);
  if (w4 !== null) parts.push(`4 Wo. ${w4}`);
  if (w12 !== null) parts.push(`12 Wo. ${w12}`);
  if (!parts.length) return "";
  return `<p class="hint">${parts.join(" · ")}${m.noisy ? " · Tendenz, Waage ungenau" : ""}${m.stale ? " · lange nicht gemessen" : ""}</p>`;
}

function verdictCard() {
  const v = data.trend?.verdict;
  if (!v) return "";
  const cls = v.status === "gegenlaeufig" ? " warn" : "";
  return `<p class="callout${cls}">${icon(v.status === "passt" ? "check" : v.status === "gegenlaeufig" ? "warn" : "spark")}<span><b>Gewichtstrend · Ziel ${esc(v.goal)}</b><br>${esc(v.text)}</span></p>`;
}

// KI-Tipps: das erste Mal auf Knopfdruck, danach erneuern sie sich selbst, sobald neuer
// Kontext da ist (neue Messung). Gemerkt pro Profil auf dem Gerät; die Signatur ändert
// sich mit jeder Messung, dann sind alte Tipps überholt und werden ersetzt.
const SECTIONS = [["gut", "Was gut läuft", "check"], ["schlecht", "Was nicht gut läuft", "warn"],
  ["verbessern", "Was du verbessern kannst", "spark"]];
const tipsKey = () => `bodytips.${getProfile()}`;
const tipsSignature = () => JSON.stringify(Object.entries(data.latest).map(([k, v]) => [k, v.date, v.value]));
let tipsBusy = false;
let tipsFailedSig = null;   // nach einem Fehler nicht in Schleife neu versuchen, bis wieder neuer Kontext kommt

function tipsCard() {
  if (!data.tips_available || !Object.keys(data.latest).length) return "";
  const saved = prefs.get(tipsKey());
  const tips = saved?.sig === tipsSignature() ? saved.tips : null;
  const sections = tips ? SECTIONS.filter(([k]) => tips[k]?.length).map(([k, title, ic]) => `
    <h4 class="tips-title">${icon(ic)} ${title}</h4>
    <ul class="tips-list">${tips[k].map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`).join("") : "";
  const note = tipsBusy ? "Tipps werden aktualisiert …"
    : tips ? "KI-Einschätzung, keine ärztliche Beratung. Sie erneuert sich bei jeder neuen Messung."
    : `Die KI bewertet deine Messungen: was gut läuft, was nicht und was du an Werten oder am Messen verbessern kannst. Danach
      aktualisiert sie sich bei jeder neuen Messung selbst. Dafür gehen Ziel, Geschlecht, Größe und die
      Trend-Zahlen (kein Name) an Anthropic.`;
  return `<div class="card body-tips">
    <div class="bw-head"><p class="eyebrow">Tipps zur Körpermessung</p>
      <button type="button" class="btn tonal" data-tips${tipsBusy ? " disabled" : ""}>${icon("spark")} ${tips ? "Neu erstellen" : "Tipps erstellen"}</button></div>
    ${sections}<p class="hint"${tipsBusy ? ' role="status"' : ""}>${note}</p>
  </div>`;
}

async function fetchTips() {
  if (tipsBusy) return;
  const sig = tipsSignature();
  tipsBusy = true;
  render();
  try {
    const tips = await api("measurements/advice", { method: "POST", body: {}, timeout: 65000 });
    tipsFailedSig = null;
    if (!prefs.set(tipsKey(), { sig, tips })) toast("Tipps werden nicht gemerkt (Speicher voll oder privater Modus).");
  } catch (e) {
    tipsFailedSig = sig;
    toast(e.offline ? "Für Tipps braucht es eine Verbindung." : e.message, { error: true });
  } finally {
    tipsBusy = false;
    render();
  }
}

/** Hat die Person schon einmal Tipps erstellt und passen sie nicht mehr zu den Messungen? */
function tipsOutdated() {
  const saved = prefs.get(tipsKey());
  return Boolean(data.tips_available && saved && Object.keys(data.latest).length
    && saved.sig !== tipsSignature() && tipsFailedSig !== tipsSignature());
}

function tile(key) {
  const latest = data.latest[key];
  if (!latest) return "";
  const series = data.series[key] || [];
  return `<div class="card body-tile">
    <p class="eyebrow">${esc(data.labels[key])}${latest.estimated ? " · geschätzt" : ""}</p>
    <div class="bw-head"><span class="big-number">${fmt(latest.value, key)}</span>
      <span class="hint">${dm(latest.date)}${latest.source === "vicoach" ? " · ViCoach" : ""}</span></div>
    ${trendLines(key)}
    ${series.length > 1 ? sparkline(series.map((p) => ({ value: p.value, date: p.date })), { height: 48 }) : ""}
  </div>`;
}

function render() {
  const pend = outbox.pending("measurements");
  const hasAny = Object.keys(data.latest).length;
  const girths = GIRTHS.filter((k) => data.latest[k]);
  root.innerHTML = `<div class="btn-row wide page-actions">
      <button type="button" class="btn primary" data-new>${icon("plus")} Messung eintragen</button>
      ${data.ai_available ? `<button type="button" class="btn tonal" data-scan>${icon("camera")} ViCoach-Screenshot</button>` : ""}
    </div>
    ${pend.length ? `<p class="callout">${pend.length} Messung(en) warten auf Verbindung.</p>` : ""}
    ${verdictCard()}
    ${tipsCard()}
    ${hasAny ? `<div class="body-grid">${MAIN.map(tile).join("")}</div>` : `<div class="card quiet"><p>
      Noch keine Messung. Trag Gewicht und Umfänge ein – oder lies das Ergebnis deiner
      Körperanalyse im Studio per Screenshot ein.</p></div>`}
    ${girths.length ? `<h3 class="group-title">Umfänge</h3><div class="list">${girths.map((k) => `
      <div class="row-item"><div class="row-main"><div class="row-title">${esc(data.labels[k])}</div>
        <div class="row-meta"><span>${dm(data.latest[k].date)}</span></div></div>
        <span class="row-aside"><b>${fmt(data.latest[k].value, k)}</b></span></div>`).join("")}</div>` : ""}
    ${data.entries.filter((e) => !isPendingDelete(`measurements/${e.id}`)).length ? `<details class="group-details"><summary class="group-title">Alle Messungen
        <span class="count-muted">${data.entries.filter((e) => !isPendingDelete(`measurements/${e.id}`)).length}</span>${icon("down", "chev")}</summary>
      <div class="list">${data.entries.filter((e) => !isPendingDelete(`measurements/${e.id}`)).map((e) => `<div class="row-item" role="button" tabindex="0" data-entry="${e.id}">
        <div class="row-main"><div class="row-title">${dm(e.date)}${e.source === "vicoach" ? " · ViCoach" : ""}</div>
          <div class="row-meta">${[...MAIN, ...GIRTHS].filter((k) => e[k] != null).slice(0, 4)
            .map((k) => `<span>${esc(data.labels[k])} ${fmt(e[k], k)}</span>`).join("")}</div></div>
        ${icon("right", "chev")}</div>`).join("")}</div></details>` : ""}
    <p class="hint pad">Körperfett aus Umfängen ist eine grobe Schätzung (US-Navy-Formel, braucht
      Größe und Geschlecht im Profil). Genauer ist eine Messung im Studio.</p>`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(4);
  data = await api("measurements");
  render();
  if (tipsOutdated()) fetchTips();
}

// ------------------------------------------------------------- Formular
function field(key, value) {
  return `<label class="field">${esc(data.labels[key])}${UNITS[key] ? ` (${UNITS[key]})` : ""}
    <input name="${key}" type="number" inputmode="decimal" step="0.1" min="0" value="${value ?? ""}"></label>`;
}

function openForm({ values = {}, date = null, source = "manuell", dropped = [] } = {}) {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const scanned = source === "vicoach";
  const pairs = (keys) => keys.reduce((out, k, i) => (i % 2 ? out : [...out, keys.slice(i, i + 2)]), [])
    .map((pair) => `<div class="field-row">${pair.map((k) => field(k, values[k])).join("")}</div>`).join("");
  const sheet = openSheet({
    title: scanned ? "Messwerte prüfen" : "Messung eintragen",
    body: `<form id="measureForm" class="form">
      ${scanned ? `<p class="callout">${icon("check")} Aus dem Screenshot gelesen – bitte mit dem Original vergleichen.
        ${dropped.length ? `Unplausibel und weggelassen: ${esc(dropped.join(", "))}.` : ""}</p>` : ""}
      <label class="field">Datum<input name="date" type="date" required value="${date || iso}" max="${iso}"></label>
      <h3 class="form-section">Körperanalyse</h3>
      ${pairs(MAIN)}
      <details class="field-more" ${GIRTHS.some((k) => values[k] != null) || !scanned ? "open" : ""}>
        <summary>Umfänge</summary>
        ${pairs(GIRTHS)}
        <p class="hint">Taille auf Nabelhöhe, Hals unter dem Kehlkopf, Hüfte an der breitesten Stelle.</p>
      </details>
      <input type="hidden" name="source" value="${source}">
    </form>`,
    footer: `<span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="submit" class="btn primary" form="measureForm">Speichern</button>`,
  });
  const form = $("#measureForm", sheet);
  submitOnce(form, async () => {
    try {
      const body = { ...formData(form), profile_id: getProfile(), client_id: outbox.uuid() };
      const res = await outbox.send({ path: "measurements", body });
      closeSheet();
      toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist" : "Messung gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { formError(form, e); }
  });
}

/** Bild verkleinern (max. 1600 px, JPEG) – spart Datenvolumen und bleibt gut lesbar. */
function shrink(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85).split(",")[1]);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Bild konnte nicht gelesen werden.")); };
    img.src = url;
  });
}

function openScan() {
  const sheet = openSheet({
    title: "ViCoach-Screenshot einlesen",
    body: `<p class="hint">Mach in der ViCoach-App einen Screenshot vom Messergebnis (mescan/bodyscan)
        und wähle ihn hier aus. Die KI liest die Werte, du prüfst sie vor dem Speichern.</p>
      <label class="btn primary file-btn">${icon("camera")} Bild auswählen
        <input type="file" accept="image/*" data-file hidden></label>
      <p class="hint" data-status role="status"></p>
      <p class="hint">Das Bild wird zum Auslesen an die KI (Anthropic) geschickt und nicht gespeichert.</p>`,
  });
  const input = $("[data-file]", sheet);
  const status = $("[data-status]", sheet);
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const label = input.closest("label");
    label.classList.add("disabled");
    status.textContent = "Werte werden gelesen … (dauert ein paar Sekunden)";
    try {
      const image = await shrink(file);
      const res = await api("measurements/scan", { method: "POST", body: { image, media_type: "image/jpeg" }, timeout: 65000 });
      closeSheet(false, { replacing: true });
      openForm({ values: res.values, date: res.date, source: "vicoach", dropped: res.dropped });
    } catch (e) {
      status.textContent = e.offline ? "Zum Einlesen braucht es eine Verbindung." : e.message;
      label.classList.remove("disabled");
      input.value = "";
    }
  });
}

function openEntry(id) {
  const e = data.entries.find((x) => x.id === id);
  if (!e) return;
  const rows = [...MAIN, ...GIRTHS].filter((k) => e[k] != null);
  const sheet = openSheet({
    title: `Messung vom ${dm(e.date)}`,
    body: `<div class="list">${rows.map((k) => `<div class="row-item"><div class="row-main">
      <div class="row-title">${esc(data.labels[k])}</div></div>
      <span class="row-aside">${fmt(e[k], k)}${k === "body_fat_pct" && e.body_fat_estimated ? " (geschätzt)" : ""}</span></div>`).join("")}</div>
      <p class="hint">Quelle: ${e.source === "vicoach" ? "ViCoach" : "manuell"}</p>`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span><button type="button" class="btn ghost" data-close>Schließen</button>`,
  });
  armDelete($("[data-delete]", sheet), () => {
    closeSheet();
    deleteLater(`measurements/${id}`, "Messung gelöscht", { onDone: () => { data = null; } });
  }, "Löschen", "Messung löschen? (Ihr Gewicht verschwindet dann auch aus dem Gewichtsverlauf.)");
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-new]")) return openForm();
    if (ev.target.closest("[data-scan]")) return openScan();
    if (ev.target.closest("[data-tips]")) return fetchTips();
    const row = ev.target.closest("[data-entry]");
    if (row) openEntry(Number(row.dataset.entry));
  });
}
