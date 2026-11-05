/** Ernährung: Tagesbilanz, Mahlzeiten, Lebensmittel suchen/scannen/anlegen. */
import { $, api, armDelete, closeSheet, deleteLater, esc, formData, formError, getProfile, icon,
  isPendingDelete, openSheet, prefs, skeleton, submitOnce, toast, busy, parseYmd } from "./util.js";
import * as outbox from "./outbox.js";
import { scanBarcode } from "./scanner.js";

let root;
let data = null;
let day = null;   // YYYY-MM-DD, null = heute
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist

// Der Pi wartet bei Open Food Facts bis zu 12 s je Abruf (app/nutrition.py) – das
// Standard-Zeitlimit von api() wuerde vorher aufgeben und "offline" melden.
const OFF_TIMEOUT = 30000;
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

/** Naehrwerte als Donut: Anteil an den kcal aus Eiweiss, Kohlenhydraten und Fett. */
function donutCard(t, g, extra = "") {
  const parts = [["Proteine", t.protein, 4, "var(--c-protein)", g.protein], ["Carbs", t.carbs, 4, "var(--c-carbs)", g.carbs],
    ["Fette", t.fat, 9, "var(--c-fat)", g.fat]];
  const total = parts.reduce((n, [, v, k]) => n + (v || 0) * k, 0) || 1;
  const r = 44, c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = parts.map(([, v, k, color]) => {
    const len = ((v || 0) * k / total) * c;
    const el = len > 0 ? `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${color}" stroke-width="12"
      stroke-dasharray="${Math.max(0, len - 3).toFixed(1)} ${c.toFixed(1)}" stroke-dashoffset="${(-offset).toFixed(1)}"
      transform="rotate(-90 60 60)"/>` : "";
    offset += len;
    return el;
  }).join("");
  return `<div class="card donut-card"><p class="eyebrow">Nährwerte</p>
    <div class="donut-row">
      <svg viewBox="0 0 120 120" width="132" height="132" role="img" aria-label="${num(t.kcal)} kcal, Eiweiß ${num(t.protein)} g, Kohlenhydrate ${num(t.carbs)} g, Fett ${num(t.fat)} g">
        <circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="12"/>${arcs}
        <text x="60" y="58" text-anchor="middle" class="ring-value">${num(t.kcal)}</text>
        <text x="60" y="76" text-anchor="middle" class="ring-label">kcal</text>
      </svg>
      <ul class="donut-legend">${parts.map(([label, v, , color, goal]) => `<li><i style="background:${color}"></i>
        <span>${label}</span><b>${num(v)} g</b>${goal ? `<small>von ${num(goal)}</small>` : ""}</li>`).join("")}</ul>
    </div>${extra}</div>`;
}

