/** Heute: welcher Split-Teil ist dran, was stand letztes Mal, was wäre der Vorschlag. */
import { api, closeSheet, empty, esc, fmtWeight, getProfile, icon, openSheet, relDate,
  setsSummary, skeleton, toast, WEEKDAYS_LONG } from "./util.js";
import { startDraft, hasDraft } from "./training.js";

let root;
let data = null;
const dayCache = new Map();

function exerciseRow(e) {
  const s = e.suggest;
  const target = e.target_reps_min === e.target_reps_max
    ? `${e.target_reps_min}` : `${e.target_reps_min}–${e.target_reps_max}`;
  return `<div class="row-item">
    <div class="row-main">
      <div class="row-title">${esc(e.name)}</div>
      <div class="row-meta">
        <span>${e.target_sets} Sätze · ${target} Whd.</span>
        ${e.last.length ? `<span>letztes Mal ${esc(setsSummary(e.last, e.unit))}</span>`
          : `<span class="faint">noch nie gemacht</span>`}
      </div>
    </div>
    <span class="suggest ${s.raise_weight ? "up" : ""}" title="${esc(s.reason)}">
      ${s.raise_weight ? icon("up") : ""}${esc(s.weight != null ? fmtWeight(s.weight, e.unit) : `${s.reps} Whd.`)}
    </span>
  </div>`;
}

function render() {
  if (!data?.split) {
    root.innerHTML = `${empty("Noch kein Split angelegt",
        "Lege unter <b>Plan</b> einen Split mit deinen Trainingstagen an.",
        { label: "Zum Plan", attrs: 'onclick="location.hash=\'plan\'"' })}`;
    return;
  }
  const day = data.day;
  const running = hasDraft();
  const weekday = WEEKDAYS_LONG[(new Date().getDay() + 6) % 7];
  const pos = data.position ? `Tag ${data.position} von ${data.day_count}` : "";
  const last = data.last_workout;

  root.innerHTML = `<p class="page-sub top">${esc(data.split.name)}</p>
    ${day ? `<div class="card hero">
      <p class="eyebrow">${data.fixed ? `${esc(weekday)} · fester Tag` : "als Nächstes dran"}</p>
      <h3 class="hero-title">${esc(day.label)}</h3>
      <p class="hint">${esc(pos)}${day.exercises.length ? ` · ${day.exercises.length} Übungen` : ""}</p>
      <div class="btn-row">
        ${running
          ? `<a class="btn primary" href="#training">${icon("clock")} Einheit fortsetzen</a>`
          : `<button type="button" class="btn primary" data-start="${day.id}">${icon("plus")} Einheit starten</button>`}
        <button type="button" class="btn ghost" data-pick>Anderer Tag</button>
      </div>
    </div>
    <h3 class="group-title">Übungen<span class="count-muted">Vorschlag fürs nächste Mal</span></h3>
    <div class="list">${day.exercises.map(exerciseRow).join("")
      || `<p class="hint pad">Für diesen Tag sind noch keine Übungen eingetragen.</p>`}</div>`
    : `${empty("Für heute ist nichts geplant", "Wähle einen Trainingstag, wenn du trotzdem willst.",
        { label: "Trainingstag wählen", attrs: "data-pick" })}`}
    ${last ? `<p class="hint pad">Letzte Einheit: ${esc(last.day_label || "Training")} · ${esc(relDate(last.at, { time: false }))}</p>` : ""}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(4);
  data = await api("today");
  render();
  prefetchDays();
}

/** Die Pläne der übrigen Trainingstage still mitladen. Ohne das könnte man im Gym
 *  ohne Empfang keinen anderen Tag starten – der Plan wird ja einzeln geholt. */
function prefetchDays() {
  const others = (data.days || []).filter((d) => d.id !== data.day?.id);
  // Bewusst roher fetch: ein stiller Vorab-Ladevorgang soll den Offline-Hinweis
  // nicht umschreiben, wenn er gerade ins Leere läuft.
  others.forEach((d, i) => setTimeout(() => {
    fetch(`/api/day/${d.id}?profile=${getProfile()}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((plan) => { if (plan) dayCache.set(d.id, plan); })
      .catch(() => {});
  }, 400 * (i + 1)));
}

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
  // Der heutige Tag steckt schon in /api/today, die anderen im Zwischenspeicher –
  // damit klappt der Start auch ohne Verbindung.
  const known = data?.day?.id === dayId ? data.day : dayCache.get(dayId);
  if (known) {
    startDraft(known);
    location.hash = "training";
    return;
  }
  try {
    const day = await api(`day/${dayId}`);
    dayCache.set(dayId, day);
    startDraft(day);
    location.hash = "training";
  } catch (e) {
    toast(e.offline ? "Dieser Trainingstag wurde noch nicht geladen – online einmal öffnen."
      : e.message, { error: true });
  }
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const start = ev.target.closest("[data-start]");
    if (start) return begin(Number(start.dataset.start));
    if (ev.target.closest("[data-pick]")) pickDay();
  });
}
