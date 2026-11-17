/** Ernährung: Tagesbilanz, Mahlzeiten, Lebensmittel suchen/scannen/anlegen. */
import { $, actionMenu, api, armDelete, closeSheet, deleteLater, entryDay, esc, formData, formError, getProfile, icon,
  isPendingDelete, openSheet, patch, prefs, skeleton, skeletonCards, submitOnce, toast, busy, parseYmd } from "./util.js";
import * as outbox from "./outbox.js";
import { scanBarcode } from "./scanner.js";

let root;
let data = null;
let week = null;   // Wochenbilanz, kommt nach dem Tag und darf fehlen (offline)
let day = null;   // YYYY-MM-DD, null = heute
let fast = null;   // Intervallfasten (GET /api/fasting); darf fehlen (offline, Fasten aus)
let fastRec = null;   // GET /api/fasting/recommendation (Sperren, Protokolle); darf fehlen
let fastRecProfile = null;   // zu welchem Profil fastRec gehoert (Profilwechsel + offline: nicht fremde Sperren zeigen)
let fastTimer = null;
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist
let view = prefs.get("nutriView", "essen") === "fasten" ? "fasten" : "essen";   // Segment "Essen | Fasten"
let weekPending = true;   // Wochenkarte noch unterwegs: Platzhalter statt Luecke

// Der Pi wartet bei Open Food Facts bis zu 12 s je Abruf (app/nutrition.py) – das
// Standard-Zeitlimit von api() wuerde vorher aufgeben und "offline" melden.
const OFF_TIMEOUT = 30000;
const MEAL_ICON = { fruehstueck: "clock", mittag: "food", abend: "home", snack: "bag" };
const MEALS = { fruehstueck: "Frühstück", mittag: "Mittagessen", abend: "Abendessen", snack: "Snacks" };
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayIso = () => iso(new Date());
const num = (v, digits = 0) => (v == null ? "–" : Number(v).toLocaleString("de-AT", { maximumFractionDigits: digits }));

// ------------------------------------------------------------- Anzeige
export function ring(value, goal, { size = 132, label = "kcal" } = {}) {
  const pct = goal ? Math.min(value / goal, 1) : 0;
  const over = goal && value > goal;
  const r = 52;
  const c = 2 * Math.PI * r;
  return `<svg class="ring-svg" viewBox="0 0 120 120" width="${size}" height="${size}" role="img"
      aria-label="${num(value)} von ${num(goal)} ${label}">
    <circle cx="60" cy="60" r="${r}" class="ring-track"/>
    ${pct > 0 ? `<circle cx="60" cy="60" r="${r}" class="ring-fill ${over ? "over" : ""}"
      stroke-dasharray="${(pct * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 60 60)"/>` : ""}
    <text x="60" y="56" text-anchor="middle" class="ring-value">${goal ? num(Math.abs(goal - value)) : num(value)}</text>
    <text x="60" y="74" text-anchor="middle" class="ring-label">${goal ? (over ? "zu viel" : "übrig") : label}</text>
  </svg>`;
}

/** Kalorienring als Hero: die Mitte zeigt, was noch uebrig ist (bei 0 gegessen also das ganze Ziel);
 *  ohne Ziel die bisher gegessenen kcal. Der Track bleibt auch bei 0 sichtbar. */
function heroRing(eaten, budget, size = 104) {
  const pct = budget ? Math.min(eaten / budget, 1) : 0;
  const over = budget && eaten > budget;
  const r = 52, c = 2 * Math.PI * r;
  const value = budget ? num(Math.abs(budget - eaten)) : num(eaten);
  const label = budget ? (over ? "kcal zu viel" : "kcal übrig") : "kcal gegessen";
  return `<svg class="ring-svg nutri-ring" viewBox="0 0 120 120" width="${size}" height="${size}" role="img"
      aria-label="${value} ${label}${budget ? `, Ziel ${num(budget)} kcal` : ""}">
    <circle cx="60" cy="60" r="${r}" class="ring-track"/>
    ${pct > 0 ? `<circle cx="60" cy="60" r="${r}" class="ring-fill ${over ? "over" : ""}"
      stroke-dasharray="${(pct * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 60 60)"/>` : ""}
    <text x="60" y="58" text-anchor="middle" class="ring-value">${value}</text>
    <text x="60" y="76" text-anchor="middle" class="ring-label">${label}</text>
  </svg>`;
}

/** Makrozeile im selben Muster fuer Eiweiss, Kohlenhydrate, Fett. Der Balken ist ein <progress>
 *  (Fuellstand per Attribut, nicht per style); die Farbe kommt ueber die Klasse is-<key>. */
function macro(key, label, value, goal, { lead = false } = {}) {
  const pct = goal ? Math.min(100, Math.round((value / goal) * 100)) : 0;
  return `<div class="nm is-${key}${lead ? " lead" : ""}">
    <span class="nm-name"><i aria-hidden="true"></i>${label}</span>
    <progress class="nutri-bar" max="100" value="${pct}" aria-label="${label}: ${num(value)}${goal ? ` von ${num(goal)}` : ""} g"></progress>
    <span class="nm-val"><b>${num(value)}</b>${goal ? ` / ${num(goal)}` : ""} g</span></div>`;
}

// ------------------------------------------------------- Naehrwerte im Detail
// [Schluessel, Bezeichnung, Einheit, eingerueckt ("davon"), Nachkommastellen]
const NUTRIENT_ROWS = [["kcal", "Kalorien", "kcal", false, 0], ["protein", "Eiweiß", "g", false, 1],
  ["carbs", "Kohlenhydrate", "g", false, 1], ["sugar", "davon Zucker", "g", true, 1],
  ["fiber", "Ballaststoffe", "g", false, 1], ["fat", "Fett", "g", false, 1],
  ["sat_fat", "davon gesättigt", "g", true, 1], ["salt", "Salz", "g", false, 2]];
const DETAILS = ["fiber", "sugar", "sat_fat", "salt"];

/** Summen ueber Eintraege. missing: Eintraege, die einen Detail-Wert nicht kennen (NULL). */
function sumEntries(entries) {
  const out = { missing: {} };
  for (const [k] of NUTRIENT_ROWS) out[k] = entries.reduce((n, e) => n + (e[k] || 0), 0);
  for (const k of DETAILS) out.missing[k] = entries.filter((e) => e[k] == null).length;
  return out;
}

/** Ziel je Naehrwert: Makros aus den Tageszielen, die Details als Richtwert (min/max). */
function targets(goals) {
  const g = goals.goals || {};
  const out = {};
  for (const k of ["kcal", "protein", "carbs", "fat"]) if (g[k]) out[k] = { value: g[k], kind: "goal" };
  return { ...out, ...(goals.reference || {}) };
}

/** Zeilen fuer das Akkordion. Ohne Ziele (Mahlzeit, Eintrag) nur die Werte. Kennt ein Teil der
 *  Eintraege einen Wert nicht, steht "mind." davor – die Summe ist dann eine Untergrenze. */
function nutrientRows(t, goal = {}, count = null) {
  return `<div class="nutri-rows">${NUTRIENT_ROWS.map(([k, label, unit, sub, digits]) => {
    const v = t[k] || 0;
    const missing = t.missing?.[k] || 0;
    const known = count == null || missing < count;
    const ref = goal[k];
    const pct = ref?.value ? Math.min(100, (v / ref.value) * 100) : 0;
    const state = !ref || !known ? "" : ref.kind === "max" ? (v > ref.value ? "over" : "")
      : ref.kind === "min" ? (v >= ref.value ? "reached" : "") : v > ref.value * 1.05 ? "over" : "";
    const value = !known ? "–" : `${missing ? "mind. " : ""}${num(v, digits)}`;
    const refText = ref ? ` <small>${ref.kind === "max" ? "max. " : ref.kind === "min" ? "mind. " : "/ "}${num(ref.value)}</small>` : "";
    return `<div class="nutri-row ${sub ? "sub" : ""} ${state}">
      <span>${label}</span>
      ${ref ? `<progress class="nutri-bar" max="100" value="${known ? Math.round(pct) : 0}" aria-hidden="true"></progress>` : "<span></span>"}
      <span class="macro-val">${value}${known ? ` ${unit}` : ""}${refText}</span></div>`;
  }).join("")}</div>`;
}

function incomplete(t, count) {
  const gaps = DETAILS.filter((k) => t.missing[k] > 0 && t.missing[k] < count);
  const words = { fiber: "Ballaststoffen", sugar: "Zucker", sat_fat: "gesättigten Fetten", salt: "Salz" };
  return gaps.length ? `<p class="hint">Nicht alle Lebensmittel haben Angaben zu ${gaps.map((k) => words[k]).join(", ")
    .replace(/, ([^,]+)$/, " und $1")} – „mind.“ heißt, es kann mehr sein.</p>` : "";
}

/** Ausklappbar; offen/zu bleibt je Stelle gemerkt (Tag, jede Mahlzeit). */
function detailsBox(key, summary, inner) {
  const open = prefs.get(`nutriOpen.${key}`) ? "open" : "";
  return `<details class="nutri-details" data-nutri="${key}" ${open}>
    <summary>${summary}${icon("down", "chev")}</summary>${inner}</details>`;
}

function pendingEntries() {
  return outbox.pending("food-log").filter((e) => (e.date || todayIso()) === data.date);
}

function pendingWater() {
  return outbox.pending("water-log").filter((e) => (e.date || todayIso()) === data.date)
    .reduce((n, e) => n + e.ml, 0);
}

// ------------------------------------------------------------- Wasser
const liters = (ml) => (ml / 1000).toLocaleString("de-AT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const WATER_QUICK = [250, 500];

/** Wasser als eine Zeile: Stand, Balken, zwei Schnelltasten. Das Minus korrigiert einen Fehltipp. */
function waterRow(ml, goal) {
  const pct = goal ? Math.min(100, Math.round((ml / goal) * 100)) : 0;
  return `<div class="card water-row">
    ${icon("water")}
    <div class="water-info"><span><b>${liters(ml)}</b>${goal ? ` von ${liters(goal)}` : ""} L Wasser</span>
      ${goal ? `<progress class="nutri-bar is-water" max="100" value="${pct}" aria-label="${pct} % des Trinkziels"></progress>` : ""}</div>
    <div class="water-quick">
      ${WATER_QUICK.map((v) => `<button type="button" class="btn small tonal" data-water="${v}">+${num(v)}</button>`).join("")}
      <button type="button" class="icon-btn" data-water="-250" aria-label="250 ml abziehen">${icon("minus")}</button>
    </div>
  </div>`;
}

const DAY_TYPE = { training: "Trainingstag", rest: "Ruhetag" };

/** Eine Zeile unter dem Ring: Trainings-/Ruhetag und was noch fehlt. */
function feedbackLine() {
  const parts = [DAY_TYPE[data.goals.day_type], ...(data.feedback || []).map((f) => f.text)].filter(Boolean);
  const warn = (data.feedback || []).some((f) => f.tone === "warn");
  return parts.length ? `<p class="hint nutri-line${warn ? " warn" : ""}">${esc(parts.join(" · "))}</p>` : "";
}

const WEEKDAY = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

/** Die letzten sieben Tage als Band: Haken = Kalorienziel getroffen, Ring = heute.
 *  Der Korridor (+-10 %) spiegelt app/nutrition.py KCAL_CORRIDOR; der Server liefert pro Tag kein Flag. */
const WEEK_CORRIDOR = 0.10;

function weekCard() {
  if (!week) return "";
  const today = todayIso();
  const days = week.days.map((d) => {
    const label = WEEKDAY[parseYmd(d.date).getDay()];
    const hit = d.logged && !d.open && d.goal_kcal && Math.abs(d.kcal - d.goal_kcal) <= d.goal_kcal * WEEK_CORRIDOR;
    const state = hit ? "hit" : d.logged && !d.open ? "miss" : "";
    const what = d.logged ? `${num(d.kcal)} kcal${hit ? ", Ziel getroffen" : ""}` : "nichts eingetragen";
    return `<div class="d ${state}${d.date === today ? " today" : ""}" role="img" aria-label="${esc(`${label}: ${what}`)}">
      <i>${hit ? icon("check") : ""}</i>
      <span>${label}${d.training ? ` <i class="wk-dot" aria-label="Trainingstag"></i>` : ""}</span></div>`;
  }).join("");
  const none = !week.days.some((d) => d.logged);
  return `<div class="card nutri-week"><div class="card-head">${icon("progress")}<div class="card-title">Letzte 7 Tage</div>
      <span class="aside">${week.avg_kcal === null ? "noch keine Bilanz" : `Ø ${num(week.avg_kcal)} kcal`}</span></div>
    ${none ? `<div class="empty"><p class="empty-title">Noch nichts eingetragen</p><p>Sobald du Mahlzeiten einträgst, siehst du hier, an welchen Tagen du dein Ziel getroffen hast.</p></div>`
      : `<div class="week-band">${days}</div>${week.text.map((t) => `<p class="hint">${esc(t)}</p>`).join("")}`}</div>`;
}

// ------------------------------------------------------------- Fasten
const clock = (d) => d.toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });
const hoursText = (h) => { const m = Math.max(0, Math.round(h * 60)); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
// Der Server wartet bei Claude bis zu 90 s (app/fasting_advice.py TIMEOUT) – das Standard-Zeitlimit
// von api() meldete vorher "offline".
const ADVICE_TIMEOUT = 100000;
const FAST_LOG_ROWS = 4;
const FAST_DOCTOR = "Sprich bitte mit deiner Ärztin oder deinem Arzt, bevor du Intervallfasten ausprobierst.";

const kindLabel = (kind, hours) => (kind === "custom" ? `${num(hours, 1)} h` : kind || "");

// ---- Phasentext: feste Texte, ohne Stundenversprechen ("ab Stunde X passiert Y"), offline lauffaehig
const PHASE_TIPS = [
  [0.15, "Gerade gestartet. Wasser, Tee oder Kaffee ohne Zucker und Milch sind im Fasten in Ordnung."],
  [0.4, "Hunger kommt in Wellen und geht oft nach einigen Minuten wieder vorbei. Trink etwas und lenk dich kurz ab."],
  [0.75, "Du bist mittendrin. Plane die erste Mahlzeit schon jetzt: Eiweiß und Gemüse sind ein guter Start."],
  [1, "Bald geschafft. Trink genug und halte die erste Mahlzeit bereit."],
];
const PHASE_LONG_NOTE = " Bei Schwindel oder Zittern brich ab und iss etwas.";
const PHASE_DONE = "Ziel erreicht. Du kannst jetzt essen – starte mit Eiweiß und Gemüse und iss langsam. Länger zu fasten bringt keinen Pflichtbonus.";
const PHASE_OVER = "Du bist über dein Ziel hinaus. Länger durchzuhalten bringt nichts Zusätzliches – iss etwas, vor allem bei Schwindel, Zittern oder Übelkeit.";
const OVER_HOURS = 6;     // so weit ueber dem Ziel kippt der Text von "geschafft" zu "iss etwas"
const LONG_TARGET = 18;   // ab hier der Zusatz zu Schwindel und Abbruch

/** Kurzer Hinweis zum Stand des laufenden Fastens: nur Fortschritt (Anteil am Ziel) und Alltagstipps. */
export function phaseTip(hours, target) {
  if (!(hours >= 0) || !(target > 0)) return "";
  if (hours >= target + OVER_HOURS) return PHASE_OVER;
  if (hours >= target) return PHASE_DONE;
  const text = PHASE_TIPS.find(([upto]) => hours / target < upto)[1];
  return target >= LONG_TARGET ? text + PHASE_LONG_NOTE : text;
}

/** Die letzten sieben Kalendertage bis `today`: Tag im Ziel = mindestens ein beendetes Fasten mit
 *  erreichtem Ziel (h.day = Tag des Endes, h.hit vom Server). */
export function weekBand(history, today = new Date()) {
  const list = history || [];
  const hit = new Set(list.filter((h) => h.hit).map((h) => h.day));
  const fasted = new Set(list.map((h) => h.day));
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    const key = iso(d);
    days.push({ date: key, label: WEEKDAY[d.getDay()], hit: hit.has(key), fasted: fasted.has(key), today: i === 0 });
  }
  return { days, hits: days.filter((d) => d.hit).length };
}

