/** Heute: eine Hauptaktion (Training, mit dem Check-in als Zeile darin), darunter drei Kacheln
 *  (Kalorien, Wasser, Fasten), der Coach in einer Zeile und alles Weitere hinter "Mehr". */
import { $, api, closeSheet, empty, entryDay, esc, reviewHtml, watchCoach, fmtWeight, getProfile, GROUPS, icon, openSheet,
  heroNotesHtml, patch, prefs, setsSummary, sheetClosed, skeleton, skeletonCards, toast, WEEKDAYS_LONG, busy } from "./util.js";
import { startDraft, hasDraft, getDraft } from "./training.js";
import { weekStrip } from "./erfolge.js";
import * as outbox from "./outbox.js";
import { openTopf, topfCard } from "./topf.js";
import { openCoach, prose } from "./coach.js";
import { nextTraining } from "./naechster.js";

let root;
let data = null;
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist
const dayCache = new Map();

const SCALES = {
  sleep: ["Schlaf", ["sehr schlecht", "schlecht", "okay", "gut", "sehr gut"]],
  energy: ["Energie", ["leer", "müde", "normal", "fit", "voller Energie"]],
  stress: ["Stress", ["entspannt", "gering", "mittel", "hoch", "sehr hoch"]],
};
const num = (v) => Number(v || 0).toLocaleString("de-AT", { maximumFractionDigits: 0 });
const liters = (ml) => (ml / 1000).toLocaleString("de-AT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const SORE = ["–", "leicht", "mittel", "stark"];
const soreWord = (lvl) => (lvl ? SORE[lvl] : "keiner");

// ------------------------------------------------------------ Check-in
/** Skalen und Muskelkater – lebt im Sheet (openCheckin), nicht mehr als grosse Karte auf der Seite. */
function checkinForm(c) {
  const v = { sleep: c?.sleep ?? 3, energy: c?.energy ?? 3, stress: c?.stress ?? 3 };
  // Noch nicht eingecheckt: die 3 ist nur ein Vorschlag – umrandet statt gefuellt, damit sie
  // nicht wie eine schon gespeicherte Antwort aussieht. Speichern geht trotzdem sofort.
  const mark = c ? "on" : "pre";
  const sore = c?.soreness || {};
  const anySore = Object.values(sore).some(Boolean);
  return `<div class="checkin" data-checkin-form>
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
  </div>`;
}

/** Eine Zeile im Hero: noch offen = Einladung, erledigt = Ergebnis. Beides oeffnet das Sheet. */
function checkinRow() {
  const c = data.checkin;
  if (!c) {
    return `<button type="button" class="checkin-row" data-open-checkin>${icon("check")}
      <span><b>Wie fit bist du heute?</b><small>Passt die Sätze an</small></span>${icon("right", "chev")}</button>`;
  }
  const tone = { voll: "ok", leichter: "soon", erholung: "urgent" }[c.level.key];
  return `<button type="button" class="checkin-row is-done" data-open-checkin>
    <span class="pill ${tone}">${c.readiness}</span>
    <span><b>${esc(c.level.title)}</b><small>${esc(c.level.text)}</small></span>${icon("edit", "chev")}</button>`;
}

function openCheckin() {
  const c = data.checkin;
  const sheet = openSheet({
    title: c ? "Check-in ändern" : "Wie geht's dir heute?",
    body: `${hasDraft() ? `<p class="callout">${icon("warn")} Es läuft schon eine Einheit – der Check-in gilt ab dem nächsten Training.</p>` : ""}${checkinForm(c)}`,
    footer: `<button type="button" class="btn primary" data-save-checkin>${icon("check")} Speichern</button>`,
  });
  sheet.addEventListener("keydown", (ev) => {
    // Skalen wie echte Radiogruppen: ein Tab-Stopp pro Skala, Pfeiltasten waehlen.
    const dot = ev.target.closest?.(".dot-btn");
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
    if (!dot || !step) return;
    ev.preventDefault();
    const dots = [...dot.closest("[role=radiogroup]").querySelectorAll(".dot-btn")];
    const next = dots[(dots.indexOf(dot) + step + dots.length) % dots.length];
    next.click();
    next.focus();
  });
  sheet.addEventListener("click", (ev) => {
    const dot = ev.target.closest(".dot-btn");
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
      return;
    }
    const sore = ev.target.closest("[data-sore]");
    if (sore) {
      const lvl = (Number(sore.dataset.level) + 1) % 4;
      sore.dataset.level = lvl;
      sore.className = `chip sore-${lvl}`;
      sore.querySelector("small").textContent = SORE[lvl];
      sore.setAttribute("aria-label", `Muskelkater ${GROUPS[sore.dataset.sore]}: ${soreWord(lvl)}`);
      return;
    }
    const save = ev.target.closest("[data-save-checkin]");
    if (save) saveCheckin(save, sheet);
  });
}

