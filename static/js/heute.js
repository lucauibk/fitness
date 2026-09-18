/** Heute: Wochen-Streak, Check-in mit Readiness, heutiges (angepasstes) Training,
 *  Mobility-Routine und Kalorienbilanz – alles auf einen Blick. */
import { $, api, closeSheet, empty, esc, fmtWeight, getProfile, GROUPS, icon, openSheet,
  relDate, setsSummary, skeleton, toast, WEEKDAYS_LONG, busy } from "./util.js";
import { startDraft, hasDraft } from "./training.js";
import { weekStrip } from "./erfolge.js";
import { ring } from "./ernaehrung.js";
import * as outbox from "./outbox.js";

let root;
let data = null;
let editingCheckin = false;
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist
const dayCache = new Map();

const SCALES = {
  sleep: ["Schlaf", ["sehr schlecht", "schlecht", "okay", "gut", "sehr gut"]],
  energy: ["Energie", ["leer", "müde", "normal", "fit", "voller Energie"]],
  stress: ["Stress", ["entspannt", "gering", "mittel", "hoch", "sehr hoch"]],
};
const SORE = ["–", "leicht", "mittel", "stark"];

// ------------------------------------------------------------ Check-in
function checkinForm(c) {
  const v = { sleep: c?.sleep ?? 3, energy: c?.energy ?? 3, stress: c?.stress ?? 3 };
  const sore = c?.soreness || {};
  return `<div class="card checkin" data-checkin-form>
    <p class="eyebrow">Wie geht's dir heute?</p>
    ${Object.entries(SCALES).map(([k, [label, words]]) => `
      <div class="scale" data-scale="${k}">
        <span class="scale-label">${label}</span>
        <div class="scale-dots" role="radiogroup" aria-label="${label}">
          ${words.map((w, i) => `<button type="button" role="radio" class="dot-btn ${v[k] === i + 1 ? "on" : ""}"
            data-value="${i + 1}" aria-checked="${v[k] === i + 1}" aria-label="${label}: ${w}">${i + 1}</button>`).join("")}
        </div>
        <span class="scale-word" data-word>${words[v[k] - 1]}</span>
      </div>`).join("")}
    <p class="scale-label">Muskelkater</p>
    ${Object.entries(GROUPS).map(([g, l]) => `
      <div class="scale sore-scale" data-sore-group="${g}">
        <span class="scale-label">${l}</span>
        <div class="scale-dots" role="radiogroup" aria-label="Muskelkater ${l}">
          ${SORE.map((w, lvl) => `<button type="button" role="radio" class="dot-btn lvl-${lvl} ${(sore[g] || 0) === lvl ? "on" : ""}"
            data-sore="${g}" data-value="${lvl}" aria-checked="${(sore[g] || 0) === lvl}" aria-label="${l}: ${w}">${lvl}</button>`).join("")}
        </div>
        <span class="scale-word" data-word>${SORE[sore[g] || 0]}</span>
      </div>`).join("")}
    <div class="btn-row"><button type="button" class="btn primary" data-save-checkin>${icon("check")} Check-in speichern</button>
      ${c ? `<button type="button" class="btn ghost" data-cancel-checkin>Abbrechen</button>` : ""}</div>
  </div>`;
}

function readinessCard(c) {
  const lvl = c.level;
  const tone = { voll: "ok", leichter: "soon", erholung: "urgent" }[lvl.key];
  const sore = Object.entries(c.soreness || {}).filter(([, v]) => v >= 2).map(([g]) => GROUPS[g]);
  return `<div class="card readiness ${tone}">
    <div class="readiness-ring" style="--p:${c.readiness}"><b>${c.readiness}</b><small>Readiness</small></div>
    <div class="grow">
      <p class="eyebrow">Check-in</p>
      <h3 class="card-title">${esc(lvl.title)}</h3>
      <p class="hint">${esc(lvl.text)}</p>
      ${sore.length ? `<p class="hint">Muskelkater: ${esc(sore.join(", "))}</p>` : ""}
      <button type="button" class="link-btn" data-edit-checkin>Ändern</button>
    </div>
  </div>`;
}