/** Laufendes Fasten: Stand vom Server, korrigiert um das, was noch im Ausgang liegt (offline
 *  gestartet oder beendet). Der Timer rechnet aus der Startzeit, nicht aus einem Zaehler. */
function runningFast() {
  const stopped = new Set(outbox.pending("fasting/stop").map((e) => e.client_id));
  const srv = fast?.running;
  if (srv) return stopped.has(srv.client_id) ? null : srv;
  const start = outbox.pending("fasting/start").filter((e) => !stopped.has(e.client_id)).pop();
  return start ? { client_id: start.client_id, started_at: start.started_at, target_hours: fast?.target_hours || 16 } : null;
}

/** Ende des letzten Fastens (auch ein noch nicht gesendetes) – Beginn des Essensfensters. */
function lastFastEnd() {
  const queued = outbox.pending("fasting/stop").map((e) => e.ended_at).filter(Boolean).sort().pop();
  return queued || fast?.last_ended_at || null;
}

/** Empfehlung sagt "nicht empfohlen" (Alter, BMI, Selbstauskunft). Ohne geladene Empfehlung (offline)
 *  gilt es als erlaubt – der Server prueft beim Start trotzdem (409). */
const fastBlocked = () => fastRec?.allowed === false && fastRecProfile === getProfile();

function blockedHtml(rec) {
  return `${(rec.blocked || []).map((b) => `<div class="callout warn">${icon("warn")}<span>${esc(b.text)}</span></div>`).join("")}
    <p class="hint">${esc(FAST_DOCTOR)}</p>`;
}

function fastRing(hours, target) {
  const r = 52, c = 2 * Math.PI * r, pct = Math.min(hours / target, 1);
  return `<svg class="ring-svg" viewBox="0 0 120 120" width="132" height="132" role="img"
      aria-label="${hoursText(hours)} von ${num(target, 1)} Stunden gefastet">
    <circle cx="60" cy="60" r="${r}" class="ring-track"/>
    ${pct > 0 ? `<circle cx="60" cy="60" r="${r}" class="ring-fill" stroke-dasharray="${(pct * c).toFixed(1)} ${c.toFixed(1)}"
      transform="rotate(-90 60 60)"/>` : ""}
    <text x="60" y="56" text-anchor="middle" class="ring-value">${hoursText(hours)}</text>
    <text x="60" y="74" text-anchor="middle" class="ring-label">von ${num(target, 1)} h</text>
  </svg>`;
}

/** Wochenband "x von 7 Tagen im Ziel" und die letzten Fasten als Liste – ohne Inline-Styles
 *  (der Fuellstand ist ein <progress>). */
function fastHistory() {
  const items = fast.history || [];
  if (!items.length) return "";
  const { days, hits } = weekBand(items);
  const band = days.map((d) => {
    const what = d.hit ? "Ziel erreicht" : d.fasted ? "Ziel nicht erreicht" : "kein Fasten";
    return `<div class="d${d.hit ? " hit" : d.fasted ? " miss" : ""}${d.today ? " today" : ""}" role="img" aria-label="${esc(`${d.label}: ${what}`)}">
      <i>${d.hit ? icon("check") : ""}</i><span>${d.label}</span></div>`;
  }).join("");
  const rows = items.slice(0, FAST_LOG_ROWS).map((h) => {
    const day = parseYmd(h.day);
    return `<li class="fast-log-row${h.hit ? "" : " is-open"}">
      <span class="fast-log-day">${WEEKDAY[day.getDay()]} ${day.getDate()}.${day.getMonth() + 1}.</span>
      <span class="fast-log-val"><b>${hoursText(h.hours)}</b> von ${num(h.target_hours, 1)} h</span>
      <progress class="nutri-bar" max="${h.target_hours}" value="${Math.min(h.hours, h.target_hours)}"
        aria-label="${esc(`${hoursText(h.hours)} von ${num(h.target_hours, 1)} Stunden`)}"></progress></li>`;
  }).join("");
  return `<div class="fast-week">
      <p class="fast-score"><b>${hits} von 7</b> Tagen im Ziel${fast.streak ? ` · Serie: ${fast.streak} ${fast.streak === 1 ? "Tag" : "Tage"}` : ""}</p>
      <div class="week-band" role="group" aria-label="${hits} von 7 Tagen im Ziel">${band}</div></div>
    <h3 class="form-section">Letzte Fasten</h3>
    <ul class="fast-log">${rows}</ul>`;
}

/** Fasten-Karte. Ohne Fastenart (und ohne laufendes Fasten) nur der leise Einstieg zum Assistenten. */
function fastCard() {
  if (!fast) return "";
  const run = runningFast();
  if (!fast.kind && !run) {
    if (fastBlocked()) {
      return `<div class="card fast-card" data-fast-card><div class="card-head">${icon("clock")}<span class="card-title">Fasten</span></div>
        ${blockedHtml(fastRec)}
        <div class="btn-row"><button type="button" class="btn primary" data-fast-assist>Angaben prüfen</button></div></div>`;
    }
    return `<div class="card quiet fast-card fast-entry" data-fast-card>
      <span>${icon("clock")} Intervallfasten ausprobieren? Der Assistent fragt kurz nach und schlägt eine Fastenart vor, die zu dir passt.</span>
      <span class="fast-entry-actions"><button type="button" class="btn tonal" data-fast-assist>Fastenart finden</button></span></div>`;
  }
  const target = run?.target_hours || fast.target_hours || 16;
  let body;
  if (run) {
    const start = new Date(run.started_at);
    const hours = (Date.now() - start) / 3600000;
    const endAt = new Date(start.getTime() + target * 3600000);
    body = `<div class="fast-main">${fastRing(hours, target)}
      <div class="fast-info"><b>${hours >= target ? "Ziel erreicht" : `Ende um ${clock(endAt)}`}</b>
        <span>seit ${clock(start)}</span></div></div>
      <div class="callout fast-phase" data-fast-phase>${icon("spark")}<span>${esc(phaseTip(hours, target))}</span></div>
      <div class="btn-row">
        <button type="button" class="btn primary" data-fast-stop><span>${icon("check")}</span> Fasten beenden</button>
        ${fast.running && !outbox.pending("fasting/stop").length ? `<button type="button" class="btn ghost" data-fast-edit>Startzeit ändern</button>` : ""}
      </div>`;
  } else if (fastBlocked()) {
    body = `${blockedHtml(fastRec)}
      <div class="btn-row"><button type="button" class="btn primary" data-fast-assist>Angaben prüfen</button></div>`;
  } else {
    const end = lastFastEnd();
    const left = end ? (new Date(end).getTime() + (24 - target) * 3600000 - Date.now()) / 3600000 : null;
    const window = left === null ? "Noch kein Fasten aufgezeichnet."
      : left > 0 ? `Essensfenster noch ${hoursText(left)} h offen.` : "Das Essensfenster ist vorbei – Zeit zum Fasten.";
    body = `<p class="hint" data-fast-window>${esc(window)}</p>
      <div class="btn-row"><button type="button" class="btn primary" data-fast-start>Fasten starten</button>
        <button type="button" class="btn ghost" data-fast-assist>Fastenart ändern</button></div>`;
  }
  return `<div class="card fast-card" data-fast-card>
    <div class="card-head">${icon("clock")}<span class="card-title">Fasten</span>
      <span class="aside">${esc(kindLabel(fast.kind, fast.target_hours))}</span></div>
    ${body}
    ${fast.hint ? `<p class="hint">${esc(fast.hint)}</p>` : ""}
    ${fastHistory()}</div>`;
}

// ---- Persoenliche Tipps: feste Tipps sofort, Claude-Text auf Knopfdruck. Eigene Karte mit eigenem
// Zustand, damit der Minuten-Timer (tickFast) und render() sie nicht zuruecksetzen.
const fastTips = { kind: null, fixed: null, advice: null, blocked: "", note: "", loading: false };

function fastTipsVisible() {
  return Boolean(fast && view === "fasten" && (fast.kind || runningFast()) && !fastBlocked());
}