// ---------------------------------------------------------- Coach-Hinweise
/** Coach: Impuls des Tages (Morgen-Coach), sonst Begruessung oder Tipp – und der Weg in den Chat. */
function coachCard() {
  const c = data.coach || {};
  const imp = c.impulse || {};
  const label = { impuls: "Dein Coach für heute", hallo: "Dein Coach", tipp: "Tipp des Tages",
    erledigt: "Heute erledigt" }[imp.kind] || "Coach";
  return `<div class="card quiet-card mini-card coach-card" role="button" tabindex="0" data-coach>
    <span class="row-icon">${icon("chat")}</span>
    <div class="grow"><p class="eyebrow">${label}</p>
      ${imp.text ? `<div class="coach-last">${prose(imp.text)}</div>` : ""}
      ${c.unread ? `<p class="coach-unread"><span class="pill ok">${c.unread === 1 ? "1 neue Nachricht" : `${c.unread} neue Nachrichten`}</span></p>` : ""}
    </div>
    <span class="btn small tonal">${c.unread ? "Lesen" : "Fragen"}</span>
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
    return `<div class="card hero hero-main is-done">
      <p class="eyebrow">${icon("check")} Heute erledigt</p>
      <h3 class="hero-title">${esc(tw.day_label)}</h3>
      <p class="hint">${esc(tw.headline)}</p>
      <div class="btn-row hero-cta">
        <button type="button" class="btn tonal" data-show-workout="${tw.id}">Ansehen</button>
        <button type="button" class="btn ghost" data-pick>Nochmal trainieren</button>
      </div>
    </div>`;
  }
  const nt = nextTraining(d);
  return `<div class="card hero hero-main">
      <p class="eyebrow">${esc(nt.eyebrow)}</p>
      <h3 class="hero-title">${esc(nt.label)}</h3>
      <p class="meta-line">${nt.meta.map((m) => `<span>${esc(m)}</span>`).join("")}</p>
      ${heroNotesHtml(d)}
      ${running ? "" : checkinRow()}
      <div class="btn-row hero-cta">
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
  return `<div class="card quiet-card mini-card" role="button" tabindex="0" data-mobility="${m.key}">
    <span class="row-icon">${icon("stretch")}</span>
    <div class="grow"><p class="eyebrow">Mobility · ${m.minutes} min</p>
      <div class="card-title">${esc(m.title)}</div><p class="hint">${esc(m.text)}</p></div>
    ${m.done ? `<span class="pill ok">erledigt</span>` : icon("right", "chev")}
  </div>`;
}

// ------------------------------------------------------------ Tageskacheln
function caloriesTile() {
  const n = data.nutrition;
  const pend = outbox.pending("food-log").filter((e) => !e.date || e.date === data.date)
    .reduce((s, e) => s + (e.preview?.kcal || 0), 0);
  const eaten = n.totals.kcal + pend;
  const budget = n.budget_kcal || 0;   // Ziel + verbrannt, vom Server: dieselbe Zahl wie in Ernährung
  const pct = budget ? Math.min(100, Math.round((eaten / budget) * 100)) : 0;
  return `<a class="tile-card" href="#ernaehrung">
    <span class="tile-label">${icon("food")} Kalorien</span>
    <b>${budget ? num(Math.abs(budget - eaten)) : num(eaten)}</b>
    <small>${budget ? (eaten > budget ? "kcal zu viel" : "kcal übrig") : "kcal gegessen"}</small>
    ${budget ? `<progress class="nutri-bar${eaten > budget ? " is-over" : ""}" max="100" value="${pct}" aria-label="${pct} % des Tagesbudgets"></progress>` : ""}
    <small>Eiweiß ${num(n.totals.protein)}${n.goals.protein ? ` / ${num(n.goals.protein)}` : ""} g</small>
  </a>`;
}

/** Wasser: eine Taste (+250 ml), Korrekturen gibt es unter Ernährung. Kein Minus neben dem Plus. */
function waterTile() {
  const n = data.nutrition;
  const pend = outbox.pending("water-log").filter((e) => !e.date || e.date === data.date)
    .reduce((s, e) => s + e.ml, 0);
  const ml = n.water_ml + pend + waterExtra;
  const pct = n.water_goal ? Math.min(100, Math.round((ml / n.water_goal) * 100)) : 0;
  return `<div class="tile-card tile-water">
    <span class="tile-label">${icon("water")} Wasser</span>
    <b>${liters(ml)}</b><small>${n.water_goal ? `von ${liters(n.water_goal)} L` : "Liter"}</small>
    ${n.water_goal ? `<progress class="nutri-bar is-water" max="100" value="${pct}" aria-label="${pct} % des Trinkziels"></progress>` : ""}
    <button type="button" class="btn small tonal" data-water="250">+ 250 ml</button>
  </div>`;
}