// --------------------------------------------------------------- Karten
function exerciseRow(e) {
  const s = e.suggest;
  const range = e.target_reps_min === e.target_reps_max ? `${e.target_reps_min}` : `${e.target_reps_min}–${e.target_reps_max}`;
  return `<div class="row-item">
    <div class="row-main">
      <div class="row-title">${esc(e.name)}</div>
      <div class="row-meta">
        <span>${e.target_sets} × ${range}</span>
        ${e.adjusted ? `<span class="soon-text">${esc(e.adjusted)}</span>`
          : e.last?.length ? `<span>zuletzt ${esc(setsSummary(e.last, e.unit))}</span>` : ""}
      </div>
    </div>
    <span class="suggest ${s.raise_weight ? "up" : ""}" title="${esc(s.reason)}">
      ${s.raise_weight ? icon("up") : ""}${esc(s.weight != null ? fmtWeight(s.weight, e.unit) : `${s.reps} Whd.`)}
    </span>
  </div>`;
}

function workoutCard() {
  const d = data;
  if (!d.split) {
    return `<div class="card">${empty("Noch kein Trainingsplan",
      "Lass dir im Profil einen Plan erstellen oder leg unter Training → Plan selbst einen an.",
      { label: "Plan erstellen", attrs: 'data-goto="profil"' })}</div>`;
  }
  const day = d.day;
  if (!day) return "";
  const running = hasDraft();
  const trained = d.streak.trained_today;
  const weekday = WEEKDAYS_LONG[(new Date().getDay() + 6) % 7];
  return `<div class="card hero">
      <p class="eyebrow">${d.fixed ? `${esc(weekday)} · Training` : "Als Nächstes dran"}${trained ? " · heute schon aktiv ✓" : ""}</p>
      <h3 class="hero-title">${esc(day.label)}</h3>
      <p class="hint">${day.exercises.length} Übungen · ${day.exercises.reduce((n, e) => n + e.target_sets, 0)} Sätze
        ${d.position ? ` · Tag ${d.position} von ${d.day_count}` : ""}</p>
      ${d.sore_today?.length ? `<p class="callout">${icon("warn")} Muskelkater in ${esc(d.sore_today.map((g) => GROUPS[g]).join(", "))} –
        die Sätze sind reduziert.${d.alternative ? ` Alternativ heute <button type="button" class="link-btn" data-start="${d.alternative.id}">${esc(d.alternative.label)} trainieren</button>.` : ""}</p>` : ""}
      <div class="btn-row">
        ${running
          ? `<a class="btn primary" href="#training/einheit">${icon("play")} Einheit fortsetzen</a>`
          : `<button type="button" class="btn primary" data-start="${day.id}">${icon("play")} Einheit starten</button>`}
        <button type="button" class="btn ghost" data-pick>Anderer Tag</button>
      </div>
    </div>
    <details class="group-details">
      <summary class="group-title">Übungen<span class="count-muted">Vorschlag fürs nächste Mal</span>${icon("down", "chev")}</summary>
      <div class="list">${day.exercises.map(exerciseRow).join("")
        || `<p class="hint pad">Für diesen Tag sind noch keine Übungen eingetragen.</p>`}</div>
    </details>`;
}

function mobilityCard() {
  const m = data.mobility;
  return `<div class="card mini-card" role="button" tabindex="0" data-mobility="${m.key}">
    <span class="row-icon">${icon("stretch")}</span>
    <div class="grow"><p class="eyebrow">Mobility · ${m.minutes} min</p>
      <div class="card-title">${esc(m.title)}</div><p class="hint">${esc(m.text)}</p></div>
    ${m.done ? `<span class="pill ok">erledigt</span>` : icon("right", "chev")}
  </div>`;
}

function nutritionCard() {
  const n = data.nutrition;
  const pend = outbox.pending("food-log").filter((e) => !e.date || e.date === data.date)
    .reduce((s, e) => s + (e.preview?.kcal || 0), 0);
  const eaten = n.totals.kcal + pend;
  return `<a class="card mini-card" href="#ernaehrung">
    ${ring(eaten, n.goals.kcal, { size: 72 })}
    <div class="grow"><p class="eyebrow">Ernährung</p>
      <div class="card-title">${Math.round(eaten)} ${n.goals.kcal ? `von ${n.goals.kcal} ` : ""}kcal</div>
      <p class="hint">Eiweiß ${Math.round(n.totals.protein)}${n.goals.protein ? ` / ${n.goals.protein}` : ""} g</p></div>
    ${icon("right", "chev")}
  </a>`;
}