function fastTipsCard() {
  if (!fastTipsVisible()) return "";
  const t = fastTips;
  const list = (items) => `<ul class="plain">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  let body;
  if (t.blocked) body = `<div class="callout warn">${icon("warn")}<span>${esc(t.blocked)}</span></div>`;
  else if (t.advice) {
    body = `<p>${esc(t.advice.erklaerung)}</p>${list(t.advice.tipps)}`
      + (t.advice.cached ? `<p class="hint">Gleiche Angaben wie vorhin – der Text ist höchstens 24 Stunden alt.</p>` : "");
  } else if (t.fixed?.tipps?.length) {
    body = list(t.fixed.tipps.slice(0, 3))
      + (t.fixed.evidenz ? `<div class="callout">${icon("warn")}<span>${esc(t.fixed.evidenz)}</span></div>` : "");
  } else body = `<p class="hint">Gerade sind keine festen Tipps geladen. Mit dem Knopf unten erstellt dein Coach persönliche Tipps.</p>`;
  return `<div class="card fast-tips" data-fast-tips>
    <div class="card-head">${icon("spark")}<span class="card-title">Persönliche Tipps</span></div>
    ${body}
    ${t.note ? `<p class="hint" role="status">${esc(t.note)}</p>` : ""}
    <div class="btn-row"><button type="button" class="btn tonal" data-fast-advice ${t.loading ? 'disabled aria-busy="true"' : ""}>
      ${t.loading ? "Dein Coach schreibt …" : t.advice ? "Erneut abrufen" : "Tipps für mich erstellen"}</button></div></div>`;
}

function paintFastTips() {
  if (view !== "fasten") return;
  const card = root?.querySelector("[data-fast-tips]");
  const html = fastTipsCard();
  if (card) card.outerHTML = html;
  else if (html) render();   // Karte war noch nicht da (erst jetzt Fastenart gewaehlt)
}

/** Feste Tipps der gewaehlten Fastenart (GET fasting/tips, ohne KI). Nur nachladen, wenn sich die Art aendert. */
async function loadFastTips() {
  const kind = fast?.kind ? `${getProfile()}:${fast.kind}` : null;   // je Profil und Art
  if (!kind || fastTips.kind === kind) return;
  fastTips.kind = kind;
  fastTips.advice = null;
  fastTips.blocked = "";
  try { fastTips.fixed = await api(`fasting/tips?kind=${encodeURIComponent(fast.kind)}`); }
  catch { fastTips.fixed = null; fastTips.kind = null; }   // offline: beim naechsten Laden nochmal
  paintFastTips();
}

const ADVICE_NOTES = {
  400: "Persönliche Tipps brauchen Claude auf dem Server – hier stehen die festen Tipps.",
  422: "Die Tipps konnten gerade nicht erstellt werden. Bitte später nochmal versuchen – bis dahin gelten die festen Tipps.",
  429: "Es werden gerade schon Tipps erstellt. Bitte einen Moment warten und dann nochmal tippen.",
};

async function requestAdvice() {
  if (fastTips.loading) return;
  Object.assign(fastTips, { loading: true, note: "", blocked: "" });
  paintFastTips();
  try {
    const res = await api("fasting/advice", { method: "POST", timeout: ADVICE_TIMEOUT });
    if (res.kind) fastTips.advice = res;
    else { fastTips.advice = null; fastTips.blocked = res.erklaerung; }   // Sperre: fester Text, keine KI
  } catch (e) {
    fastTips.advice = null;
    fastTips.note = e.offline ? "Offline – hier stehen die festen Tipps." : ADVICE_NOTES[e.status] || e.message;
  } finally {
    fastTips.loading = false;
    paintFastTips();
  }
}

/** Nur die Fasten-Karte und den Chip im Kopf neu zeichnen (Timer) – die ganze Seite neu aufzubauen
 *  wuerde Eingaben stoeren. */
function tickFast() {
  if (!root || document.hidden || !data) return;
  const chip = root.querySelector("[data-fast-chip]");
  if (chip) chip.outerHTML = fastChip();
  const card = root.querySelector("[data-fast-card]");
  if (!card) return;
  const html = fastCard();
  if (html) card.outerHTML = html;
}

/** Stand und Empfehlung vom Server holen, ohne zu zeichnen (load() zeichnet einmal fuer alles). */
async function fetchFast() {
  // Empfehlung (Sperren) ist optional: ohne sie laeuft die Karte wie bisher, der Server sperrt beim Start trotzdem.
  const [overview, rec] = await Promise.allSettled([api("fasting"), api("fasting/recommendation")]);
  if (overview.status === "fulfilled") fast = overview.value;
  if (rec.status === "fulfilled") { fastRec = rec.value; fastRecProfile = getProfile(); }
}

/** Nach einer Fasten-Aktion: neu holen und zeichnen. */
async function loadFast() {
  await fetchFast();
  if (data) render();
  loadFastTips();
}

/** Laufendes Fasten als kleiner Chip im Kopf der Essen-Ansicht – ohne die Hoehe zu aendern. */
function fastChip() {
  const run = runningFast();
  if (!run) return `<span data-fast-chip hidden></span>`;
  const hours = (Date.now() - new Date(run.started_at)) / 3600000;
  return `<button type="button" class="chip-btn fast-chip" data-fast-chip data-view="fasten"
    aria-label="Fasten läuft seit ${hoursText(hours)} Stunden, zur Fasten-Ansicht">${icon("clock")} ${hoursText(hours)} h</button>`;
}

/** Uhrzeit aus einem time-Feld als ISO: spaeter als jetzt heisst gestern (ueber Mitternacht gefastet). */
function timeToIso(value) {
  const [h, m] = value.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  if (d.getTime() > Date.now() + 60000) d.setDate(d.getDate() - 1);
  return d.toISOString();
}

function openFastStart() {
  if (!fast?.kind || fastBlocked()) {
    // Veralteter Zustand (Karte bietet den Start dann nicht an): zum Assistenten statt in ein Formular ohne Art
    const blocked = fastBlocked();
    const sheet = openSheet({ title: "Fasten starten",
      body: blocked ? blockedHtml(fastRec) : `<p>Du hast noch keine Fastenart gewählt.</p>
        <p class="hint">Der Assistent fragt kurz nach und schlägt dir eine Art vor, die zu dir passt.</p>`,
      footer: `<button class="btn primary" type="button" data-assist>${blocked ? "Angaben prüfen" : "Welche Fastenart passt zu mir?"}</button>` });
    sheet.addEventListener("click", (ev) => { if (ev.target.closest("[data-assist]")) openFastAssistant({ onDone: loadFast }); });
    return;
  }
  const now = clock(new Date());
  openSheet({ title: "Fasten starten", body: `<form id="fastForm">
      <label class="field"><span>Startzeit</span>
        <input type="time" name="time" value="${now}" required></label>
      <p class="hint">Jetzt, oder die Uhrzeit, ab der du nichts mehr gegessen hast.</p></form>`,
    footer: `<button class="btn primary" type="submit" form="fastForm">Starten</button>` });
  const form = $("#fastForm");
  submitOnce(form, async () => {
    try {
      const res = await outbox.send({ path: "fasting/start", body: {
        profile_id: getProfile(), client_id: outbox.uuid(), started_at: timeToIso(formData(form).time) } });
      closeSheet();
      if (res.queued) toast("Fasten gestartet – wird gesendet, sobald wieder Verbindung da ist");
      await loadFast();
    } catch (e) {
      formError(form, e);
      if (e.status === 409) loadFast();   // Sperre oder zweites Geraet hat schon gestartet
    }
  });
}

async function stopFast() {
  const run = runningFast();
  if (!run) return;
  try {
    const res = await outbox.send({ path: "fasting/stop", body: {
      profile_id: getProfile(), client_id: run.client_id, ended_at: new Date().toISOString() } });
    if (res.queued) toast("Fasten beendet – wird gesendet, sobald wieder Verbindung da ist");
    await loadFast();
  } catch (e) {
    toast(e.message, { error: true });
    loadFast();
  }
}

function openFastEdit() {
  const run = fast?.running;
  if (!run) return;
  openSheet({ title: "Startzeit ändern", body: `<form id="fastForm">
      <label class="field"><span>Startzeit</span>
        <input type="time" name="time" value="${clock(new Date(run.started_at))}" required></label></form>`,
    footer: `<button class="btn primary" type="submit" form="fastForm">Speichern</button>` });
  const form = $("#fastForm");
  submitOnce(form, async () => {
    try {
      await api(`fasting/${run.id}`, { method: "PATCH", body: { started_at: timeToIso(formData(form).time) } });
      closeSheet();
      await loadFast();
    } catch (e) { formError(form, e); }
  });
}

// ---- Fastenart-Assistent: Kurz-Check -> Essensfenster -> Empfehlung. Die Regeln liegen auf dem Server
// (app/fasting_advice.py); hier wird nur gefragt, angezeigt und gespeichert.
const FLAG_LABELS = {
  pregnant: "Ich bin schwanger oder stille",
  eating_disorder: "Ich habe oder hatte eine Essstörung",
  diabetes_meds: "Ich nehme Medikamente gegen Diabetes (z. B. Insulin)",
};
const WINDOW_CHOICES = [
  ["morgens", "Eher früh", "Frühstück und Mittag, abends nichts mehr"],
  ["abends", "Eher spät", "Kein Frühstück, dafür bis in den Abend"],
  ["egal", "Egal", "Passt sich meinem Alltag an"],
];
const CUSTOM_MIN = 1, CUSTOM_MAX = 48;

/** Fastenart-Assistent als Sheet. onDone: nach dem Uebernehmen oder Ausschalten (z. B. Karte neu laden). */
export async function openFastAssistant({ onDone } = {}) {
  let profile, rec;
  try {
    [profile, rec] = await Promise.all([api(`profiles/${getProfile()}`), api("fasting/recommendation")]);
  } catch (e) {
    return toast(e.offline ? "Der Assistent braucht eine Verbindung." : e.message, { error: true });
  }
  const st = { step: 0, profile, rec, flags: [...(profile.fasting_flags || [])], pref: profile.eating_window_pref || null,
    choice: null, hours: profile.fasting_hours || 16, busy: false };
  const sheet = openSheet({ title: "Welche Fastenart passt zu mir?", body: "", footer: " " });
  const body = $(".sheet-body", sheet);
  const foot = $(".sheet-foot", sheet);
  body.classList.add("fast-assist");   // Bereichs-CSS (fasten.css) nur fuer dieses Sheet

  const bar = () => `<div class="steps-bar" role="img" aria-label="Schritt ${st.step + 1} von 3">${
    [0, 1, 2].map((i) => `<i${i <= st.step ? ' class="on"' : ""}></i>`).join("")}</div>`;
  const protocol = (kind) => st.rec.protocols.find((p) => p.kind === kind);
  const chosenHours = () => (st.choice === "custom" ? Number(st.hours) : protocol(st.choice)?.hours);

  function paint() {
    if (st.step === 0) {
      body.innerHTML = `${bar()}<h3 class="form-section">Schritt 1 von 3 · Kurz-Check</h3>
        <p>Intervallfasten passt nicht zu jedem. Trifft etwas davon auf dich zu?</p>
        <div class="toggles fast-flags">${Object.entries(FLAG_LABELS).map(([k, l]) => `<label class="toggle">
          <input type="checkbox" data-flag="${k}" ${st.flags.includes(k) ? "checked" : ""}><span></span>${esc(l)}</label>`).join("")}</div>
        <p class="hint">Nichts davon? Dann einfach weiter. Die Angaben nutzt nur diese Empfehlung – sie gehen nicht an den Coach.</p>`;
      foot.innerHTML = `<button type="button" class="btn primary" data-next ${st.busy ? "disabled" : ""}>Weiter</button>`;
    } else if (st.step === 1) {
      body.innerHTML = `${bar()}<h3 class="form-section">Schritt 2 von 3 · Essensfenster</h3>
        <p>Wann isst du am liebsten?</p>
        <div class="choice-grid">${WINDOW_CHOICES.map(([k, label, text]) => `<button type="button" class="choice"
          data-pref="${k}" aria-pressed="${st.pref === k}"><b>${label}</b><small>${text}</small></button>`).join("")}</div>
        <p class="hint">Das ist nur ein Wunsch – die Empfehlung richtet sich nach deinem Fasten-Verlauf, deinem Ziel und deinen Angaben im Profil.</p>`;
      foot.innerHTML = `<button type="button" class="btn ghost" data-back>Zurück</button>
        <button type="button" class="btn primary" data-next ${st.busy ? "disabled" : ""}>Empfehlung ansehen</button>`;
    } else if (!st.rec.allowed) {
      body.innerHTML = `${bar()}<h3 class="form-section">Ergebnis</h3>
        <p><b>Intervallfasten wird für dich nicht empfohlen.</b></p>
        ${blockedHtml(st.rec)}<p class="hint">${esc(st.rec.disclaimer)}</p>`;
      foot.innerHTML = `<button type="button" class="btn ghost" data-back>Angaben ändern</button>
        ${st.profile.fasting_kind ? `<button type="button" class="btn" data-off>Fasten ausschalten</button>` : ""}
        <button type="button" class="btn primary" data-close>Schließen</button>`;
    } else paintResult();
    toTop();
  }

  function paintResult() {
    const rec = st.rec;
    const recP = protocol(rec.kind);
    const picked = protocol(st.choice);
    const hours = chosenHours();
    const over = recP && hours > recP.hours;
    const evidence = picked?.advanced && picked.evidenz && !rec.reasons.includes(picked.evidenz) ? picked.evidenz : null;
    const choice = (p) => `<button type="button" class="choice" data-kind="${p.kind}" aria-pressed="${st.choice === p.kind}">
        <b>${esc(p.kind)}</b><small>${p.hours} h fasten · ${p.eating_hours} h essen${p.kind === rec.kind ? " · empfohlen" : ""}${
      p.kind === rec.current_kind ? " · aktuell" : ""}${p.advanced ? " · fortgeschritten" : ""}</small></button>`;
    body.innerHTML = `${bar()}<h3 class="form-section">Ergebnis</h3>
      <div class="fast-result"><span class="eyebrow">Empfehlung</span><b class="fast-kind">${esc(rec.kind)}</b>
        <span class="hint">${recP.hours} h fasten · ${recP.eating_hours} h essen</span></div>
      <ul class="plain fast-reasons">${rec.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${rec.warnings.map((w) => `<div class="callout warn">${icon("warn")}<span>${esc(w)}</span></div>`).join("")}
      <h3 class="form-section">Andere Fastenart wählen</h3>
      <div class="choice-grid">${rec.protocols.map(choice).join("")}
        <button type="button" class="choice" data-kind="custom" aria-pressed="${st.choice === "custom"}"><b>Eigene</b>
          <small>Stunden selbst festlegen${rec.current_kind === "custom" ? " · aktuell" : ""}</small></button></div>
      ${st.choice === "custom" ? `<label class="field fast-custom">Stunden ohne Essen (${CUSTOM_MIN}–${CUSTOM_MAX})
        <input type="number" inputmode="decimal" min="${CUSTOM_MIN}" max="${CUSTOM_MAX}" step="0.5" value="${st.hours}" data-hours></label>` : ""}
      ${evidence ? `<div class="callout">${icon("warn")}<span>${esc(evidence)}</span></div>` : ""}
      ${over ? `<div class="callout warn">${icon("warn")}<span>Das liegt über der Empfehlung ${esc(rec.kind)}. Du kannst es wählen – ich empfehle es dir nicht.</span></div>` : ""}
      <p class="hint">${esc(rec.disclaimer)}</p>`;
    foot.innerHTML = `<button type="button" class="btn ghost" data-back>Zurück</button>
      <button type="button" class="btn primary" data-apply ${st.busy ? "disabled" : ""}>${esc(st.choice === "custom" ? "Eigene Art übernehmen" : `${st.choice} übernehmen`)}</button>`;
  }

  const toTop = () => body.scrollTo?.({ top: 0 });

  async function save(patch) {
    st.profile = await api(`profiles/${getProfile()}`, { method: "PATCH", body: patch });
    window.dispatchEvent(new CustomEvent("profile-changed", { detail: st.profile }));
  }
  async function guarded(fn) {
    if (st.busy) return;
    st.busy = true;
    paint();
    try { await fn(); } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
    finally { st.busy = false; if (sheet.isConnected) paint(); }
  }
  const sameList = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

  sheet.addEventListener("change", (ev) => {
    const f = ev.target.closest("[data-flag]");
    if (f) st.flags = f.checked ? [...new Set([...st.flags, f.dataset.flag])] : st.flags.filter((x) => x !== f.dataset.flag);
  });
  sheet.addEventListener("input", (ev) => {
    if (ev.target.matches("[data-hours]")) st.hours = ev.target.value;
  });
  sheet.addEventListener("click", (ev) => {
    const t = ev.target;
    const pref = t.closest("[data-pref]");
    if (pref) { st.pref = pref.dataset.pref; return paint(); }
    const kind = t.closest("[data-kind]");
    if (kind) { st.choice = kind.dataset.kind; return paint(); }
    // Mit Flag wurde das Essensfenster uebersprungen: Zurueck geht dann direkt zum Kurz-Check
    if (t.closest("[data-back]")) { st.step = st.step === 2 && st.flags.length ? 0 : st.step - 1; return paint(); }
    if (t.closest("[data-next]")) {
      return guarded(async () => {
        if (st.step === 0) {
          if (!sameList(st.flags, st.profile.fasting_flags || [])) await save({ fasting_flags: st.flags });
          if (st.flags.length) {          // Sperre: kein Essensfenster mehr abfragen
            st.rec = await api("fasting/recommendation");
            st.step = 2;
          } else st.step = 1;
        } else {
          if (st.pref && st.pref !== st.profile.eating_window_pref) await save({ eating_window_pref: st.pref });
          st.rec = await api("fasting/recommendation");
          st.step = 2;
        }
        if (st.rec.allowed && !st.choice) st.choice = st.rec.kind;
      });
    }
    if (t.closest("[data-off]")) {
      return guarded(async () => {
        await save({ fasting_kind: null });
        closeSheet();
        toast("Fasten ausgeschaltet");
        onDone?.();
      });
    }
    if (t.closest("[data-apply]")) {
      return guarded(async () => {
        const custom = st.choice === "custom";
        const hours = Number(st.hours);
        if (custom && !(hours >= CUSTOM_MIN && hours <= CUSTOM_MAX)) {
          throw new Error(`Bitte eine Stundenzahl zwischen ${CUSTOM_MIN} und ${CUSTOM_MAX} eintragen.`);
        }
        await save(custom ? { fasting_kind: "custom", fasting_hours: hours } : { fasting_kind: st.choice });
        closeSheet();
        toast(`Fastenart ${custom ? `${num(hours, 1)} h` : st.choice} gespeichert`);
        onDone?.();
      });
    }
  });
  paint();
}

const MEAL_SHORT = { fruehstueck: "Frühstück", mittag: "Mittag", abend: "Abend", snack: "Snack" };

/** Kopf der Essen-Ansicht: Datum und eine Zeile Bilanz. Klebt beim Scrollen unter der Leiste. */
function nutriHead(eaten, budget, g, t) {
  const isToday = data.date === todayIso();
  const dateLabel = isToday ? "Heute" : parseYmd(data.date).toLocaleDateString("de-AT", { weekday: "long", day: "numeric", month: "long" });
  return `<div class="nutri-head">
    <div class="date-switch">
      <button type="button" class="icon-btn" data-day="-1" aria-label="Vorheriger Tag">${icon("left")}</button>
      <b>${esc(dateLabel)}</b>
      ${fastChip()}
      <button type="button" class="icon-btn" data-day="1" aria-label="Nächster Tag" ${isToday ? "disabled" : ""}>${icon("right")}</button>
    </div>
    <p class="nutri-sum"><b>${num(eaten)}</b>${budget ? ` von ${num(budget)}` : ""} kcal · Eiweiß <b>${num(t.protein)}</b>${g.protein ? ` / ${num(g.protein)}` : ""} g</p>
  </div>`;
}

/** Kompakte Bilanz: Ring, drei Makros, eine Fusszeile. Wird immer gezeichnet, auch bei 0 kcal –
 *  sonst schiebt der erste Eintrag des Tages alles darunter nach unten. */
function nutriCard(eaten, budget, g, t) {
  return `<div class="card nutri-card">
    <div class="nutri-top">
      ${heroRing(eaten, budget)}
      <div class="nutri-macros">
        ${macro("protein", "Eiweiß", t.protein, g.protein, { lead: true })}
        ${macro("carbs", "Kohlenhydrate", t.carbs, g.carbs)}
        ${macro("fat", "Fett", t.fat, g.fat)}
      </div>
    </div>
    <div class="nutri-foot"><span>Ziel ${g.kcal ? num(g.kcal) : "–"} · Verbrannt ${num(data.burned_kcal || 0)} kcal</span>
      <button type="button" class="link-btn" data-details>Alle Nährwerte</button></div>
    ${feedbackLine()}
  </div>`;
}

function mealCard(key, m, pend) {
  const extra = pend.filter((e) => e.meal === key);
  const visible = m.entries.filter((e) => !isPendingDelete(`food-log/${e.id}`));
  const kcal = m.kcal - m.entries.filter((e) => isPendingDelete(`food-log/${e.id}`))
    .reduce((n, e) => n + (e.kcal || 0), 0) + extra.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
  return `<div class="card meal" data-meal-card="${key}">
    <div class="card-head">${icon(MEAL_ICON[key] || "food")}
      <div class="meal-title"><b>${esc(m.label)}</b><span class="aside">${num(kcal)} kcal</span></div>
      <button type="button" class="btn small tonal" data-add="${key}" aria-label="${esc(m.label)} hinzufügen">${icon("plus")} Hinzufügen</button>
      <button type="button" class="icon-btn" data-meal-menu="${key}" aria-label="Mehr zu ${esc(m.label)}">${icon("more")}</button>
    </div>
    <div class="list flush">
      ${visible.map((e) => `<div class="row-item" role="button" tabindex="0" data-entry="${e.id}">
        <div class="row-main"><div class="row-title">${esc(e.name)}</div>
          <div class="row-meta"><span>${isQuick(e) ? "Schnelleintrag" : `${portionsText(e)}${num(e.grams)} g`}</span>${
            e.protein || e.carbs || e.fat ? `<span>E ${num(e.protein)} · K ${num(e.carbs)} · F ${num(e.fat)}</span>` : ""}</div></div>
        <span class="row-aside">${num(e.kcal)} kcal</span></div>`).join("")}
      ${extra.map((e) => `<div class="row-item is-pending"><div class="row-main">
        <div class="row-title">${esc(e.preview?.name || "Eintrag")}</div>
        <div class="row-meta"><span>${e.grams ? `${num(e.grams)} g` : "Schnelleintrag"}</span><span class="pill">wartet</span></div></div>
        <span class="row-aside">${num(e.preview?.kcal)} kcal</span></div>`).join("")}
      ${visible.length || extra.length ? "" : `<div class="meal-empty-row">Noch nichts eingetragen</div>`}
    </div>
  </div>`;
}

/** Seltene Aktionen einer Mahlzeit hinter dem "⋯": immer erreichbar, nicht nur bei leerer Mahlzeit. */
function openMealMenu(key) {
  const entries = data.meals[key].entries.filter((e) => !isPendingDelete(`food-log/${e.id}`));
  actionMenu(data.meals[key].label, [
    { label: "Wie am Vortag", icon: "refresh", run: () => copyMeal(key) },
    { label: "Gespeicherte Mahlzeit", icon: "food", run: () => openSavedMeals(key) },
    entries.length && { label: "Als Mahlzeit speichern", icon: "download", run: () => saveMeal(key) },
    entries.length && { label: "Nährwerte", icon: "progress", run: () => openNutrients(`${data.meals[key].label} · Nährwerte`, entries, {}) },
  ]);
}

/** Alle Naehrwerte als Sheet (statt Akkordeon, das die Seite verschiebt). */
function openNutrients(title, entries, goal) {
  const t = sumEntries(entries);
  const day = sumEntries(visibleEntries()).kcal;
  const share = goal === null || !day ? "" : `<p class="hint">${Math.round((t.kcal / day) * 100)} % des Tages</p>`;
  openSheet({ title, body: `${nutrientRows(t, goal || {}, entries.length)}${incomplete(t, entries.length)}${share}` });
}

function essenView() {
  const g = data.goals.goals || {};
  const t = data.totals;
  const pend = pendingEntries();
  const pendKcal = pend.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
  const eaten = t.kcal + pendKcal;
  const budget = g.kcal ? g.kcal + (data.burned_kcal || 0) : 0;
  return `${nutriHead(eaten, budget, g, t)}
    ${nutriCard(eaten, budget, g, t)}
    ${!g.kcal ? `<p class="callout">${icon("warn")} Für ein Kalorienziel fehlen noch: ${esc((data.goals.missing || []).join(", "))}.
      <a href="#profil">Im Profil ergänzen</a></p>` : ""}
    ${Object.entries(data.meals).map(([key, m]) => mealCard(key, m, pend)).join("")}
    ${waterRow(data.water_ml + pendingWater() + waterExtra, data.water_goal)}
    ${weekCard() || (weekPending ? skeletonCards([150]) : "")}
    <p class="hint pad source-note">Nährwerte: Bundeslebensmittelschlüssel (BLS) 4.0, Max Rubner-Institut, CC BY 4.0 · Markenprodukte: Open Food Facts, ODbL</p>`;
}

function fastenView() {
  if (!fast) {
    return `<div class="card quiet">${icon("cloud")} <span>Fasten ist gerade nicht geladen – bitte Verbindung prüfen.</span></div>`;
  }
  return `${fastCard()}${fastTipsCard()}`;
}

function render() {
  const switcher = `<div class="segmented view-switch" role="radiogroup" aria-label="Ansicht">
    <button type="button" role="radio" aria-checked="${view === "essen"}" data-view="essen">Essen</button>
    <button type="button" role="radio" aria-checked="${view === "fasten"}" data-view="fasten">Fasten</button></div>`;
  patch(root, `${switcher}${view === "fasten" ? fastenView() : essenView()}`);
  window.dispatchEvent(new Event("nutri-rendered"));
}

const visibleEntries = () => Object.values(data.meals).flatMap((m) => m.entries)
  .filter((e) => !isPendingDelete(`food-log/${e.id}`));

/** Dieselbe Mahlzeit wie am Vortag eintragen – das Fruehstueck ist oft jeden Tag gleich.
 *  Jeder Eintrag geht einzeln durch den Ausgang, also auch offline. */
async function copyMeal(meal, btn) {
  const prev = parseYmd(data.date);
  prev.setDate(prev.getDate() - 1);
  await (btn ? busy(btn, run) : run());
  async function run() {
    let before;
    try {
      before = await api(`nutrition/day?date=${iso(prev)}`);
    } catch (e) {
      return toast(e.offline ? "Der Vortag ist offline nicht geladen." : e.message, { error: true });
    }
    const entries = (before.meals[meal]?.entries || []).filter((e) => e.food_id || isQuick(e));
    if (!entries.length) return toast(`Am Vortag ist bei „${MEALS[meal]}“ nichts eingetragen.`, { error: true });
    let queued = 0;
    try {
      for (const e of entries) {
        const what = isQuick(e)
          ? { name: e.name, kcal: e.kcal, protein: e.protein, carbs: e.carbs, fat: e.fat }
          : { food_id: e.food_id, grams: e.grams };
        const res = await outbox.send({ path: "food-log", body: {
          profile_id: getProfile(), ...what, meal, date: entryDay(data?.date),
          client_id: outbox.uuid(), preview: { name: e.name, kcal: Math.round(e.kcal || 0) } } });
        if (res.queued) queued += 1;
      }
    } catch (e) { return toast(e.message, { error: true }); }
    toast(queued ? `${entries.length} Einträge übernommen – werden gesendet, sobald wieder Verbindung da ist`
      : `${MEALS[meal]} vom Vortag übernommen (${entries.length} ${entries.length === 1 ? "Eintrag" : "Einträge"})`);
    window.dispatchEvent(new CustomEvent("data-changed"));
  }
}

/** Alle Eintraege einer Mahlzeit unter einem Namen merken – naechstes Mal ein Tipp. */
function saveMeal(meal) {
  const items = data.meals[meal].entries.filter((e) => e.food_id && !isPendingDelete(`food-log/${e.id}`))
    .map((e) => ({ food_id: e.food_id, grams: e.grams }));
  if (!items.length) return toast("Diese Mahlzeit hat noch keine speicherbaren Einträge.", { error: true });
  const sheet = openSheet({ title: "Mahlzeit speichern", body: `<form id="mealForm" class="form">
      <label class="field">Name<input name="name" required maxlength="60" value="${esc(MEALS[meal])}" autocomplete="off"></label>
      <p class="hint">${items.length} ${items.length === 1 ? "Lebensmittel" : "Lebensmittel"} mit den heutigen Mengen.</p>
      <button class="btn primary" type="submit">${icon("check")} Speichern</button></form>` });
  $("input", sheet).select();
  const form = $("#mealForm", sheet);
  submitOnce(form, async (ev) => {
    try {
      await api("meals", { method: "POST", body: { profile_id: getProfile(),
        name: new FormData(ev.target).get("name"), items } });
      closeSheet();
      toast("Mahlzeit gespeichert");
    } catch (e) { formError(form, e.offline ? new Error("Zum Speichern ist eine Verbindung nötig.") : e); }
  });
}

/** Gespeicherte Mahlzeit in die Mahlzeit des Tages eintragen (jeder Eintrag einzeln durch den Ausgang). */
async function openSavedMeals(meal) {
  const sheet = openSheet({ title: "Gespeicherte Mahlzeiten", body: skeleton(3) });
  let list;
  try { list = await api("meals"); } catch (e) { closeSheet(); return toast(e.offline ? "Gespeicherte Mahlzeiten sind offline nicht geladen." : e.message, { error: true }); }
  if (!sheet.isConnected) return;
  $(".sheet-body", sheet).innerHTML = list.length ? `<div class="list">${list.map((m) => `
    <div class="row-item" role="button" tabindex="0" data-use="${m.id}">
      <div class="row-main"><div class="row-title">${esc(m.name)}</div>
        <div class="row-meta"><span>${m.items.length} ${m.items.length === 1 ? "Lebensmittel" : "Lebensmittel"}</span>
          <span>${num(m.kcal)} kcal</span></div></div>
      <button type="button" class="icon-btn" data-del-meal="${m.id}" aria-label="${esc(m.name)} löschen">${icon("trash")}</button></div>`).join("")}</div>`
    : `<p class="hint pad">Noch nichts gespeichert. Bei einer Mahlzeit mit Einträgen „Als Mahlzeit speichern“ antippen.</p>`;
  sheet.addEventListener("click", async (ev) => {
    const del = ev.target.closest("[data-del-meal]");
    if (del) {
      try { await api(`meals/${del.dataset.delMeal}`, { method: "DELETE" }); del.closest(".row-item").remove(); }
      catch (e) { toast(e.message, { error: true }); }
      return;
    }
    const use = ev.target.closest("[data-use]");
    if (!use) return;
    const m = list.find((x) => x.id === Number(use.dataset.use));
    closeSheet();
    let queued = 0;
    try {
      for (const it of m.items) {
        const res = await outbox.send({ path: "food-log", body: { profile_id: getProfile(), food_id: it.food_id,
          grams: it.grams, meal, date: entryDay(data?.date), client_id: outbox.uuid(), preview: { name: it.name, kcal: Math.round(it.kcal || 0) } } });
        if (res.queued) queued += 1;
      }
    } catch (e) { return toast(e.message, { error: true }); }
    toast(queued ? `${m.name} übernommen – wird gesendet, sobald wieder Verbindung da ist` : `${m.name} eingetragen`);
    window.dispatchEvent(new CustomEvent("data-changed"));
  });
}

export async function load() {
  const when = day || todayIso();
  view = prefs.get("nutriView", "essen") === "fasten" ? "fasten" : "essen";   // z. B. von der Fasten-Kachel auf Heute gesetzt
  if (!data) root.innerHTML = skeletonCards([76, 200, 150, 150, 150]);   // grobe Geometrie der echten Seite
  else root.classList.add("is-loading");   // Tageswechsel/Neuladen: alten Stand dimmen statt Skeleton
  try {
    // Alles parallel; Woche und Fasten warten hoechstens kurz mit, damit die Seite einmal zeichnet
    // (spaeter eintreffendes zeichnet still nach – es liegt unten bzw. im Kopf ohne Hoehe).
    const dayP = api(`nutrition/day?date=${when}`);
    const weekP = api(`nutrition/week?date=${when}`).then((w) => { week = w; }, () => { week = null; });
    const fastP = fetchFast();
    data = await dayP;
    await Promise.race([Promise.allSettled([weekP, fastP]), new Promise((r) => setTimeout(r, 1200))]);
    if ((day || todayIso()) !== when) return;   // inzwischen anderer Tag
    weekPending = false;
    render();
    loadFastTips();
    Promise.allSettled([weekP, fastP]).then(() => { if (data?.date === when && root.isConnected) render(); });
  } finally {
    root.classList.remove("is-loading");
  }
}

// ------------------------------------------------------- Hinzufügen
/** Vergleichsform wie nutrition.norm im Backend: "Müller" = "Mueller" = "muller". */
const norm = (s) => (s || "").toLowerCase().replace(/ß/g, "ss").replace(/(?<=[aou])e/g, "")
  .normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/['’`´]/g, "")
  .replace(/[^a-z0-9]+/g, " ").trim();
