/** Heute: Wochen-Streak, Check-in mit Readiness, heutiges (angepasstes) Training,
 *  Mobility-Routine und Kalorienbilanz – alles auf einen Blick. */
import { $, api, closeSheet, empty, entryDay, esc, reviewHtml, watchCoach, fmtWeight, getProfile, GROUPS, icon, openSheet,
  setsSummary, sheetClosed, skeleton, toast, WEEKDAYS_LONG, busy } from "./util.js";
import { startDraft, hasDraft, getDraft } from "./training.js";
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
const soreWord = (lvl) => (lvl ? SORE[lvl] : "keiner");

// ------------------------------------------------------------ Check-in
function checkinForm(c) {
  const v = { sleep: c?.sleep ?? 3, energy: c?.energy ?? 3, stress: c?.stress ?? 3 };
  // Noch nicht eingecheckt: die 3 ist nur ein Vorschlag – umrandet statt gefuellt, damit sie
  // nicht wie eine schon gespeicherte Antwort aussieht. Speichern geht trotzdem sofort.
  const mark = c ? "on" : "pre";
  const sore = c?.soreness || {};
  const anySore = Object.values(sore).some(Boolean);
  return `<div class="card checkin" data-checkin-form>
    <p class="eyebrow">${c ? "Check-in ändern" : "Wie geht's dir heute?"}</p>
    ${Object.entries(SCALES).map(([k, [label, words]]) => `
      <div class="scale" data-scale="${k}">
        <span class="scale-label">${label}</span>
        <div class="scale-dots" role="radiogroup" aria-label="${label}">
          ${words.map((w, i) => `<button type="button" role="radio" class="dot-btn ${v[k] === i + 1 ? mark : ""}"
            data-value="${i + 1}" aria-checked="${v[k] === i + 1}" tabindex="${v[k] === i + 1 ? 0 : -1}"
            aria-label="${label}: ${w}">${i + 1}</button>`).join("")}
        </div>
        <span class="scale-word" data-word>${words[v[k] - 1]}</span>
      </div>`).join("")}
    <details class="field-more" ${anySore ? "open" : ""}><summary>Muskelkater angeben</summary>
      <p class="hint">Antippen zum Steigern.</p>
      <div class="chips sore">${Object.entries(GROUPS).map(([g, l]) => `<button type="button"
        class="chip sore-${sore[g] || 0}" data-sore="${g}" data-level="${sore[g] || 0}"
        aria-label="Muskelkater ${l}: ${soreWord(sore[g] || 0)}">${l}<small>${SORE[sore[g] || 0]}</small></button>`).join("")}</div>
    </details>
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

// ---------------------------------------------------------- Coach-Hinweise
/** Vorschlaege des taeglichen Coach-Laufs. Nur Hinweise: umgesetzt wird von Hand. */
function coachCard() {
  const notes = data.coach_notes || [];
  if (!notes.length) return "";
  return `<div class="card coach-notes"><p class="eyebrow">${icon("spark")} Coach-Hinweise</p>
    ${notes.map((n) => `<div class="coach-note" data-note="${n.id}">
      <div class="grow"><p class="coach-note-title">${esc(n.title)}${n.exercise ? ` <small>· ${esc(n.exercise)}</small>` : ""}</p>
        <p class="hint">${esc(n.text)}</p></div>
      <button type="button" class="icon-btn" data-dismiss-note="${n.id}" aria-label="Hinweis ausblenden">${icon("x")}</button>
    </div>`).join("")}</div>`;
}

async function dismissNote(btn) {
  const id = Number(btn.dataset.dismissNote);
  btn.disabled = true;
  try {
    await api(`coach-notes/${id}/dismiss`, { method: "POST" });
  } catch (e) {
    btn.disabled = false;
    return toast(e.message, { error: true });
  }
  data.coach_notes = data.coach_notes.filter((n) => n.id !== id);
  const card = btn.closest(".coach-notes");
  if (data.coach_notes.length) btn.closest(".coach-note").remove();
  else card.remove();
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
        ${s.reason && e.last?.length ? `<span>${esc(s.reason)}</span>` : ""}
      </div>
    </div>
    <span class="suggest ${s.raise_weight ? "up" : ""}">
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
  const weekday = WEEKDAYS_LONG[(new Date().getDay() + 6) % 7];
  const tw = d.today_workout;
  // Heute schon trainiert: die Karte sagt das, statt weiter "Einheit starten" anzubieten.
  if (tw && !running) {
    return `<div class="card hero is-done">
      <p class="eyebrow">${icon("check")} Heute erledigt</p>
      <h3 class="hero-title">${esc(tw.day_label)}</h3>
      <p class="hint">${esc(tw.headline)}</p>
      <div class="btn-row">
        <button type="button" class="btn tonal" data-show-workout="${tw.id}">Ansehen</button>
        <button type="button" class="btn ghost" data-pick>Nochmal trainieren</button>
      </div>
    </div>`;
  }
  return `<div class="card hero">
      <p class="eyebrow">${d.fixed ? `${esc(weekday)} · Training`
        : d.note === "nachgeholt" ? "Nachholen · verschoben" : "Als Nächstes dran"}</p>
      <h3 class="hero-title">${esc(day.label)}</h3>
      <p class="hint">${day.exercises.length} Übungen · ${day.exercises.reduce((n, e) => n + e.target_sets, 0)} Sätze
        ${d.position ? ` · Tag ${d.position} von ${d.day_count}` : ""}
        ${d.block ? ` · Woche ${d.block.week}/${d.block.weeks}${d.block.deload ? " (Entlastung)" : ` · ${d.block.rir} in Reserve`}` : ""}</p>
      ${d.block?.deload ? `<p class="callout">${icon("warn")} Entlastungswoche: leichter trainieren, nichts bis ans Limit – so erholen sich Muskeln und Gelenke.</p>` : ""}
      ${d.sore_today?.length ? `<p class="callout">${icon("warn")} Muskelkater in ${esc(d.sore_today.map((g) => GROUPS[g]).join(", "))} –
        die Sätze sind reduziert.${d.alternative ? ` Alternativ heute <button type="button" class="link-btn" data-start="${d.alternative.id}">${esc(d.alternative.label)} trainieren</button>.` : ""}</p>` : ""}
      ${d.note === "erholung" ? `<p class="callout">${icon("warn")} Gestern waren dieselben Muskeln dran – lass sie heute ruhen oder wähle „Anderer Tag“.</p>` : ""}
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
  return `<div class="card nutri-mini">
    <a class="mini-card" href="#ernaehrung">
      ${ring(eaten, n.goals.kcal ? n.goals.kcal + (n.burned_kcal || 0) : n.goals.kcal, { size: 72 })}
      <div class="grow"><p class="eyebrow">Ernährung</p>
        <div class="card-title">${Math.round(eaten)} ${n.goals.kcal ? `von ${n.goals.kcal} ` : ""}kcal</div>
        <p class="hint">Eiweiß ${Math.round(n.totals.protein)}${n.goals.protein ? ` / ${n.goals.protein}` : ""} g${n.burned_kcal ? ` · ${n.burned_kcal} kcal verbrannt` : ""}</p></div>
      ${icon("right", "chev")}
    </a>
    ${waterLine()}
  </div>`;
}