function waterCard() {
  const n = data.nutrition;
  const pend = outbox.pending("water-log").filter((e) => !e.date || e.date === data.date)
    .reduce((s, e) => s + e.ml, 0);
  const ml = n.water_ml + pend + waterExtra;
  const liters = (v) => (v / 1000).toLocaleString("de-AT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return `<div class="card mini-card water-mini">
    <span class="row-icon">${icon("water")}</span>
    <div class="grow"><p class="eyebrow">Wasser</p>
      <div class="card-title">${liters(ml)}${n.water_goal ? ` von ${liters(n.water_goal)}` : ""} L</div></div>
    <button type="button" class="icon-btn tonal-icon" data-water="-500" aria-label="Halben Liter abziehen">${icon("minus")}</button>
    <button type="button" class="icon-btn tonal-icon" data-water="500" aria-label="Halben Liter hinzufügen">${icon("plus")}</button>
  </div>`;
}

function render() {
  const d = data;
  const showForm = !d.checkin || editingCheckin;
  root.innerHTML = `<div class="streak-head">
      <span class="streak-count">${icon("flame")} <b>${d.streak.daily}</b> ${d.streak.daily === 1 ? "Tag" : "Tage"} in Folge</span>
      ${d.split ? `<span class="hint">${esc(d.split.name)}</span>` : ""}
    </div>
    ${weekStrip(d.streak.week)}
    ${showForm ? checkinForm(d.checkin) : readinessCard(d.checkin)}
    ${workoutCard()}
    ${mobilityCard()}
    ${nutritionCard()}
    ${waterCard()}
    ${d.last_workout ? `<p class="hint pad">Letzte Einheit: ${esc(d.last_workout.day_label || "Training")} · ${esc(relDate(d.last_workout.at, { time: false }))}</p>` : ""}`;
}

export async function load() {
  // Nicht mitten im Check-in neu zeichnen
  if (data && root.querySelector("[data-checkin-form].touched")) return;
  if (!data) root.innerHTML = skeleton(5);
  data = await api("today");
  render();
  prefetchDays();
}

/** Pläne der anderen Tage still mitladen – fürs Starten ohne Empfang. */
function prefetchDays() {
  const others = (data.days || []).filter((d) => d.id !== data.day?.id);
  others.forEach((d, i) => setTimeout(() => {
    fetch(`/api/day/${d.id}?profile=${getProfile()}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((plan) => { if (plan) dayCache.set(d.id, plan); })
      .catch(() => {});
  }, 400 * (i + 1)));
}

// --------------------------------------------------------------- Aktionen
function pickDay() {
  if (!data?.days?.length) return;
  const sheet = openSheet({
    title: "Trainingstag wählen",
    body: `<div class="list">${data.days.map((d) => `
      <div class="row-item" role="button" tabindex="0" data-pick-day="${d.id}">
        <div class="row-main"><div class="row-title">${esc(d.label)}</div>
          ${d.weekdays?.length ? `<div class="row-meta"><span>${d.weekdays.map((i) => WEEKDAYS_LONG[i]).join(", ")}</span></div>` : ""}
        </div>${icon("right", "chev")}
      </div>`).join("")}</div>`,
  });
  sheet.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-pick-day]");
    if (t) { closeSheet(); begin(Number(t.dataset.pickDay)); }
  });
}

async function begin(dayId) {
  const known = data?.day?.id === dayId ? data.day : dayCache.get(dayId);
  try {
    startDraft(known || await api(`day/${dayId}`));
    location.hash = "training/einheit";
  } catch (e) {
    toast(e.offline ? "Dieser Trainingstag wurde noch nicht geladen – online einmal öffnen." : e.message, { error: true });
  }
}

async function saveCheckin(btn) {
  const form = root.querySelector("[data-checkin-form]");
  const value = (k) => Number(form.querySelector(`[data-scale="${k}"] .on`)?.dataset.value || 3);
  const soreness = {};
  form.querySelectorAll("[data-sore-group]").forEach((group) => {
    const lvl = Number(group.querySelector(".on")?.dataset.value || 0);
    if (lvl) soreness[group.dataset.soreGroup] = lvl;
  });
  const body = { profile_id: getProfile(), sleep: value("sleep"), energy: value("energy"),
    stress: value("stress"), soreness, client_id: outbox.uuid() };
  await busy(btn, async () => {
    try {
      const res = await outbox.send({ path: "checkin", body });
      editingCheckin = false;
      form.classList.remove("touched");
      if (res.queued) toast("Check-in gespeichert – wird gesendet, sobald wieder Verbindung da ist");
      await load();
    } catch (e) { toast(e.message, { error: true }); }
  });
}