const matches = (f, q) => { const hay = norm(`${f.name} ${f.brand || ""}`); return norm(q).split(" ").every((w) => hay.includes(w)); };
const kcalFor = (f, grams) => Math.round(((f.kcal || 0) * grams) / 100);
const count = (n) => n.toLocaleString("de-AT", { maximumFractionDigits: 2 });

/** Portion eines Lebensmittels: angegebene Portion, sonst eine kleine Packung (Burger, Riegel, Becher) –
 *  grosse Packungen sind keine Portion. */
function servingOf(f) {
  if (f.serving_g > 0) {
    const label = f.serving_label || "Portion";
    return { g: f.serving_g, text: /\d\s*(g|ml)\b/i.test(label) ? label : `${label} (${num(f.serving_g)} g)` };
  }
  if (f.pack_g > 0 && f.pack_g <= 250) return { g: f.pack_g, text: `Packung (${num(f.pack_g)} g)` };
  return null;
}

const isQuick = (e) => !e.food_id && !e.grams;

/** Einheiten eines Lebensmittels: natuerliche Portionen vom Server (Stück, Scheibe, EL …),
 *  sonst die Portion bzw. Packung aus Open Food Facts. */
function unitsOf(f) {
  if (f.units?.length) return f.units;
  const s = servingOf(f);
  return s ? [{ label: s.text, g: s.g }] : [];
}

