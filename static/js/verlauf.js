/** Verlauf: Krafteinheiten und Cardio gemischt nach Datum. */
import { $, api, armDelete, closeSheet, empty, esc, icon, openSheet, setsSummary, skeleton,
  toast } from "./util.js";
import * as outbox from "./outbox.js";

let root;
let workouts = null;
let cardio = null;

const dateLabel = (iso) => new Date(iso).toLocaleDateString("de-AT",
  { weekday: "short", day: "numeric", month: "short" });

function workoutRow(w) {
  const sets = w.sets || [];
  const kg = sets.reduce((n, s) => n + (s.weight || 0) * (s.reps || 0), 0);
  const when = w.started_at || w.finished_at;
  return `<div class="row-item ${w.pending ? "is-pending" : ""}" ${w.pending ? "" : `role="button" tabindex="0" data-workout="${w.id}"`}>
    <span class="row-icon">${icon("dumbbell")}</span>
    <div class="row-main">
      <div class="row-title">${esc(w.day_label || w.title || "Training")}</div>
      <div class="row-meta">
        <span>${when ? dateLabel(when) : ""}</span>
        <span>${sets.length} Sätze</span>
        ${kg ? `<span>${Math.round(kg).toLocaleString("de-AT")} kg bewegt</span>` : ""}
      </div>
    </div>
    ${w.pending ? `<span class="pill">wartet</span>` : icon("right", "chev")}
  </div>`;
}

function cardioRow(c) {
  return `<div class="row-item ${c.pending ? "is-pending" : ""}" ${c.pending ? "" : `role="button" tabindex="0" data-cardio="${c.id}"`}>
    <span class="row-icon">${icon("run")}</span>
    <div class="row-main">
      <div class="row-title">${esc(c.label || c.activity)}</div>
      <div class="row-meta"><span>${dateLabel(c.date)}</span><span>${Math.round(c.minutes)} min</span>
        ${c.distance_km ? `<span>${String(c.distance_km).replace(".", ",")} km</span>` : ""}
        ${c.kcal ? `<span>≈ ${Math.round(c.kcal)} kcal</span>` : ""}</div>
    </div>
    ${c.pending ? `<span class="pill">wartet</span>` : ""}
  </div>`;
}

function render() {
  const items = [
    ...outbox.pendingWorkouts().map((w) => ({ at: w.started_at || w.finished_at, html: workoutRow(w) })),
    ...outbox.pending("cardio").map((c) => ({ at: c.date || new Date().toISOString(), html: cardioRow(c) })),
    ...(workouts || []).map((w) => ({ at: w.started_at || w.finished_at, html: workoutRow(w) })),
    ...(cardio || []).map((c) => ({ at: c.date, html: cardioRow(c) })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at)));
  root.innerHTML = items.length
    ? `<div class="list">${items.map((i) => i.html).join("")}</div>`
    : empty("Noch nichts trainiert", "Gespeicherte Einheiten und Cardio erscheinen hier.");
}

export async function load() {
  if (!workouts) root.innerHTML = skeleton(4);
  [workouts, cardio] = await Promise.all([api("workouts?limit=60"), api("cardio?limit=60")]);
  render();
}

function openWorkout(id) {
  const w = (workouts || []).find((x) => x.id === id);
  if (!w) return;
  const byExercise = {};
  for (const s of w.sets) (byExercise[s.exercise] ||= []).push(s);
  const sheet = openSheet({
    title: w.day_label || w.title || "Einheit",
    body: `<p class="hint">${new Date(w.started_at || w.finished_at).toLocaleString("de-AT",
      { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}</p>
      <div class="list">${Object.entries(byExercise).map(([name, sets]) => `
        <div class="row-item"><div class="row-main">
          <div class="row-title">${esc(name)}</div>
          <div class="row-meta"><span>${esc(setsSummary(sets, sets[0].unit))}</span></div>
        </div></div>`).join("")}</div>
      ${w.notes ? `<p class="hint pad">${esc(w.notes)}</p>` : ""}`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Schließen</button>`,
  });
  armDelete($("[data-delete]", sheet), () => remove(`workouts/${id}`, "Einheit gelöscht"),
    "Löschen", "Einheit wirklich löschen?");
}

function openCardio(id) {
  const c = (cardio || []).find((x) => x.id === id);
  if (!c) return;
  const sheet = openSheet({
    title: c.label,
    body: `<p class="hint">${dateLabel(c.date)}</p>
      <div class="kpis compact">
        <div><span class="big-number">${Math.round(c.minutes)}</span><span>Minuten</span></div>
        ${c.distance_km ? `<div><span class="big-number">${String(c.distance_km).replace(".", ",")}</span><span>km</span></div>` : ""}
        ${c.kcal ? `<div><span class="big-number">${Math.round(c.kcal)}</span><span>kcal (geschätzt)</span></div>` : ""}
        ${c.avg_hr ? `<div><span class="big-number">${c.avg_hr}</span><span>Puls ⌀</span></div>` : ""}
      </div>${c.notes ? `<p class="hint pad">${esc(c.notes)}</p>` : ""}`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span><button type="button" class="btn ghost" data-close>Schließen</button>`,
  });
  armDelete($("[data-delete]", sheet), () => remove(`cardio/${id}`, "Cardio-Einheit gelöscht"),
    "Löschen", "Wirklich löschen?");
}

async function remove(path, message) {
  closeSheet();
  try {
    await api(path, { method: "DELETE" });
    toast(message);
    window.dispatchEvent(new CustomEvent("data-changed"));
  } catch (e) { toast(e.message, { error: true }); }
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const w = ev.target.closest("[data-workout]");
    if (w) return openWorkout(Number(w.dataset.workout));
    const c = ev.target.closest("[data-cardio]");
    if (c) return openCardio(Number(c.dataset.cardio));
  });
}