// Mobility-Routine Schritt für Schritt
async function openMobility(key) {
  let r;
  try { r = await api(`mobility/${key}`); } catch (e) { return toast(e.message, { error: true }); }
  let i = 0;
  let timer = null;
  let left = 0;
  const sheet = openSheet({ title: esc(r.title), body: "", wide: true });
  const body = $(".sheet-body", sheet);
  const stop = () => { clearInterval(timer); timer = null; };
  function paint() {
    const m = r.moves[i];
    const last = i === r.moves.length - 1;
    body.innerHTML = `<div class="steps-bar">${r.moves.map((_, k) => `<i class="${k <= i ? "on" : ""}"></i>`).join("")}</div>
      <p class="eyebrow">Übung ${i + 1} von ${r.moves.length}</p>
      <h3 class="hero-title">${esc(m.name)}</h3>
      <p>${esc(m.text)}</p>
      <div class="move-target">${m.seconds
        ? `<b data-left>${left || m.seconds}</b><span>Sekunden</span>
           <button type="button" class="btn tonal" data-timer>${icon(timer ? "pause" : "play")} ${timer ? "Pause" : "Start"}</button>`
        : `<b>${m.reps}</b><span>Wiederholungen</span>`}</div>
      <div class="btn-row wide">
        ${i ? `<button type="button" class="btn ghost" data-prev>${icon("left")} Zurück</button>` : ""}
        <button type="button" class="btn primary" data-next>${last ? `${icon("check")} Fertig` : `Weiter ${icon("right")}`}</button>
      </div>`;
  }
  function tick() {
    left -= 1;
    if (!sheet.isConnected) return stop();
    const el = $("[data-left]", sheet);
    if (el) el.textContent = Math.max(left, 0);
    if (left <= 0) {
      stop();
      navigator.vibrate?.(150);
      if (i < r.moves.length - 1) { i += 1; left = 0; }
      paint();
    }
  }
  sheet.addEventListener("click", async (ev) => {
    const t = (s) => ev.target.closest(s);
    if (t("[data-timer]")) {
      if (timer) stop();
      else { left = left || r.moves[i].seconds; timer = setInterval(tick, 1000); }
      return paint();
    }
    if (t("[data-prev]")) { stop(); left = 0; i -= 1; return paint(); }
    const next = t("[data-next]");
    if (!next) return;
    stop();
    left = 0;
    if (i < r.moves.length - 1) { i += 1; return paint(); }
    try {
      const res = await outbox.send({ path: "mobility/log",
        body: { profile_id: getProfile(), routine: key, client_id: outbox.uuid() } });
      closeSheet();
      toast(res.queued ? "Erledigt – wird gesendet, sobald wieder Verbindung da ist" : "Mobility erledigt 💪");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { toast(e.message, { error: true }); }
  });
  paint();
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const t = (s) => ev.target.closest(s);
    // Zwei radiogroups teilen sich .dot-btn (Schlaf/Energie/Stress vs. Muskelkater je
    // Gruppe) – data-sore zuerst pruefen, sonst greift der generische Zweig daneben.
    const sore = t("[data-sore]");
    if (sore) {
      const group = sore.closest("[data-sore-group]");
      group.querySelectorAll("[data-sore]").forEach((b) => {
        const on = b === sore;
        b.classList.toggle("on", on);
        b.setAttribute("aria-checked", on);
      });
      $("[data-word]", group).textContent = SORE[Number(sore.dataset.value)];
      group.closest("[data-checkin-form]").classList.add("touched");
      return;
    }
    const dot = t(".dot-btn");
    if (dot) {
      const scale = dot.closest("[data-scale]");
      scale.querySelectorAll(".dot-btn").forEach((b) => {
        const on = b === dot;
        b.classList.toggle("on", on);
        b.setAttribute("aria-checked", on);
      });
      $("[data-word]", scale).textContent = SCALES[scale.dataset.scale][1][Number(dot.dataset.value) - 1];
      dot.closest("[data-checkin-form]").classList.add("touched");
      return;
    }
    const save = t("[data-save-checkin]");
    if (save) return saveCheckin(save);
    if (t("[data-edit-checkin]")) { editingCheckin = true; return render(); }
    if (t("[data-cancel-checkin]")) { editingCheckin = false; return render(); }
    const start = t("[data-start]");
    if (start) return begin(Number(start.dataset.start));
    if (t("[data-pick]")) return pickDay();
    const mob = t("[data-mobility]");
    if (mob) return openMobility(mob.dataset.mobility);
    const go = t("[data-goto]");
    if (go) location.hash = go.dataset.goto;
    const water = t("[data-water]");
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
    // Im Ausgang gelandet (offline): pendingWater() (in waterCard) zaehlt den Eintrag ab jetzt mit.
    if (!res.queued) data.nutrition.water_ml = Math.max(0, data.nutrition.water_ml + ml);
    render();
  } catch (e) {
    waterExtra -= ml;
    render();
    toast(e.message, { error: true });
  }
}