function macro(label, value, goal) {
  const pct = goal ? Math.min(100, (value / goal) * 100) : 0;
  return `<div class="macro"><span>${label}</span>
    <span class="bar"><i style="width:${pct}%"></i></span>
    <span class="macro-val">${num(value)}${goal ? ` / ${num(goal)}` : ""} g</span></div>`;
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
      ${ref ? `<span class="bar"><i style="width:${known ? pct : 0}%"></i></span>` : "<span></span>"}
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

function glass(ml, goal) {
  const pct = goal ? Math.min(1, Math.max(0, ml / goal)) : 0;
  const top = 12, bottom = 88, h = (bottom - top) * pct;
  const shape = "M9 10h42l-6.5 78a4 4 0 0 1-4 3.7H19.5a4 4 0 0 1-4-3.7z";
  return `<svg class="glass-svg" viewBox="0 0 60 100" width="60" height="100" role="img"
      aria-label="${num(ml)} von ${goal ? num(goal) : "?"} ml Wasser getrunken">
    <clipPath id="glassClip"><path d="${shape}"/></clipPath>
    <path d="${shape}" class="glass-outline"/>
    ${h > 0 ? `<rect x="3" y="${bottom - h}" width="54" height="${h + 12}" class="glass-fill" clip-path="url(#glassClip)"/>` : ""}
  </svg>`;
}

function waterCard(ml, goal) {
  return `<div class="card water-card">
    <div class="card-title">${icon("water")} Wasser</div>
    <div class="water-row">
      <button type="button" class="icon-btn tonal-icon" data-water="-500" aria-label="Halben Liter abziehen">${icon("minus")}</button>
      ${glass(ml, goal)}
      <button type="button" class="icon-btn tonal-icon" data-water="500" aria-label="Halben Liter hinzufügen">${icon("plus")}</button>
    </div>
    <p class="water-total">${liters(ml)}${goal ? ` von ${liters(goal)}` : ""} L</p>
  </div>`;
}

function render() {
  const g = data.goals.goals || {};
  const t = data.totals;
  const pend = pendingEntries();
  const pendKcal = pend.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
  const isToday = data.date === todayIso();
  const dateLabel = isToday ? "Heute" : parseYmd(data.date).toLocaleDateString("de-AT", { weekday: "long", day: "numeric", month: "long" });
  root.innerHTML = `<div class="date-switch">
      <button type="button" class="icon-btn" data-day="-1" aria-label="Vorheriger Tag">${icon("left")}</button>
      <b>${esc(dateLabel)}</b>
      <button type="button" class="icon-btn" data-day="1" aria-label="Nächster Tag" ${isToday ? "disabled" : ""}>${icon("right")}</button>
    </div>
    <div class="card nutri-head">
      <div class="nutri-stat"><b>${num(t.kcal + pendKcal)}</b><span>Gegessen</span></div>
      ${ring(t.kcal + pendKcal, g.kcal ? g.kcal + (data.burned_kcal || 0) : g.kcal)}
      <div class="nutri-stat"><b>${num(data.burned_kcal || 0)}</b><span>Verbrannt</span></div>
    </div>
    ${t.kcal ? donutCard(t, g, dayDetails()) : ""}
    ${!g.kcal ? `<p class="callout">${icon("warn")} Für ein Kalorienziel fehlen noch: ${esc((data.goals.missing || []).join(", "))}.
      <a href="#profil">Im Profil ergänzen</a></p>` : ""}
    ${waterCard(data.water_ml + pendingWater() + waterExtra, data.water_goal)}
    ${Object.entries(data.meals).map(([key, m]) => {
      const extra = pend.filter((e) => e.meal === key);
      const visible = m.entries.filter((e) => !isPendingDelete(`food-log/${e.id}`));
      const kcal = m.kcal - m.entries.filter((e) => isPendingDelete(`food-log/${e.id}`))
        .reduce((n, e) => n + (e.kcal || 0), 0) + extra.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
      return `<div class="card meal">
        <div class="ex-head"><div><div class="card-title">${esc(m.label)}</div>
          <div class="row-meta"><span>${num(kcal)} kcal</span></div></div>
          <button type="button" class="icon-btn tonal-icon" data-add="${key}" aria-label="${esc(m.label)} hinzufügen">${icon("plus")}</button>
        </div>
        ${visible.length || extra.length ? `<div class="list flush">
          ${visible.map((e) => `<div class="row-item" role="button" tabindex="0" data-entry="${e.id}">
            <div class="row-main"><div class="row-title">${esc(e.name)}</div>
              <div class="row-meta"><span>${isQuick(e) ? "Schnelleintrag" : `${portionsText(e)}${num(e.grams)} g`}</span>${
                e.protein || e.carbs || e.fat ? `<span>E ${num(e.protein)} · K ${num(e.carbs)} · F ${num(e.fat)}</span>` : ""}</div></div>
            <span class="row-aside">${num(e.kcal)} kcal</span></div>`).join("")}
          ${extra.map((e) => `<div class="row-item is-pending"><div class="row-main">
            <div class="row-title">${esc(e.preview?.name || "Eintrag")}</div>
            <div class="row-meta"><span>${e.grams ? `${num(e.grams)} g` : "Schnelleintrag"}</span><span class="pill">wartet</span></div></div>
            <span class="row-aside">${num(e.preview?.kcal)} kcal</span></div>`).join("")}
        </div>${visible.length ? mealDetails(key, visible) : ""}<button type="button" class="link-btn copy-meal" data-save-meal="${key}">${icon("download")} Als Mahlzeit speichern</button>`
        : `<div class="btn-row"><button type="button" class="link-btn copy-meal" data-copy-meal="${key}">${icon("refresh")} Wie am Vortag</button>
          <button type="button" class="link-btn copy-meal" data-saved-meal="${key}">${icon("food")} Gespeicherte Mahlzeit</button></div>`}
      </div>`;
    }).join("")}
    <p class="hint pad source-note">Nährwerte: Bundeslebensmittelschlüssel (BLS) 4.0, Max Rubner-Institut, CC BY 4.0 · Markenprodukte: Open Food Facts, ODbL</p>`;
}

const visibleEntries = () => Object.values(data.meals).flatMap((m) => m.entries)
  .filter((e) => !isPendingDelete(`food-log/${e.id}`));

function dayDetails() {
  const entries = visibleEntries();
  const t = sumEntries(entries);
  return detailsBox("day", "Alle Nährwerte", nutrientRows(t, targets(data.goals), entries.length) + incomplete(t, entries.length));
}

function mealDetails(key, entries) {
  const t = sumEntries(entries);
  const day = sumEntries(visibleEntries()).kcal;
  const share = day ? ` · ${Math.round((t.kcal / day) * 100)} % des Tages` : "";
  return detailsBox(key, `Nährwerte${share}`, nutrientRows(t, {}, entries.length) + incomplete(t, entries.length));
}

/** Dieselbe Mahlzeit wie am Vortag eintragen – das Fruehstueck ist oft jeden Tag gleich.
 *  Jeder Eintrag geht einzeln durch den Ausgang, also auch offline. */
async function copyMeal(meal, btn) {
  const prev = parseYmd(data.date);
  prev.setDate(prev.getDate() - 1);
  await busy(btn, async () => {
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
          profile_id: getProfile(), ...what, meal, date: data.date,
          client_id: outbox.uuid(), preview: { name: e.name, kcal: Math.round(e.kcal || 0) } } });
        if (res.queued) queued += 1;
      }
    } catch (e) { return toast(e.message, { error: true }); }
    toast(queued ? `${entries.length} Einträge übernommen – werden gesendet, sobald wieder Verbindung da ist`
      : `${MEALS[meal]} vom Vortag übernommen (${entries.length} ${entries.length === 1 ? "Eintrag" : "Einträge"})`);
    window.dispatchEvent(new CustomEvent("data-changed"));
  });
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
          grams: it.grams, meal, date: data.date, client_id: outbox.uuid(), preview: { name: it.name, kcal: Math.round(it.kcal || 0) } } });
        if (res.queued) queued += 1;
      }
    } catch (e) { return toast(e.message, { error: true }); }
    toast(queued ? `${m.name} übernommen – wird gesendet, sobald wieder Verbindung da ist` : `${m.name} eingetragen`);
    window.dispatchEvent(new CustomEvent("data-changed"));
  });
}