/** Nur wenn Fasten an ist oder laeuft. Tippen fuehrt in die Fasten-Ansicht der Ernährung. */
function fastingTile() {
  const f = data.fasting;
  if (!f) return "";
  const run = f.running;
  const hours = run ? (Date.now() - new Date(run.started_at)) / 3600000 : 0;
  const hm = (h) => { const m = Math.max(0, Math.round(h * 60)); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
  const pct = run && f.target_hours ? Math.min(100, Math.round((hours / f.target_hours) * 100)) : 0;
  return `<a class="tile-card" href="#ernaehrung" data-fast-tile>
    <span class="tile-label">${icon("clock")} Fasten</span>
    <b>${run ? hm(hours) : esc(f.kind || "–")}</b>
    <small>${run ? `von ${hm(f.target_hours)} h` : "nicht aktiv"}</small>
    ${run ? `<progress class="nutri-bar" max="100" value="${pct}" aria-label="${pct} % des Fastenziels"></progress>` : ""}
  </a>`;
}

function render() {
  const d = data;
  const more = [
    d.profile_missing?.length ? `<a class="card quiet-card mini-card" href="#profil">
      <span class="row-icon">${icon("user")}</span>
      <div class="grow"><p class="eyebrow">Profil vervollständigen</p>
        <p class="hint">Für Kalorien- und Trinkziel fehlen noch: ${esc(d.profile_missing.join(", "))}.</p></div>
      ${icon("right", "chev")}</a>` : "",
    mobilityCard(),
    topfCard(d.rewards),
    (d.plan_events || []).length ? `<div class="card quiet-card quiet-events"><p class="eyebrow">${icon("spark")} Dein Plan passt sich an</p>
      ${d.plan_events.map((e) => `<p class="hint">${esc(e.text)}</p>`).join("")}</div>` : "",
  ].filter(Boolean).join("");
  // Reihenfolge: Status → eine Hauptaktion → Tageskacheln → Coach → Mehr
  patch(root, `<div class="week-card" role="button" tabindex="0" data-goto="fortschritt">
      <span class="streak-count">${icon("flame")} <b>${d.streak.daily}</b> ${d.streak.daily === 1 ? "Tag" : "Tage"}<span class="sr-only"> in Folge</span></span>
      ${weekStrip(d.streak.week)}
    </div>
    ${workoutCard()}
    <div class="today-tiles">${caloriesTile()}${waterTile()}${fastingTile()}</div>
    ${coachCard()}
    <details class="group-details more-details" ${prefs.get("heuteMore") ? "open" : ""} data-more>
      <summary class="group-title">Mehr<span class="count-muted">Mobility, Topf, Hinweise</span>${icon("down", "chev")}</summary>
      ${more}
    </details>`);
}

export async function load() {
  if (!data) root.innerHTML = skeletonCards([84, 280, 130, 84]);   // Geometrie der echten Seite
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

async function saveCheckin(btn, sheet) {
  const form = sheet.querySelector("[data-checkin-form]");
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
      closeSheet();
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
  data = null; waterExtra = 0; dayCache.clear();   // frisch (z. B. nach Profilwechsel)
  el.addEventListener("click", (ev) => {
    const t = (s) => ev.target.closest(s);
    if (t("[data-open-checkin]")) return openCheckin();
    if (t("[data-fast-tile]")) prefs.set("nutriView", "fasten");   // Link zu Ernährung, dort gleich Fasten
    if (t("[data-coach]")) return openCoach();
    if (t("[data-more]") && t("summary")) prefs.set("heuteMore", !t("[data-more]").open);   // vor dem Umschalten: neuer Zustand
    const start = t("[data-start]");
    if (start) return begin(Number(start.dataset.start));
    if (t("[data-pick]")) return pickDay();
    const show = t("[data-show-workout]");
    if (show) return showWorkout(Number(show.dataset.showWorkout));
    if (t("[data-topf]")) return openTopf();
    const mob = t("[data-mobility]");
    if (mob) return openMobility(mob.dataset.mobility);
    const go = t("[data-goto]");
    if (go) location.hash = go.dataset.goto;
    const water = t("[data-water]");
    if (water) return addWater(Number(water.dataset.water));
  });
}

/** Nur die Wasserkachel neu zeichnen – ein Antippen soll nicht die ganze Seite neu aufbauen. */
function paintWater() {
  const tile = root.querySelector(".tile-water");
  if (tile) tile.outerHTML = waterTile();
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

// Chat gelesen oder neue Antwort: "neu"-Punkt und letzte Nachricht auffrischen
window.addEventListener("coach-seen", () => {
  if (data && root?.isConnected) load().catch(() => {});
});