/** Eine Menge als Vielfaches einer Einheit, wenn sie glatt (auf halbe) aufgeht. */
function asUnits(f, grams) {
  for (const [i, u] of unitsOf(f).entries()) {
    const n = grams / u.g;
    if (n >= 0.5 && Math.abs(n * 2 - Math.round(n * 2)) < 0.02) return { i, n: Math.round(n * 2) / 2, u };
  }
  return null;
}

/** "2 × Scheibe · " vor der Grammzahl eines Eintrags, wenn die Menge glatt aufgeht. */
function portionsText(e) {
  const m = asUnits(e, e.grams);
  return m ? `${count(m.n)} × ${m.u.label} · ` : "";
}

/** Suchwort im Namen hervorheben (erster Treffer des laengsten Worts). */
function highlight(text, q) {
  const word = (q || "").trim().split(/\s+/).sort((a, b) => b.length - a.length)[0];
  const at = word && word.length >= 2 ? text.toLowerCase().indexOf(word.toLowerCase()) : -1;
  if (at < 0) return esc(text);
  return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + word.length))}</mark>${esc(text.slice(at + word.length))}`;
}

function foodRow(f, i, q, again = false, online = false) {
  const badge = online ? `<span class="pill">Online</span>` : "";
  // Schon mal gegessen: die zuletzt genommene Menge ist, was man mit einem Tipp uebernimmt –
  // also deren kcal als Hauptzahl, nicht mal pro Portion und mal pro 100 g.
  if (f.last_grams) {
    const meta = [f.brand && esc(f.brand), `${num(f.kcal)} kcal / 100 g`].filter(Boolean);
    return `<div class="row-item food-row" role="button" tabindex="0" data-pick="${i}">
      <div class="row-main"><div class="row-title">${highlight(f.name, q)}</div>
        <div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join("")}${badge}</div></div>
      <span class="row-aside food-kcal"><b>${num(kcalFor(f, f.last_grams))} kcal</b><small>${portionsText({ ...f, grams: f.last_grams })}${num(f.last_grams)} g wie zuletzt</small></span>
      ${again ? `<button type="button" class="icon-btn tonal-icon again-btn" data-repeat="${i}"
        aria-label="${esc(f.name)}, ${num(f.last_grams)} g wie zuletzt direkt eintragen">${icon("plus")}</button>` : ""}</div>`;
  }
  // Erste natuerliche Einheit als Hauptzahl: "1 Stück (mittel) = 87 kcal" sagt mehr als "58 / 100 g"
  const u = unitsOf(f)[0];
  const meta = [f.brand && esc(f.brand), u && esc(`${u.label} · ${num(u.g)} g`)].filter(Boolean);
  return `<div class="row-item food-row" role="button" tabindex="0" data-pick="${i}">
    <div class="row-main"><div class="row-title">${highlight(f.name, q)}</div>
      ${meta.length || badge ? `<div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join("")}${badge}</div>` : ""}</div>
    <span class="row-aside food-kcal">${u ? `<b>${num(kcalFor(f, u.g))} kcal</b><small>pro ${esc(u.label.replace(/ \(.*\)$/, ""))}</small><small>${num(f.kcal)} / 100 g</small>`
      : `<b>${num(f.kcal)} kcal</b><small>pro 100 g</small>`}</span></div>`;
}

/** Menge in Portionen oder Gramm. Gespeichert werden immer Gramm.
 *  update(food): Einheiten nachtragen (Portion kommt spaeter vom Server) – eine schon begonnene
 *  Eingabe bleibt dabei erhalten. */
function amountBox(box, f0, grams, onChange) {
  let f = f0;
  let units = unitsOf(f);
  let touched = false;
  // unit: Index in units, -1 = Gramm. Ohne Vorgabe: erste Einheit x 1, sonst 100 g.
  let unit = -1, value = 100;
  const init = () => {
    const hit = grams ? asUnits(f, grams) : null;
    unit = hit ? hit.i : (!grams && units.length ? 0 : -1);
    value = hit ? hit.n : (unit >= 0 ? 1 : Math.round(grams || 100));
  };
  init();
  const current = () => (unit >= 0 ? Math.round(value * units[unit].g * 10) / 10 : value);
  const unitText = (u) => (/\d\s*(g|ml)\b/i.test(u.label) ? u.label : `${u.label} · ${num(u.g)} g`);

  function paint() {
    const pack = f.pack_g > 0 && f.pack_g <= 1000 ? Math.round(f.pack_g) : null;
    const gramChips = [...new Set([f.last_grams, pack, 50, 100, 150, 200].filter(Boolean).map(Math.round))].slice(0, 5);
    box.innerHTML = `${units.length ? `<div class="chips unit-chips" role="radiogroup" aria-label="Einheit">
        ${units.map((u, i) => `<button type="button" role="radio" class="chip${i === unit ? " active" : ""}" aria-checked="${i === unit}"
          data-unit="${i}">${esc(unitText(u))}</button>`).join("")}
        <button type="button" role="radio" class="chip${unit < 0 ? " active" : ""}" aria-checked="${unit < 0}" data-unit="-1">Gramm</button>
      </div>` : ""}
      ${unit >= 0 ? `<div class="field"><span>Anzahl · ${esc(units[unit].label)}</span>
          <div class="stepper">
            <button type="button" class="icon-btn tonal-icon" data-step="-0.5" aria-label="Eine halbe weniger">${icon("minus")}</button>
            <input data-count type="number" inputmode="decimal" min="0.25" max="50" step="0.25" value="${value}" aria-label="Anzahl">
            <button type="button" class="icon-btn tonal-icon" data-step="0.5" aria-label="Eine halbe mehr">${icon("plus")}</button>
          </div></div>
        <div class="chips">${[0.5, 1, 2, 3].map((n) => `<button type="button" class="chip${n === value ? " active" : ""}" data-set="${n}">${count(n)}</button>`).join("")}</div>`
      : `<label class="field">Menge (g)<input data-grams type="number" inputmode="decimal" min="1" max="5000" value="${value}"></label>
        <div class="chips">${gramChips.map((g) => `<button type="button" class="chip${g === value ? " active" : ""}" data-set="${g}">${
          g === pack ? `${g} g (Packung)` : f.last_grams && g === Math.round(f.last_grams) ? `${g} g (zuletzt)` : `${g} g`}</button>`).join("")}</div>`}`;
  }

  const changed = () => {
    box.querySelectorAll("[data-set]").forEach((c) => c.classList.toggle("active", Number(c.dataset.set) === value));
    onChange(current());
  };
  box.addEventListener("click", (ev) => {
    const u = ev.target.closest("[data-unit]");
    if (u && Number(u.dataset.unit) !== unit) {
      touched = true;
      const g = current();
      unit = Number(u.dataset.unit);
      // Beim Wechsel die Menge mitnehmen, auf halbe Einheiten gerundet
      value = unit >= 0 ? Math.max(0.5, Math.round((g / units[unit].g) * 2) / 2) : Math.round(g);
      paint();
      return changed();
    }
    const step = ev.target.closest("[data-step]");
    if (step) value = Math.min(50, Math.max(0.5, Math.round((value + Number(step.dataset.step)) * 2) / 2));
    const set = ev.target.closest("[data-set]");
    if (set) value = Number(set.dataset.set);
    if (step || set) {
      touched = true;
      const input = box.querySelector("[data-count], [data-grams]");
      input.value = value;
      changed();
    }
  });
  box.addEventListener("input", (ev) => {
    if (!ev.target.matches("[data-count], [data-grams]")) return;
    touched = true;
    value = Number(ev.target.value) || 0;
    changed();
  });
  paint();
  onChange(current());
  return {
    grams: current,
    update(next) {
      f = next;
      units = unitsOf(f);
      if (!touched) init();
      else if (unit >= units.length) unit = -1;
      paint();
      onChange(current());
    },
  };
}

/** Online gefundenes Produkt erst lokal uebernehmen – Eintraege und Rezepte brauchen eine id. */
async function ensureFood(f) {
  if (f.id) return f;
  return api("foods", { method: "POST", body: {
    name: f.name, brand: f.brand, barcode: f.barcode, kcal: f.kcal, protein: f.protein,
    carbs: f.carbs, fat: f.fat, fiber: f.fiber, sugar: f.sugar, sat_fat: f.sat_fat, salt: f.salt,
    serving_g: f.serving_g, serving_label: f.serving_label,
    pack_g: f.pack_g, source: "off" } });
}

/** Hinzufuegen-Sheet in fester Huelle: Kopf (Suche, Barcode, Foto, Mahlzeit) | scrollender Inhalt
 *  (Vorschlaege/Treffer oder Menge) | Fuss (Zuletzt hinzugefuegt, Fertig/Hinzufuegen). Die Hoehe
 *  aendert sich nie – das Sheet bleibt nach dem Eintragen offen, damit man ein ganzes Fruehstueck
 *  in einem Rutsch erfassen kann.
 *  opts.onPick: Auswahl-Modus fuer Rezept-Zutaten (statt ins Tagebuch);
 *  opts.food: gleich mit diesem Lebensmittel bei der Menge starten. */
function openAdd(meal, opts = {}) {
  const picking = typeof opts.onPick === "function";
  const st = { meal, local: [], online: [], recent: [], favorites: [], items: [], query: "", food: null, error: null,
    searchingLocal: false, searchingOnline: false, suggestLoading: true, mode: "list", last: null };
  const sheet = openSheet({ title: picking ? "Zutat hinzufügen" : "Hinzufügen", cls: "add-sheet", wide: true,
    body: "", footer: `<div class="add-foot" data-foot></div>` });
  const body = $(".sheet-body", sheet);
  const foot = $("[data-foot]", sheet);
  let localTimer = null;
  let onlineTimer = null;
  let amount = null;
  const scroller = () => $("[data-scroll]", sheet);

  // ---- Fuss: je nach Modus
  function paintFoot() {
    if (st.mode === "amount") {
      foot.innerHTML = `<button type="button" class="btn ghost" data-back>${icon("left")} Zurück</button>
        <span class="grow"></span>
        <button type="button" class="btn primary" data-save>${icon("check")} ${picking ? "Als Zutat übernehmen" : "Hinzufügen"}</button>`;
    } else if (st.mode === "own") {
      foot.innerHTML = `<button type="button" class="btn ghost" data-back>${icon("left")} Zurück</button>
        <span class="grow"></span>
        <button type="submit" class="btn primary" form="foodForm">${st.editing ? "Speichern" : "Weiter"}</button>`;
    } else if (picking) {
      foot.innerHTML = `<span class="hint add-last">Lebensmittel wählen, dann die Menge angeben.</span>`;
    } else {
      const l = st.last;
      const eaten = data ? data.totals.kcal + pendingEntries().reduce((n, e) => n + (e.preview?.kcal || 0), 0) : null;
      foot.innerHTML = `<div class="add-last" data-last>${l
        ? `${icon("check")}<span>${esc(l.name)} · ${num(l.grams)} g · ${num(l.kcal)} kcal</span>${
          l.id ? `<button type="button" class="link-btn" data-undo>Rückgängig</button>` : `<span class="pill">wartet</span>`}`
        : `<span class="hint">${eaten == null ? "" : `Heute ${num(eaten)} kcal`}</span>`}</div>
        <button type="button" class="btn primary" data-done>Fertig</button>`;
    }
  }

  // ---- Kopf + Inhalt (Suche)
  function paintShell() {
    st.mode = "list";
    st.food = null;
    amount = null;
    body.innerHTML = `<div class="add-top">
        <label class="search">${icon("search")}<input type="search" data-q value="${esc(st.query)}"
          placeholder="Lebensmittel oder Marke suchen" aria-label="Lebensmittel suchen" autocomplete="off"></label>
        <div class="add-tools">
          <button type="button" class="btn small tonal" data-scan>${icon("barcode")} Barcode</button>
          ${picking ? "" : `<button type="button" class="btn small tonal" data-ai>${icon("spark")} Foto / Text</button>`}
          <button type="button" class="btn small ghost icon-only" data-more aria-label="Weitere Möglichkeiten">${icon("more")}</button>
        </div>
        ${picking ? "" : `<div class="segmented add-meal" role="radiogroup" aria-label="Mahlzeit">${Object.keys(MEALS).map((k) =>
          `<button type="button" role="radio" aria-checked="${k === st.meal}" data-meal="${k}">${MEAL_SHORT[k]}</button>`).join("")}</div>`}
      </div>
      <div class="add-scroll" data-scroll></div>`;
    paintList();
    paintFoot();
    const input = $("[data-q]", sheet);
    input.addEventListener("input", () => {
      if (st.mode !== "list") { st.mode = "list"; st.food = null; amount = null; paintFoot(); }
      st.query = input.value.trim();
      clearTimeout(localTimer);
      clearTimeout(onlineTimer);
      st.error = null;
      if (st.query.length < 2) { st.local = []; st.online = []; st.searchingOnline = false; st.searchingLocal = false; return paintList(); }
      // Eigene Lebensmittel sofort, Open Food Facts erst ab drei Zeichen und wenn nicht
      // mehr getippt wird – jede Suche kostet dort mehrere Abrufe (429-Gefahr).
      st.searchingLocal = true;
      st.searchingOnline = st.query.length >= 3;
      localTimer = setTimeout(searchLocal, 200);
      if (st.searchingOnline) onlineTimer = setTimeout(searchOnline, 700);
      else st.online = [];
      paintList();
    });
    if (!st.query) input.focus({ preventScroll: true });
  }

  // Eine Liste, eine Reihenfolge: Eintraege rutschen nie nach oben, neue haengen sich unten an.
  function paintList() {
    const box = scroller();
    if (!box || st.mode !== "list") return;
    st.items = [];
    const rows = (items, online = false) => items.map((f) => { st.items.push(f); return foodRow(f, st.items.length - 1, st.query, !picking, online); }).join("");
    let html;
    if (st.query.length < 2) {
      const favIds = new Set(st.favorites.map((f) => f.id));
      const list = [...st.favorites, ...st.recent.filter((f) => !favIds.has(f.id))];
      html = `<h3 class="form-section">Vorschläge</h3>${st.suggestLoading ? skeleton(5)
        : list.length ? `<div class="list">${rows(list)}</div>` : `<p class="hint pad">Noch keine zuletzt verwendeten Lebensmittel.</p>`}`;
    } else {
      const known = new Set(st.local.map((f) => f.barcode).filter(Boolean));
      const online = st.online.filter((f) => !f.barcode || !known.has(f.barcode));
      const status = st.searchingLocal || st.searchingOnline ? "Suche …"
        : st.error ? ""
        : !st.local.length && !online.length ? (st.query.length < 3 ? "Ab drei Buchstaben wird auch bei Marken gesucht." : "Nichts gefunden.") : "";
      html = `${st.local.length || online.length ? `<div class="list">${rows(st.local)}${rows(online, true)}</div>` : ""}
        <p class="hint pad add-status" role="status">${esc(status)}</p>`;
    }
    box.innerHTML = `${st.error ? `<p class="callout warn" role="status">${icon("warn")} ${esc(st.error)}</p>` : ""}${html}`;
  }

  async function searchLocal() {
    const q = st.query;
    try {
      const res = await api(`foods/search/local?q=${encodeURIComponent(q)}`);
      if (q !== st.query) return;
      st.local = res.local;
    } catch {
      if (q !== st.query) return;
      st.local = st.recent.filter((f) => matches(f, q));
    }
    st.searchingLocal = false;
    paintList();
  }

  async function searchOnline() {
    const q = st.query;
    try {
      const res = await api(`foods/search?q=${encodeURIComponent(q)}`, { timeout: OFF_TIMEOUT });
      if (q !== st.query) return;
      st.online = res.online;
      st.error = res.error;
    } catch (e) {
      if (q !== st.query) return;
      st.online = [];
      st.error = e.offline ? "Offline – es werden nur gespeicherte Lebensmittel durchsucht." : e.message;
    }
    st.searchingOnline = false;
    paintList();
  }

  // ---- Menge
  function paintAmount() {
    st.mode = "amount";
    const f = st.food;
    const box = scroller();
    box.innerHTML = `<div class="amount-head"><h3 class="card-title">${esc(f.name)}</h3>
        ${f.id ? `<button type="button" class="icon-btn fav-btn${f.favorite ? " on" : ""}" data-fav aria-pressed="${!!f.favorite}"
          aria-label="${f.favorite ? "Aus den Favoriten nehmen" : "Als Favorit merken"}">${icon("star")}</button>` : ""}</div>
      <div class="amount-links hint">${f.brand ? `<span>${esc(f.brand)}</span>` : ""}
        ${f.source === "eigen" && f.id ? `<button type="button" class="link-btn" data-edit-food>${icon("edit")} Lebensmittel bearbeiten</button>` : ""}
        ${f.source === "rezept" && f.id && !picking ? `<button type="button" class="link-btn" data-edit-recipe>${icon("edit")} Rezept bearbeiten</button>` : ""}</div>
      <div data-amount></div>
      <div class="card quiet" data-calc></div>`;
    box.scrollTop = 0;
    amount = amountBox($("[data-amount]", sheet), f, f.last_grams, (g) => {
      const k = (v) => num(((v || 0) * g) / 100, 1);
      $("[data-calc]", sheet).innerHTML = `<b>${k(f.kcal)} kcal</b><span>${num(g, 1)} g · E ${k(f.protein)} g · K ${k(f.carbs)} g · F ${k(f.fat)} g</span>`;
    });
    paintFoot();
  }

  async function pick(food) {
    st.food = { ...food };
    paintAmount();
    // Die Suche kennt die Portion nur fuer die ersten Treffer – bei Bedarf jetzt nachladen.
    // Die Menge wird nur ergaenzt (update), nie neu aufgebaut: eine Eingabe geht nicht verloren.
    if (food.barcode && !food.serving_g && (food.source === "off" || !food.id)) {
      try {
        const p = await api(`foods/portion/${food.barcode}`, { timeout: OFF_TIMEOUT });
        if (st.food?.barcode !== food.barcode || !(p.serving_g || p.pack_g)) return;
        Object.entries(p).forEach(([k, v]) => { if (v && !st.food[k]) st.food[k] = v; });
        amount?.update(st.food);
      } catch { /* ohne Portion geht es auch */ }
    }
  }

  /** Nach dem Eintragen: zurueck zur Liste, Streifen "zuletzt hinzugefuegt", Sheet bleibt offen. */
  function afterAdd(entry, food, grams, queued) {
    st.last = { id: queued ? null : entry?.id ?? null, name: food.name, grams, kcal: Math.round((food.kcal * grams) / 100) };
    st.query = "";
    st.local = [];
    st.online = [];
    paintShell();
    window.dispatchEvent(new CustomEvent("data-changed"));
  }

  async function save(btn) {
    const f = st.food;
    const grams = amount.grams();
    if (!(grams > 0)) return toast("Bitte eine Menge eintragen.", { error: true });
    if (picking) {
      return busy(btn, async () => {
        try { opts.onPick(await ensureFood(f), grams); } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
      });
    }
    await busy(btn, async () => {
      try {
        const food = await ensureFood(f);
        const res = await outbox.send({ path: "food-log", body: { profile_id: getProfile(), food_id: food.id, grams, meal: st.meal,
          date: entryDay(data?.date), client_id: outbox.uuid(),
          preview: { name: food.name, kcal: Math.round((food.kcal * grams) / 100) } } });
        afterAdd(res.data?.entry, food, grams, res.queued);
      } catch (e) {
        toast(e.offline ? "Offline lassen sich nur schon verwendete Lebensmittel eintragen." : e.message, { error: true });
      }
    });
  }

  /** Mit einem Tipp noch mal dasselbe in derselben Menge wie zuletzt – ohne Mengenfenster. */
  const added = new Set();
  async function logAgain(btn) {
    const f = st.items[Number(btn.dataset.repeat)];
    if (!f?.id || !(f.last_grams > 0)) return;
    if (added.has(f.id)) return toast(`${f.name} ist schon eingetragen – zum Ändern im Tagebuch öffnen.`);
    await busy(btn, async () => {
      try {
        const res = await outbox.send({ path: "food-log", body: {
          profile_id: getProfile(), food_id: f.id, grams: f.last_grams, meal: st.meal, date: entryDay(data?.date),
          client_id: outbox.uuid(), preview: { name: f.name, kcal: kcalFor(f, f.last_grams) } } });
        added.add(f.id);
        btn.innerHTML = icon("check");
        btn.classList.add("done");
        st.last = { id: res.queued ? null : res.data?.entry?.id ?? null, foodId: f.id, name: f.name, grams: f.last_grams, kcal: kcalFor(f, f.last_grams) };
        paintFoot();
        window.dispatchEvent(new CustomEvent("data-changed"));
      } catch (e) { toast(e.message, { error: true }); }
    });
  }

  async function undoLast(btn) {
    const l = st.last;
    if (!l?.id) return;
    await busy(btn, async () => {
      try {
        await api(`food-log/${l.id}`, { method: "DELETE" });
        if (l.foodId) added.delete(l.foodId);
        st.last = null;
        // Haken in der Liste zuruecknehmen
        if (st.mode === "list") paintList();
        paintFoot();
        window.dispatchEvent(new CustomEvent("data-changed"));
      } catch (e) { toast(e.message, { error: true }); }
    });
  }

  async function byBarcode(code) {
    try {
      const res = await api(`foods/barcode/${code}`, { timeout: OFF_TIMEOUT });
      pick(res.food);
    } catch (e) {
      if (/nicht gefunden/.test(e.message)) {
        toast("Produkt nicht in der Datenbank – bitte die Werte von der Packung eintragen.");
        return openOwn({ barcode: code });
      }
      st.error = e.offline ? "Offline – der Barcode kann gerade nicht nachgeschlagen werden." : e.message;
      paintShell();
    }
  }

  /** Eigenes Lebensmittel anlegen – oder, mit preset.id, ein selbst angelegtes bearbeiten. */
  function openOwn(preset = {}) {
    const edit = Boolean(preset.id);
    const v = (k) => esc(preset[k] ?? "");
    st.mode = "own";
    st.editing = edit;
    body.innerHTML = `<div class="add-scroll"><form id="foodForm" class="form">
        <label class="field">Name<input name="name" maxlength="120" required autofocus value="${esc(preset.name || st.query || "")}"></label>
        <label class="field">Marke<input name="brand" maxlength="60" placeholder="optional" value="${v("brand")}"></label>
        <p class="hint">Nährwerte je 100 g (stehen auf der Packung):</p>
        <div class="field-row">
          <label class="field">kcal<input name="kcal" type="number" inputmode="decimal" min="0" max="950" step="0.1" required value="${v("kcal")}"></label>
          <label class="field">Eiweiß<input name="protein" type="number" inputmode="decimal" min="0" max="100" step="0.1" value="${v("protein")}"></label>
        </div>
        <div class="field-row">
          <label class="field">Kohlenhydrate<input name="carbs" type="number" inputmode="decimal" min="0" max="100" step="0.1" value="${v("carbs")}"></label>
          <label class="field">Fett<input name="fat" type="number" inputmode="decimal" min="0" max="100" step="0.1" value="${v("fat")}"></label>
        </div>
        <p class="hint">Portion – dann trägst du später einfach „1 Scheibe“ statt Gramm ein:</p>
        <div class="field-row">
          <label class="field">Portion heißt<input name="serving_label" maxlength="60" placeholder="z. B. 1 Scheibe" value="${v("serving_label")}"></label>
          <label class="field">Portion (g)<input name="serving_g" type="number" inputmode="decimal" min="1" max="2000" placeholder="optional" value="${v("serving_g")}"></label>
        </div>
        <input type="hidden" name="barcode" value="${esc(preset.barcode || "")}">
        ${edit ? `<p class="hint">Gilt für alle Profile – eigene Lebensmittel sind gemeinsam. Schon eingetragene Mahlzeiten behalten ihre Werte.</p>
          <div class="btn-row"><button type="button" class="btn danger ghost" data-delete-food>${icon("trash")} Löschen</button></div>` : ""}
      </form></div>`;
    paintFoot();
    const form = $("#foodForm", sheet);
    submitOnce(form, async () => {
      try {
        const food = edit
          ? await api(`foods/${preset.id}`, { method: "PATCH", body: formData(form) })
          : await api("foods", { method: "POST", body: formData(form) });
        if (edit) toast("Lebensmittel gespeichert");
        paintShell();       // Huelle wieder aufbauen, dann Menge
        pick({ ...preset, ...food });
      } catch (e) { formError(form, e); }
    });
    if (edit) {
      armDelete($("[data-delete-food]", sheet), async () => {
        try {
          await api(`foods/${preset.id}`, { method: "DELETE" });
          st.recent = st.recent.filter((f) => f.id !== preset.id);
          st.local = st.local.filter((f) => f.id !== preset.id);
          toast("Lebensmittel gelöscht – eingetragene Mahlzeiten bleiben");
          paintShell();
        } catch (e) { toast(e.message, { error: true }); }
      }, "Löschen", "Wirklich löschen?");
    }
  }

  /** Seltenes hinter dem "⋯": Menue ersetzt das Sheet, die Ziele oeffnen ohnehin eigene Sheets. */
  function openMore() {
    actionMenu("Weitere Möglichkeiten", [
      !picking && { label: "Nur Kalorien eintragen", icon: "flame", run: () => openQuick(st.meal) },
      { label: "Eigenes Lebensmittel anlegen", icon: "plus", run: () => openAdd(st.meal, { ...opts, own: true, query: st.query }) },
      !picking && { label: "Rezept erstellen", icon: "food", run: () => openRecipe(null, st.meal) },
      !picking && { label: "Gespeicherte Mahlzeit", icon: "download", run: () => openSavedMeals(st.meal) },
      !picking && { label: "Wie am Vortag", icon: "refresh", run: () => copyMeal(st.meal) },
    ]);
  }
  // Hinweis: actionMenu ersetzt dieses Sheet – "Eigenes Lebensmittel" oeffnet es deshalb neu im Anlege-Modus.

  sheet.addEventListener("click", async (ev) => {
    const t = (q) => ev.target.closest(q);
    const repeat = t("[data-repeat]");
    if (repeat) return logAgain(repeat);
    const p = t("[data-pick]");
    if (p) return pick(st.items[Number(p.dataset.pick)]);
    if (t("[data-back]")) return paintShell();
    if (t("[data-edit-food]") && st.food) return openOwn(st.food);
    const s = t("[data-save]");
    if (s) return save(s);
    const undo = t("[data-undo]");
    if (undo) return undoLast(undo);
    if (t("[data-done]")) return closeSheet();
    const m = t("[data-meal]");
    if (m) {
      st.meal = m.dataset.meal;
      sheet.querySelectorAll("[data-meal]").forEach((b) => b.setAttribute("aria-checked", String(b === m)));
      return;
    }
    if (t("[data-more]")) return openMore();
    if (t("[data-ai]")) return openAi(st.meal, st.query);
    if (t("[data-edit-recipe]") && st.food) return openRecipe(st.food.id, st.meal);
    const fav = t("[data-fav]");
    if (fav && st.food?.id) return toggleFavorite(fav);
    if (t("[data-scan]")) {
      const code = await scanBarcode();
      if (code) byBarcode(code);
    }
  });

  async function toggleFavorite(btn) {
    const on = !st.food.favorite;
    await busy(btn, async () => {
      try {
        await api(`foods/${st.food.id}/favorite`, { method: on ? "PUT" : "DELETE" });
        st.food.favorite = on;
        st.favorites = on ? [{ ...st.food }, ...st.favorites] : st.favorites.filter((f) => f.id !== st.food.id);
        btn.classList.toggle("on", on);
        btn.setAttribute("aria-pressed", on);
        btn.setAttribute("aria-label", on ? "Aus den Favoriten nehmen" : "Als Favorit merken");
        toast(on ? "Als Favorit gemerkt" : "Aus den Favoriten genommen");
      } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
    });
  }

  // Zaehler im Fuss nachfuehren, wenn die Seite dahinter neu gezeichnet wurde
  const onRendered = () => {
    if (!sheet.isConnected) return window.removeEventListener("nutri-rendered", onRendered);
    if (st.mode === "list" && !st.last) paintFoot();
  };
  window.addEventListener("nutri-rendered", onRendered);

  if (opts.query) st.query = opts.query;
  paintShell();
  if (opts.own) openOwn({ name: opts.query });
  else if (opts.food) pick(opts.food);
  // Vorschlaege erst zeigen, wenn Zuletzt UND Favoriten da sind: ein Mal zeichnen, nichts springt nach
  Promise.allSettled([api("foods/recent"), api("foods/favorites")]).then(([r, f]) => {
    st.recent = r.status === "fulfilled" ? r.value : [];
    st.favorites = f.status === "fulfilled" ? f.value : [];
    st.suggestLoading = false;
    paintList();
  });
}

// ------------------------------------------------------------ KI: Text oder Foto
const AI_TIMEOUT = 150000;   // claude -p auf dem Pi: Start + Antwort, mit Foto auch mal 30 s

/** Foto verkleinern (max. 1280 px, JPEG) – reicht zum Erkennen und spart Upload-Zeit. */
function shrinkPhoto(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, 1280 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.8));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Bild konnte nicht gelesen werden.")); };
    img.src = url;
  });
}

function openAi(meal, text = "") {
  let photo = null;
  const sheet = openSheet({
    title: "Mahlzeit erkennen",
    body: `<form id="aiForm" class="form">
      <label class="field">Was hast du gegessen?
        <textarea name="text" rows="3" maxlength="500" placeholder="z. B. 2 Semmeln mit Butter und Schinken, ein Kaffee mit Milch">${esc(text)}</textarea></label>
      <label class="btn small ghost">${icon("camera")} Foto aufnehmen oder wählen
        <input type="file" accept="image/*" capture="environment" data-photo hidden></label>
      <img class="ai-photo" data-preview alt="" hidden>
      <p class="hint">Claude schätzt Teile und Mengen, die Nährwerte kommen aus der Datenbank. Du prüfst alles, bevor es eingetragen wird.</p>
    </form>`,
    footer: `<button type="submit" class="btn primary" form="aiForm">${icon("spark")} Erkennen</button>`,
  });
  const form = $("#aiForm", sheet);
  $("[data-photo]", form).addEventListener("change", async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try {
      photo = await shrinkPhoto(file);
      const prev = $("[data-preview]", form);
      prev.src = photo;
      prev.hidden = false;
    } catch (e) { formError(form, e); }
  });
  submitOnce(form, async () => {
    const t = form.elements.text.value.trim();
    if (!t && !photo) return formError(form, new Error("Bitte beschreiben oder ein Foto wählen."));
    $(".sheet-body", sheet).insertAdjacentHTML("beforeend",
      `<p class="coach-wait" data-wait>${icon("spark")} Claude schaut sich das an – das dauert 10–30 Sekunden …</p>`);
    try {
      const res = await api("nutrition/ai", { method: "POST", body: { text: t || null, image: photo }, timeout: AI_TIMEOUT });
      if (!res.items.length) {
        $("[data-wait]", sheet)?.remove();
        return formError(form, new Error(res.hinweis || "Nichts erkannt – beschreib es etwas genauer."));
      }
      reviewAi(meal, res);
    } catch (e) {
      $("[data-wait]", sheet)?.remove();
      formError(form, e.offline ? new Error("Dafür ist eine Verbindung nötig.") : e);
    }
  });
}

/** Erkannte Teile pruefen: Treffer waehlen, Menge anpassen, abwaehlen – dann alles eintragen. */
function reviewAi(meal, res) {
  const items = res.items.map((it) => ({ ...it, pick: it.candidates.length ? 0 : -1, on: true }));
  const kcalOf = (it) => (it.pick >= 0 ? (it.candidates[it.pick].kcal * it.grams) / 100
    : ((it.estimate.kcal || 0) * it.grams) / (it.origGrams || it.grams));
  items.forEach((it) => { it.origGrams = it.grams; });
  const sheet = openSheet({
    title: "Erkannt – bitte prüfen",
    body: `${res.hinweis ? `<p class="callout">${icon("spark")} ${esc(res.hinweis)}</p>` : ""}
      <div class="ai-items">${items.map((it, i) => `<div class="card ai-item" data-i="${i}">
        <label class="toggle"><input type="checkbox" data-on checked><span></span><b>${esc(it.name)}</b>${it.menge ? ` <small>· ${esc(it.menge)}</small>` : ""}</label>
        <label class="field">Lebensmittel<select data-pick>
          ${it.candidates.map((c, j) => `<option value="${j}">${esc(c.name)}${c.brand ? ` (${esc(c.brand)})` : ""} · ${num(c.kcal)} kcal/100 g</option>`).join("")}
          <option value="-1"${it.candidates.length ? "" : " selected"}>Schätzung von Claude${it.estimate.kcal ? ` (${num(it.estimate.kcal)} kcal)` : ""}</option>
        </select></label>
        <div class="field-row"><label class="field narrow">Menge (g)<input data-grams type="number" inputmode="decimal" min="1" max="3000" value="${num(it.grams)}"></label>
          <p class="ai-kcal" data-kcal></p></div>
      </div>`).join("")}</div>
      <label class="field">Mahlzeit<select data-meal>${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <p class="hint" data-total></p>`,
    footer: `<button type="button" class="btn ghost" data-again>Neu beschreiben</button><span class="grow"></span>
      <button type="button" class="btn primary" data-save-ai>${icon("check")} Eintragen</button>`,
  });
  const paint = () => {
    let total = 0;
    items.forEach((it, i) => {
      const k = kcalOf(it);
      if (it.on) total += k;
      const card = sheet.querySelector(`.ai-item[data-i="${i}"]`);
      card.classList.toggle("off", !it.on);
      $("[data-kcal]", card).textContent = `${num(k)} kcal`;
    });
    $("[data-total]", sheet).textContent = `Zusammen ${num(total)} kcal · ${items.filter((x) => x.on).length} Einträge`;
  };
  sheet.addEventListener("input", (ev) => {
    const card = ev.target.closest(".ai-item");
    if (!card) return;
    const it = items[Number(card.dataset.i)];
    if (ev.target.matches("[data-grams]")) it.grams = Number(ev.target.value) || 0;
    if (ev.target.matches("[data-pick]")) it.pick = Number(ev.target.value);
    if (ev.target.matches("[data-on]")) it.on = ev.target.checked;
    paint();
  });
  sheet.addEventListener("click", async (ev) => {
    if (ev.target.closest("[data-again]")) return openAi(meal);
    const btn = ev.target.closest("[data-save-ai]");
    if (!btn) return;
    const chosen = items.filter((it) => it.on && it.grams > 0);
    if (!chosen.length) return toast("Nichts ausgewählt.", { error: true });
    const m = $("[data-meal]", sheet).value;
    await busy(btn, async () => {
      let queued = 0;
      try {
        for (const it of chosen) {
          const f = (x) => (x == null ? null : Math.round(((x * it.grams) / it.origGrams) * 10) / 10);
          const what = it.pick >= 0
            ? { food_id: it.candidates[it.pick].id, grams: it.grams }
            : { name: it.name, kcal: Math.max(1, Math.round(f(it.estimate.kcal) || 0)), protein: f(it.estimate.protein),
              carbs: f(it.estimate.carbs), fat: f(it.estimate.fat) };
          const r = await outbox.send({ path: "food-log", body: { profile_id: getProfile(), ...what, meal: m,
            date: entryDay(data?.date), client_id: outbox.uuid(), preview: { name: it.pick >= 0 ? it.candidates[it.pick].name : it.name, kcal: Math.round(kcalOf(it)) } } });
          if (r.queued) queued += 1;
        }
      } catch (e) { return toast(e.message, { error: true }); }
      closeSheet();
      toast(queued ? `${chosen.length} Einträge – werden gesendet, sobald wieder Verbindung da ist` : `${chosen.length} Einträge eingetragen`);
      window.dispatchEvent(new CustomEvent("data-changed"));
    });
  });
  paint();
}

// ------------------------------------------------------------ Rezepte
/** Rezept-Editor. Der Entwurf lebt ausserhalb des Sheets, weil fuer jede Zutat das Such-Sheet
 *  an seine Stelle tritt (es gibt immer nur ein Sheet). */
let recipeDraft = null;

async function openRecipe(id, meal) {
  if (id && recipeDraft?.id !== id) {
    try {
      const r = await api(`recipes/${id}`);
      recipeDraft = { id, name: r.name, portions: r.recipe.portions, cooked_g: r.recipe.cooked_g || null,
        items: r.recipe.items.map((it) => ({ ...it, per100: it.grams ? (it.kcal * 100) / it.grams : 0 })) };
    } catch (e) { return toast(e.offline ? "Rezepte brauchen eine Verbindung." : e.message, { error: true }); }
  } else if (!id && !recipeDraft) {
    recipeDraft = { id: null, name: "", portions: 2, cooked_g: null, items: [] };
  }
  const d = recipeDraft;
  const total = d.items.reduce((n, it) => n + (it.per100 * it.grams) / 100, 0);
  const sheet = openSheet({
    title: d.id ? "Rezept bearbeiten" : "Rezept erstellen",
    body: `<form id="recipeForm" class="form">
      <label class="field">Name<input name="name" maxlength="120" required value="${esc(d.name)}" placeholder="z. B. Topfenpalatschinken"></label>
      <div class="field-row">
        <label class="field narrow">Portionen<input name="portions" type="number" inputmode="numeric" min="1" max="50" required value="${d.portions}"></label>
        <label class="field">Gewicht nach dem Kochen (g)<input name="cooked_g" type="number" inputmode="numeric" min="1" max="20000" value="${d.cooked_g ?? ""}" placeholder="optional"></label>
      </div>
      <h3 class="form-section">Zutaten</h3>
      ${d.items.length ? `<div class="list">${d.items.map((it, i) => `<div class="row-item">
          <div class="row-main"><div class="row-title">${esc(it.name)}</div><div class="row-meta"><span>${num(it.grams)} g</span></div></div>
          <span class="row-aside">${num((it.per100 * it.grams) / 100)} kcal</span>
          <button type="button" class="icon-btn" data-drop="${i}" aria-label="${esc(it.name)} entfernen">${icon("x")}</button></div>`).join("")}</div>`
        : `<p class="hint pad">Noch keine Zutaten.</p>`}
      <button type="button" class="btn small tonal" data-ingredient>${icon("plus")} Zutat hinzufügen</button>
      ${d.items.length ? `<p class="hint">Gesamt ${num(total)} kcal · pro Portion ${num(total / Math.max(1, d.portions))} kcal</p>` : ""}
    </form>`,
    footer: `${d.id ? `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>` : ""}
      <span class="grow"></span>
      <button type="button" class="btn ghost" data-discard>Verwerfen</button>
      <button type="submit" class="btn primary" form="recipeForm">${icon("check")} Speichern</button>`,
  });
  const form = $("#recipeForm", sheet);
  const keep = () => {   // Eingaben im Entwurf halten, bevor das Sheet wechselt
    const f = formData(form);
    Object.assign(d, { name: f.name || "", portions: Number(f.portions) || 1, cooked_g: f.cooked_g || null });
  };
  sheet.addEventListener("click", (ev) => {
    const drop = ev.target.closest("[data-drop]");
    if (drop) { keep(); d.items.splice(Number(drop.dataset.drop), 1); return openRecipe(d.id, meal); }
    if (ev.target.closest("[data-ingredient]")) {
      keep();
      return openAdd(meal, { onPick: (food, grams) => {
        d.items.push({ food_id: food.id, name: food.name, grams, per100: food.kcal || 0 });
        openRecipe(d.id, meal);
      } });
    }
    if (ev.target.closest("[data-discard]")) { recipeDraft = null; closeSheet(); }
  });
  submitOnce(form, async () => {
    keep();
    if (!d.items.length) return formError(form, new Error("Bitte mindestens eine Zutat hinzufügen."));
    const body = { name: d.name, portions: d.portions, cooked_g: d.cooked_g,
      items: d.items.map((it) => ({ food_id: it.food_id, grams: it.grams })) };
    try {
      const saved = await api(d.id ? `recipes/${d.id}` : "recipes", { method: d.id ? "PUT" : "POST", body });
      recipeDraft = null;
      toast(`Rezept „${saved.name}“ gespeichert – ${num(saved.serving_g)} g pro Portion`);
      openAdd(meal, { food: saved });       // gleich eintragen koennen
    } catch (e) { formError(form, e); }
  });
  if (d.id) {
    armDelete($("[data-delete]", sheet), async () => {
      try {
        await api(`foods/${d.id}`, { method: "DELETE" });
        recipeDraft = null;
        closeSheet();
        toast("Rezept gelöscht – eingetragene Mahlzeiten bleiben");
      } catch (e) { toast(e.message, { error: true }); }
    }, "Löschen", "Rezept löschen?");
  }
}

/** Schnelleintrag: nur Kalorien (und optional Makros) – fuers Restaurant oder wenn nichts passt. */
function openQuick(meal, entry = null) {
  const v = entry || {};
  const sheet = openSheet({
    title: entry ? "Schnelleintrag" : "Nur Kalorien eintragen",
    body: `<form id="quickForm" class="form">
      <label class="field">Was?<input name="name" maxlength="80" placeholder="optional, z. B. Pizza beim Italiener" value="${esc(entry && v.name !== "Schnelleintrag" ? v.name : "")}"></label>
      <label class="field">Kalorien (kcal)<input name="kcal" type="number" inputmode="numeric" min="1" max="5000" required value="${v.kcal ?? ""}" ${entry ? "" : "autofocus"}></label>
      <details class="field-more" ${v.protein || v.carbs || v.fat ? "open" : ""}><summary>Makros (optional)</summary>
        <div class="field-row">
          <label class="field narrow">Eiweiß (g)<input name="protein" type="number" inputmode="decimal" min="0" max="500" step="0.1" value="${v.protein ?? ""}"></label>
          <label class="field narrow">KH (g)<input name="carbs" type="number" inputmode="decimal" min="0" max="500" step="0.1" value="${v.carbs ?? ""}"></label>
          <label class="field narrow">Fett (g)<input name="fat" type="number" inputmode="decimal" min="0" max="500" step="0.1" value="${v.fat ?? ""}"></label>
        </div></details>
      <label class="field">Mahlzeit<select name="meal">${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === (v.meal || meal) ? " selected" : ""}>${l}</option>`).join("")}</select></label>
    </form>`,
    footer: `${entry ? `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button><span class="grow"></span>` : ""}
      <button type="submit" class="btn primary" form="quickForm">${icon("check")} ${entry ? "Speichern" : "Eintragen"}</button>`,
  });
  const form = $("#quickForm", sheet);
  submitOnce(form, async () => {
    const f = formData(form);
    try {
      if (entry) {
        await api(`food-log/${entry.id}`, { method: "PATCH", body: f });
        toast("Gespeichert");
      } else {
        const res = await outbox.send({ path: "food-log", body: { profile_id: getProfile(), ...f, date: entryDay(data?.date),
          client_id: outbox.uuid(), preview: { name: f.name || "Schnelleintrag", kcal: Math.round(f.kcal) } } });
        toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist" : `${Math.round(f.kcal)} kcal eingetragen`);
      }
      closeSheet();
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { formError(form, e); }
  });
  if (entry) {
    armDelete($("[data-delete]", sheet), () => {
      closeSheet();
      deleteLater(`food-log/${entry.id}`, "Eintrag gelöscht");
    }, "Löschen", "Eintrag löschen?");
  }
}