/** Wasser als Zeile in der Ernaehrungskarte statt eigener Karte – die volle Ansicht mit
 *  Glas gibt es unter Ernaehrung. */
function waterLine() {
  const n = data.nutrition;
  const pend = outbox.pending("water-log").filter((e) => !e.date || e.date === data.date)
    .reduce((s, e) => s + e.ml, 0);
  const ml = n.water_ml + pend + waterExtra;
  const liters = (v) => (v / 1000).toLocaleString("de-AT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return `<div class="water-line">
    <span class="row-icon">${icon("water")}</span>
    <span class="grow"><b>${liters(ml)}</b>${n.water_goal ? ` von ${liters(n.water_goal)}` : ""} L Wasser</span>
    <button type="button" class="icon-btn tonal-icon" data-water="-500" aria-label="Halben Liter abziehen">${icon("minus")}</button>
    <button type="button" class="icon-btn tonal-icon" data-water="500" aria-label="Halben Liter hinzufügen">${icon("plus")}</button>
  </div>`;
}

function render() {
  const d = data;
  const showForm = !d.checkin || editingCheckin;
  // Reihenfolge nach Wichtigkeit: das Training zuerst, der Check-in darunter – er passt
  // die Saetze an, ist aber nicht die Hauptsache des Tages.
  root.innerHTML = `<div class="streak-head">
      <span class="streak-count">${icon("flame")} <b>${d.streak.daily}</b> ${d.streak.daily === 1 ? "Tag" : "Tage"} in Folge</span>
    </div>
    ${weekStrip(d.streak.week)}
    ${d.profile_missing?.length ? `<a class="card mini-card" href="#profil">
      <span class="row-icon">${icon("user")}</span>
      <div class="grow"><p class="eyebrow">Profil vervollständigen</p>
        <p class="hint">Für Kalorien- und Trinkziel fehlen noch: ${esc(d.profile_missing.join(", "))}.</p></div>
      ${icon("right", "chev")}</a>` : ""}
    ${coachCard()}
    ${workoutCard()}
    ${(d.plan_events || []).length ? `<div class="card quiet-events"><p class="eyebrow">${icon("spark")} Dein Plan passt sich an</p>
      ${d.plan_events.map((e) => `<p class="hint">${esc(e.text)}</p>`).join("")}</div>` : ""}
    ${showForm ? checkinForm(d.checkin) : readinessCard(d.checkin)}
    ${mobilityCard()}
    ${nutritionCard()}`;
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

async function begin(dayId, { replace = false } = {}) {
  // Eine laufende Einheit nie still ersetzen – die Saetze darin waeren weg.
  await sheetClosed();   // begin() kommt oft aus einem gerade geschlossenen Sheet
  if (hasDraft() && !replace) {
    if (getDraft().split_day_id === dayId) { location.hash = "training/einheit"; return; }
    return confirmReplace(dayId);
  }
  const known = data?.day?.id === dayId ? data.day : dayCache.get(dayId);
  try {
    startDraft(known || await api(`day/${dayId}`));
    location.hash = "training/einheit";
  } catch (e) {
    toast(e.offline ? "Dieser Trainingstag wurde noch nicht geladen – online einmal öffnen." : e.message, { error: true });
  }
}

function confirmReplace(dayId) {
  const draft = getDraft();
  const done = draft.exercises.reduce((n, e) => n + e.sets.filter((s) => s.done).length, 0);
  const next = (data?.day?.id === dayId ? data.day : dayCache.get(dayId))?.label || "den anderen Tag";
  const sheet = openSheet({
    title: "Es läuft schon eine Einheit",
    body: `<p>${esc(draft.title || "Einheit")} ist offen${done ? ` – ${done} ${done === 1 ? "Satz" : "Sätze"} schon abgehakt` : ""}.</p>`,
    footer: `<a class="btn primary" href="#training/einheit">Weitermachen</a>
      <button type="button" class="btn danger ghost" data-replace>Verwerfen und ${esc(next)} starten</button>`,
  });
  sheet.querySelector("[data-replace]").addEventListener("click", () => {
    closeSheet();
    begin(dayId, { replace: true });
  });
}

/** Die heutige Einheit nur ansehen – Bearbeiten und Loeschen gibt es im Verlauf. */
async function showWorkout(id) {
  const sheet = openSheet({ title: "Heutige Einheit", body: skeleton(3) });
  try {
    const w = await api(`workouts/${id}`);
    if (!sheet.isConnected) return;
    const byExercise = {};
    for (const s of w.sets) (byExercise[s.exercise] ||= []).push(s);
    $(".sheet-body", sheet).innerHTML = `<div class="list">${Object.entries(byExercise).map(([name, sets]) => `
        <div class="row-item"><div class="row-main">
          <div class="row-title">${esc(name)}</div>
          <div class="row-meta"><span>${esc(setsSummary(sets, sets[0].unit))}</span></div>
        </div></div>`).join("")}</div>
      ${reviewHtml(w.review)}
      <p class="hint pad"><a href="#fortschritt/verlauf" data-close>Im Verlauf bearbeiten</a></p>`;
    if (w.review?.coach?.status === "pending") watchCoach(id, sheet);
  } catch (e) {
    closeSheet();
    toast(e.message, { error: true });
  }
}

async function saveCheckin(btn) {
  const form = root.querySelector("[data-checkin-form]");
  const value = (k) => Number(form.querySelector(`[data-scale="${k}"] :is(.on, .pre)`)?.dataset.value || 3);
  const soreness = {};
  form.querySelectorAll("[data-sore]").forEach((b) => {
    const lvl = Number(b.dataset.level);
    if (lvl) soreness[b.dataset.sore] = lvl;
  });
  // Datum mitschicken: offline gespeichert und erst morgen gesendet, gehoert der Check-in
  // trotzdem zu heute – sonst ueberschreibt er den von morgen.
  const body = { profile_id: getProfile(), date: entryDay(data?.date), sleep: value("sleep"), energy: value("energy"),
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
        body: { profile_id: getProfile(), routine: key, date: entryDay(data?.date), client_id: outbox.uuid() } });
      closeSheet();
      toast(res.queued ? "Erledigt – wird gesendet, sobald wieder Verbindung da ist" : "Mobility erledigt 💪");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { toast(e.message, { error: true }); }
  });
  paint();
}

export async function mount(el) {
  root = el;
  // Skalen wie echte Radiogruppen: ein Tab-Stopp pro Skala, Pfeiltasten waehlen.
  el.addEventListener("keydown", (ev) => {
    const dot = ev.target.closest?.(".dot-btn");
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
    if (!dot || !step) return;
    ev.preventDefault();
    const dots = [...dot.closest("[role=radiogroup]").querySelectorAll(".dot-btn")];
    const next = dots[(dots.indexOf(dot) + step + dots.length) % dots.length];
    next.click();
    next.focus();
  });
  el.addEventListener("click", (ev) => {
    const t = (s) => ev.target.closest(s);
    const dot = t(".dot-btn");
    if (dot) {
      const scale = dot.closest("[data-scale]");
      scale.querySelectorAll(".dot-btn").forEach((b) => {
        const on = b === dot;
        b.classList.remove("pre");
        b.classList.toggle("on", on);
        b.setAttribute("aria-checked", on);
        b.tabIndex = on ? 0 : -1;
      });
      $("[data-word]", scale).textContent = SCALES[scale.dataset.scale][1][Number(dot.dataset.value) - 1];
      dot.closest("[data-checkin-form]").classList.add("touched");
      return;
    }
    const sore = t("[data-sore]");
    if (sore) {
      const lvl = (Number(sore.dataset.level) + 1) % 4;
      sore.dataset.level = lvl;
      sore.className = `chip sore-${lvl}`;
      sore.querySelector("small").textContent = SORE[lvl];
      sore.setAttribute("aria-label", `Muskelkater ${GROUPS[sore.dataset.sore]}: ${soreWord(lvl)}`);
      sore.closest("[data-checkin-form]").classList.add("touched");
      return;
    }
    const save = t("[data-save-checkin]");
    if (save) return saveCheckin(save);
    if (t("[data-edit-checkin]")) { editingCheckin = true; return render(); }
    if (t("[data-cancel-checkin]")) { editingCheckin = false; return render(); }
    const note = t("[data-dismiss-note]");
    if (note) return dismissNote(note);
    const start = t("[data-start]");
    if (start) return begin(Number(start.dataset.start));
    if (t("[data-pick]")) return pickDay();
    const show = t("[data-show-workout]");
    if (show) return showWorkout(Number(show.dataset.showWorkout));
    const mob = t("[data-mobility]");
    if (mob) return openMobility(mob.dataset.mobility);
    const go = t("[data-goto]");
    if (go) location.hash = go.dataset.goto;
    const water = t("[data-water]");
    if (water) return addWater(Number(water.dataset.water));
  });
}

/** Nur die Wasserkarte neu zeichnen: ein render() wuerde einen halb angetippten
 *  Check-in darueber zuruecksetzen. */
function paintWater() {
  const line = root.querySelector(".water-line");
  if (line) line.outerHTML = waterLine();
  else render();
}

async function addWater(ml) {
  const body = { profile_id: getProfile(), ml, date: entryDay(data?.date), client_id: outbox.uuid() };
  waterExtra += ml;   // sofort sichtbar, nicht erst nach der Antwort vom Pi
  paintWater();
  try {
    const res = await outbox.send({ path: "water-log", body });
    waterExtra -= ml;
    // Erfolgreich gespeichert: gleich in die Bilanz einrechnen statt neu vom Server zu laden.
    // Im Ausgang gelandet (offline): pendingWater() (in waterCard) zaehlt den Eintrag ab jetzt mit.
    if (!res.queued) data.nutrition.water_ml = Math.max(0, data.nutrition.water_ml + ml);
    paintWater();
  } catch (e) {
    waterExtra -= ml;
    paintWater();
    toast(e.message, { error: true });
  }
}