export async function load() {
  if (!data) root.innerHTML = skeleton(5);
  data = await api(`nutrition/day?date=${day || todayIso()}`);
  render();
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

function foodRow(f, i, q) {
  // Schon mal gegessen: die zuletzt genommene Menge ist, was man mit einem Tipp uebernimmt –
  // also deren kcal als Hauptzahl, nicht mal pro Portion und mal pro 100 g.
  if (f.last_grams) {
    const meta = [f.brand && esc(f.brand), `${num(f.kcal)} kcal / 100 g`].filter(Boolean);
    return `<div class="row-item food-row" role="button" tabindex="0" data-pick="${i}">
      <div class="row-main"><div class="row-title">${highlight(f.name, q)}</div>
        <div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div></div>
      <span class="row-aside food-kcal"><b>${num(kcalFor(f, f.last_grams))} kcal</b><small>${portionsText({ ...f, grams: f.last_grams })}${num(f.last_grams)} g wie zuletzt</small></span></div>`;
  }
  // Erste natuerliche Einheit als Hauptzahl: "1 Stück (mittel) = 87 kcal" sagt mehr als "58 / 100 g"
  const u = unitsOf(f)[0];
  const meta = [f.brand && esc(f.brand), u && esc(`${u.label} · ${num(u.g)} g`)].filter(Boolean);
  return `<div class="row-item food-row" role="button" tabindex="0" data-pick="${i}">
    <div class="row-main"><div class="row-title">${highlight(f.name, q)}</div>
      ${meta.length ? `<div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>` : ""}</div>
    <span class="row-aside food-kcal">${u ? `<b>${num(kcalFor(f, u.g))} kcal</b><small>pro ${esc(u.label.replace(/ \(.*\)$/, ""))}</small><small>${num(f.kcal)} / 100 g</small>`
      : `<b>${num(f.kcal)} kcal</b><small>pro 100 g</small>`}</span></div>`;
}

/** Menge in Portionen oder Gramm. Gespeichert werden immer Gramm. */
function amountBox(box, f, grams, onChange) {
  const units = unitsOf(f);
  const hit = grams ? asUnits(f, grams) : null;
  // unit: Index in units, -1 = Gramm. Ohne Vorgabe: erste Einheit x 1, sonst 100 g.
  let unit = hit ? hit.i : (!grams && units.length ? 0 : -1);
  let value = hit ? hit.n : (unit >= 0 ? 1 : Math.round(grams || 100));
  const current = () => (unit >= 0 ? Math.round(value * units[unit].g * 10) / 10 : value);
  const pack = f.pack_g > 0 && f.pack_g <= 1000 ? Math.round(f.pack_g) : null;
  const unitText = (u) => (/\d\s*(g|ml)\b/i.test(u.label) ? u.label : `${u.label} · ${num(u.g)} g`);

  function paint() {
    const gramChips = [...new Set([f.last_grams, pack, 50, 100, 150, 200].filter(Boolean).map(Math.round))].slice(0, 6);
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
        <div class="chips">${[0.5, 1, 2, 3, 4].map((n) => `<button type="button" class="chip${n === value ? " active" : ""}" data-set="${n}">${count(n)}</button>`).join("")}</div>`
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
      const input = box.querySelector("[data-count], [data-grams]");
      input.value = value;
      changed();
    }
  });
  box.addEventListener("input", (ev) => {
    if (!ev.target.matches("[data-count], [data-grams]")) return;
    value = Number(ev.target.value) || 0;
    changed();
  });
  paint();
  onChange(current());
  return { grams: current };
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

/** Hinzufuegen-Sheet. opts.onPick: Auswahl-Modus fuer Rezept-Zutaten (statt ins Tagebuch);
 *  opts.food: gleich mit diesem Lebensmittel bei der Menge starten. */
function openAdd(meal, opts = {}) {
  const picking = typeof opts.onPick === "function";
  const st = { meal, local: [], online: [], recent: [], favorites: [], items: [], query: "", food: null, error: null,
    searchingLocal: false, searchingOnline: false };
  const sheet = openSheet({ title: picking ? "Zutat hinzufügen" : `${MEALS[meal]} · hinzufügen`, body: "", wide: true });
  const body = $(".sheet-body", sheet);
  let localTimer = null;
  let onlineTimer = null;
  let amount = null;

  function section(title, items, empty) {
    const from = st.items.length;
    st.items.push(...items);
    return `<h3 class="form-section">${title}</h3>
      ${items.length ? `<div class="list">${items.map((f, i) => foodRow(f, from + i, st.query)).join("")}</div>` : empty}`;
  }

  function paintList() {
    const box = $("[data-results]", sheet);
    if (!box) return;
    st.items = [];
    if (st.query.length < 2) {
      const favIds = new Set(st.favorites.map((f) => f.id));
      box.innerHTML = (st.favorites.length ? section(`${icon("star")} Favoriten`, st.favorites, "") : "")
        + section("Zuletzt verwendet", st.recent.filter((f) => !favIds.has(f.id)),
          `<p class="hint pad">Noch keine zuletzt verwendeten Lebensmittel.</p>`);
    } else {
      const known = new Set(st.local.map((f) => f.barcode).filter(Boolean));
      const online = st.online.filter((f) => !f.barcode || !known.has(f.barcode));
      box.innerHTML = (st.local.length || st.searchingLocal
        ? section("Lebensmittel", st.local, `<p class="hint pad">Suche …</p>`) : "")
        + section("Markenprodukte (Open Food Facts)", online,
          `<p class="hint pad">${st.searchingOnline ? "Weitere Produkte werden gesucht …" : st.error ? ""
            : st.query.length < 3 ? "Ab drei Buchstaben wird auch hier gesucht." : "Nichts gefunden."}</p>`)
        + (online.length && st.searchingOnline ? `<p class="hint pad">Weitere Produkte werden gesucht …</p>` : "");
    }
    $("[data-error]", sheet).textContent = st.error || "";
  }

  function paintSearch() {
    body.innerHTML = `<label class="search">${icon("search")}<input type="search" data-q value="${esc(st.query)}"
        placeholder="z. B. Topfen, Semmel, Hühnerbrust oder eine Marke" aria-label="Lebensmittel suchen" autocomplete="off"></label>
      <div class="btn-row">
        <button type="button" class="btn small tonal" data-scan>${icon("barcode")} Barcode scannen</button>
        ${picking ? "" : `<button type="button" class="btn small tonal" data-ai>${icon("spark")} Beschreiben / Foto</button>
        <button type="button" class="btn small ghost" data-quick>${icon("flame")} Nur Kalorien</button>
        <button type="button" class="btn small ghost" data-recipe>${icon("food")} Rezept erstellen</button>`}
        <button type="button" class="btn small ghost" data-own>${icon("plus")} Eigenes Lebensmittel</button>
      </div>
      <p class="hint" data-error role="status"></p>
      <div data-results></div>`;
    paintList();
    const input = $("[data-q]", sheet);
    input.addEventListener("input", () => {
      st.query = input.value.trim();
      clearTimeout(localTimer);
      clearTimeout(onlineTimer);
      st.error = null;
      if (st.query.length < 2) { st.local = []; st.online = []; st.searchingOnline = false; return paintList(); }
      // Eigene Lebensmittel sofort, Open Food Facts erst ab drei Zeichen und wenn nicht
      // mehr getippt wird – jede Suche kostet dort mehrere Abrufe (429-Gefahr).
      st.searchingLocal = true;
      st.searchingOnline = st.query.length >= 3;
      localTimer = setTimeout(searchLocal, 200);
      if (st.searchingOnline) onlineTimer = setTimeout(searchOnline, 700);
      else st.online = [];
    });
    if (!st.query) input.focus({ preventScroll: true });
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

  function paintAmount() {
    const f = st.food;
    const u = unitsOf(f)[0];
    body.innerHTML = `<button type="button" class="link-btn" data-back>${icon("left")} Zurück</button>
      <div class="amount-head"><h3 class="card-title">${esc(f.name)}</h3>
        ${f.id ? `<button type="button" class="icon-btn fav-btn${f.favorite ? " on" : ""}" data-fav aria-pressed="${!!f.favorite}"
          aria-label="${f.favorite ? "Aus den Favoriten nehmen" : "Als Favorit merken"}">${icon("star")}</button>` : ""}</div>
      ${f.source === "eigen" && f.id ? `<button type="button" class="link-btn" data-edit-food>${icon("edit")} Lebensmittel bearbeiten</button>` : ""}
      ${f.source === "rezept" && f.id && !picking ? `<button type="button" class="link-btn" data-edit-recipe>${icon("edit")} Rezept bearbeiten</button>` : ""}
      <p class="hint">${f.brand ? `${esc(f.brand)} · ` : ""}je 100 g: ${num(f.kcal)} kcal · E ${num(f.protein, 1)} · K ${num(f.carbs, 1)} · F ${num(f.fat, 1)}
        ${u ? `<br>${esc(/\d\s*(g|ml)\b/i.test(u.label) ? u.label : `${u.label} (${num(u.g)} g)`)}: ${num(kcalFor(f, u.g))} kcal` : ""}</p>
      <div data-amount></div>
      ${picking ? "" : `<label class="field">Mahlzeit<select data-meal>${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === st.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>`}
      <div class="card quiet" data-calc></div>
      <div class="btn-row"><button type="button" class="btn primary" data-save>${icon("check")} ${picking ? "Als Zutat übernehmen" : "Hinzufügen"}</button></div>`;
    amount = amountBox($("[data-amount]", sheet), f, f.last_grams, (g) => {
      const k = (v) => num(((v || 0) * g) / 100, 1);
      $("[data-calc]", sheet).innerHTML = `<b>${k(f.kcal)} kcal</b><span>${num(g, 1)} g · E ${k(f.protein)} g · K ${k(f.carbs)} g · F ${k(f.fat)} g</span>`;
    });
  }

  async function pick(food) {
    st.food = { ...food };
    paintAmount();
    // Die Suche kennt die Portion nur fuer die ersten Treffer – bei Bedarf jetzt nachladen
    if (food.barcode && !food.serving_g && (food.source === "off" || !food.id)) {
      try {
        const p = await api(`foods/portion/${food.barcode}`, { timeout: OFF_TIMEOUT });
        if (st.food?.barcode !== food.barcode || !(p.serving_g || p.pack_g)) return;
        const before = servingOf(st.food);
        Object.entries(p).forEach(([k, v]) => { if (v && !st.food[k]) st.food[k] = v; });
        if (!before && servingOf(st.food)) paintAmount();
      } catch { /* ohne Portion geht es auch */ }
    }
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
    const meal = $("[data-meal]", sheet).value;
    await busy(btn, async () => {
      try {
        const food = await ensureFood(f);
        const body = { profile_id: getProfile(), food_id: food.id, grams, meal, date: data.date,
          client_id: outbox.uuid(),
          preview: { name: food.name, kcal: Math.round((food.kcal * grams) / 100) } };
        const res = await outbox.send({ path: "food-log", body });
        closeSheet();
        toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist"
          : `${food.name} eingetragen`);
        window.dispatchEvent(new CustomEvent("data-changed"));
      } catch (e) {
        toast(e.offline ? "Offline lassen sich nur schon verwendete Lebensmittel eintragen." : e.message, { error: true });
      }
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
      paintSearch();
    }
  }

  /** Eigenes Lebensmittel anlegen – oder, mit preset.id, ein selbst angelegtes bearbeiten. */
  function openOwn(preset = {}) {
    const edit = Boolean(preset.id);
    const v = (k) => esc(preset[k] ?? "");
    body.innerHTML = `<button type="button" class="link-btn" data-back>${icon("left")} Zurück</button>
      <form id="foodForm" class="form">
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
        ${edit ? `<p class="hint">Gilt für alle Profile – eigene Lebensmittel sind gemeinsam. Schon eingetragene Mahlzeiten behalten ihre Werte.</p>` : ""}
        <div class="btn-row">
          ${edit ? `<button type="button" class="btn danger ghost" data-delete-food>${icon("trash")} Löschen</button><span class="grow"></span>` : ""}
          <button type="submit" class="btn primary">${edit ? "Speichern" : "Weiter"}</button>
        </div>
      </form>`;
    const form = $("#foodForm", sheet);
    submitOnce(form, async () => {
      try {
        const food = edit
          ? await api(`foods/${preset.id}`, { method: "PATCH", body: formData(form) })
          : await api("foods", { method: "POST", body: formData(form) });
        if (edit) toast("Lebensmittel gespeichert");
        pick({ ...preset, ...food });
      } catch (e) { formError(form, e); }
    });
    if (edit) {
      armDelete($("[data-delete-food]", sheet), async () => {
        try {
          await api(`foods/${preset.id}`, { method: "DELETE" });
          st.recent = st.recent.filter((f) => f.id !== preset.id);
          st.local = st.local.filter((f) => f.id !== preset.id);
          st.food = null;
          toast("Lebensmittel gelöscht – eingetragene Mahlzeiten bleiben");
          paintSearch();
        } catch (e) { toast(e.message, { error: true }); }
      }, "Löschen", "Wirklich löschen?");
    }
  }

  sheet.addEventListener("click", async (ev) => {
    const t = (s) => ev.target.closest(s);
    const p = t("[data-pick]");
    if (p) return pick(st.items[Number(p.dataset.pick)]);
    if (t("[data-back]")) { st.food = null; return paintSearch(); }
    if (t("[data-edit-food]") && st.food) return openOwn(st.food);
    const s = t("[data-save]");
    if (s) return save(s);
    if (t("[data-own]")) return openOwn();
    if (t("[data-quick]")) return openQuick(st.meal);
    if (t("[data-ai]")) return openAi(st.meal, st.query);
    if (t("[data-recipe]")) return openRecipe(null, st.meal);
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

  if (opts.food) pick(opts.food);
  else paintSearch();
  api("foods/recent").then((r) => { st.recent = r; if (!st.food) paintList(); }).catch(() => {});
  api("foods/favorites").then((r) => { st.favorites = r; if (!st.food) paintList(); }).catch(() => {});
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
            date: data.date, client_id: outbox.uuid(), preview: { name: it.pick >= 0 ? it.candidates[it.pick].name : it.name, kcal: Math.round(kcalOf(it)) } } });
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
        const res = await outbox.send({ path: "food-log", body: { profile_id: getProfile(), ...f, date: data.date,
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
      deleteLater(`food-log/${entry.id}`, "Eintrag gelöscht", { onDone: () => { data = null; } });
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
    deleteLater(`food-log/${id}`, "Eintrag gelöscht", { onDone: () => { data = null; } });
  }, "Löschen", "Eintrag löschen?");
}

// "toggle" blubbert nicht hoch – mitlauschen in der Capture-Phase, auch in Sheets
document.addEventListener("toggle", (ev) => {
  const key = ev.target.dataset?.nutri;
  if (key) prefs.set(`nutriOpen.${key}`, ev.target.open);
}, true);

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const d = ev.target.closest("[data-day]");
    if (d) {
      const cur = parseYmd(data.date);
      cur.setDate(cur.getDate() + Number(d.dataset.day));
      day = iso(cur) >= todayIso() ? null : iso(cur);
      data = null;
      return load();
    }
    const add = ev.target.closest("[data-add]");
    if (add) return openAdd(add.dataset.add);
    const copy = ev.target.closest("[data-copy-meal]");
    if (copy) return copyMeal(copy.dataset.copyMeal, copy);
    const sv = ev.target.closest("[data-save-meal]");
    if (sv) return saveMeal(sv.dataset.saveMeal);
    const sm = ev.target.closest("[data-saved-meal]");
    if (sm) return openSavedMeals(sm.dataset.savedMeal);
    const entry = ev.target.closest("[data-entry]");
    if (entry) openEntry(Number(entry.dataset.entry));
    const water = ev.target.closest("[data-water]");
    if (water) return addWater(Number(water.dataset.water));
  });
}

async function addWater(ml) {
  const body = { profile_id: getProfile(), ml, date: data.date, client_id: outbox.uuid() };
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