function openEntry(id) {
  const e = Object.values(data.meals).flatMap((m) => m.entries).find((x) => x.id === id);
  if (!e) return;
  if (isQuick(e)) return openQuick(e.meal, e);
  const per100 = (k) => (e.grams ? ((e[k] || 0) * 100) / e.grams : 0);
  const food = { serving_g: e.serving_g, serving_label: e.serving_label, pack_g: e.pack_g, units: e.units };
  const sheet = openSheet({
    title: esc(e.name),
    body: `<form id="entryForm" class="form">
      <div data-amount></div>
      <label class="field">Mahlzeit<select name="meal">${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === e.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <p class="hint" data-calc-hint></p>
      <div data-entry-details></div>
    </form>`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="submit" class="btn primary" form="entryForm">Speichern</button>`,
  });
  const form = $("#entryForm", sheet);
  const amt = amountBox($("[data-amount]", sheet), food, e.grams, (g) => {
    const k = (key) => num((per100(key) * g) / 100, 1);
    $("[data-calc-hint]", sheet).textContent = `${num(g, 1)} g · ${k("kcal")} kcal · E ${k("protein")} g · K ${k("carbs")} g · F ${k("fat")} g`;
    // unbekannte Detail-Werte bleiben unbekannt (null), statt mit der Menge zu 0 zu werden
    const scaled = Object.fromEntries(NUTRIENT_ROWS.map(([key]) => [key,
      e[key] == null ? null : e.grams ? (e[key] * g) / e.grams : 0]));
    $("[data-entry-details]", sheet).innerHTML = detailsBox("entry", "Alle Nährwerte",
      nutrientRows(sumEntries([scaled]), {}, 1));
  });
  submitOnce(form, async () => {
    const grams = amt.grams();
    if (!(grams > 0)) return toast("Bitte eine Menge eintragen.", { error: true });
    try {
      await api(`food-log/${id}`, { method: "PATCH", body: { grams, meal: form.elements.meal.value } });
      closeSheet();
      toast("Gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (err) { formError(form, err); }
  });
  armDelete($("[data-delete]", sheet), () => {
    closeSheet();
    deleteLater(`food-log/${id}`, "Eintrag gelöscht");
  }, "Löschen", "Eintrag löschen?");
}

// "toggle" blubbert nicht hoch – mitlauschen in der Capture-Phase, auch in Sheets
document.addEventListener("toggle", (ev) => {
  const key = ev.target.dataset?.nutri;
  if (key) prefs.set(`nutriOpen.${key}`, ev.target.open);
}, true);

export async function mount(el) {
  root = el;
  data = null; week = null; fast = null; fastRec = null; weekPending = true;   // frisch (z. B. nach Profilwechsel)
  el.addEventListener("click", (ev) => {
    const d = ev.target.closest("[data-day]");
    if (d) {
      const cur = parseYmd(data.date);
      cur.setDate(cur.getDate() + Number(d.dataset.day));
      day = iso(cur) >= todayIso() ? null : iso(cur);
      return load();   // alter Stand bleibt gedimmt stehen, bis der neue Tag da ist
    }
    const switchTo = ev.target.closest("[data-view]");
    if (switchTo) {
      view = switchTo.dataset.view === "fasten" ? "fasten" : "essen";
      prefs.set("nutriView", view);
      render();
      if (view === "fasten") loadFastTips();
      return;
    }
    const add = ev.target.closest("[data-add]");
    if (add) return openAdd(add.dataset.add);
    const menu = ev.target.closest("[data-meal-menu]");
    if (menu) return openMealMenu(menu.dataset.mealMenu);
    if (ev.target.closest("[data-details]")) {
      return openNutrients("Alle Nährwerte", visibleEntries(), targets(data.goals));
    }
    const entry = ev.target.closest("[data-entry]");
    if (entry) openEntry(Number(entry.dataset.entry));
    const water = ev.target.closest("[data-water]");
    if (water) return addWater(Number(water.dataset.water));
    if (ev.target.closest("[data-fast-start]")) return openFastStart();
    if (ev.target.closest("[data-fast-edit]")) return openFastEdit();
    if (ev.target.closest("[data-fast-assist]")) return openFastAssistant({ onDone: loadFast });
    if (ev.target.closest("[data-fast-advice]")) return requestAdvice();
    const stop = ev.target.closest("[data-fast-stop]");
    if (stop) return confirmStop(stop);
  });
  // Zeit laeuft weiter, ohne dass die Seite neu geladen wird. Ein Timer, auch nach erneutem mount().
  clearInterval(fastTimer);
  fastTimer = setInterval(tickFast, 30000);
  document.addEventListener("visibilitychange", tickFast);
  window.addEventListener("outbox-changed", () => { if (data) tickFast(); });
}

/** Beenden mit zweitem Tipp gegen Fehltipper – offline laesst sich ein Fasten nicht mehr korrigieren. */
function confirmStop(button) {
  if (button.dataset.armed) return stopFast();
  button.dataset.armed = "1";
  const label = button.lastChild;
  const old = label.textContent;
  label.textContent = " Wirklich beenden?";
  button.classList.add("armed");
  setTimeout(() => {
    if (!button.isConnected) return;
    delete button.dataset.armed;
    button.classList.remove("armed");
    label.textContent = old;
  }, 3000);
}

async function addWater(ml) {
  const body = { profile_id: getProfile(), ml, date: entryDay(data?.date), client_id: outbox.uuid() };
  waterExtra += ml;   // sofort sichtbar, nicht erst nach der Antwort vom Pi
  render();
  try {
    const res = await outbox.send({ path: "water-log", body });
    waterExtra -= ml;
    // Erfolgreich gespeichert: gleich in die Bilanz einrechnen statt neu vom Server zu laden.
    // Im Ausgang gelandet (offline): pendingWater() zaehlt den Eintrag ab jetzt mit.
    if (!res.queued) data.water_ml = Math.max(0, data.water_ml + ml);
    render();
  } catch (e) {
    waterExtra -= ml;
    render();
    toast(e.message, { error: true });
  }
}
