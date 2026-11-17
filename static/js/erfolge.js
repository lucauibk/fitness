/** Erfolge: Tages- und Wochenserie, Abzeichen und die nächsten Ziele. */
import { api, empty, esc, icon, skeleton } from "./util.js";

let root;
let data = null;
const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
// Ein Symbol je Kennzahl – zehn gleiche Pokale lesen sich wie ein Muster, nicht wie Erfolge.
const METRIC_ICON = { workouts: "dumbbell", best_daily: "flame", best_weekly: "flame", volume_t: "weight",
  prs: "trophy", cardio_km: "run", checkins: "user", bodyfat: "progress" };
const metricIcon = (m) => icon(METRIC_ICON[m.metric] || "trophy");

export function weekStrip(week) {
  return `<div class="week-strip" role="list" aria-label="Diese Woche">${week.map((d, i) => `
    <span role="listitem" class="wday ${d.active ? "active" : ""} ${d.today ? "today" : ""} ${d.planned ? "planned" : ""} ${d.future ? "future" : ""}"
      aria-label="${WD[i]}: ${d.active ? "aktiv" : d.planned ? "Training geplant" : "Ruhetag"}">
      <b>${WD[i]}</b><i>${d.active ? icon("check") : ""}</i></span>`).join("")}</div>`;
}

function render() {
  const d = data;
  root.innerHTML = `<div class="card">
      <div class="card-head">${icon("flame")}<span class="card-title">Serie</span></div>
      <div class="stats">
        <div class="stat lg"><b>${d.daily_streak}</b><span>Tage in Folge</span></div>
        <div class="stat lg"><b>${d.weekly_streak}</b><span>Wochen in Folge</span></div>
      </div>
      ${weekStrip(d.week)}
      <p class="hint">Aktiv zählt: Einheit, Cardio oder Mobility. Geplante Ruhetage unterbrechen die Serie nicht.
        Bestwerte: ${d.best_daily} Tage · ${d.best_weekly} Wochen.</p>
    </div>
    <div class="card">
      <div class="card-head">${icon("trophy")}<span class="card-title">Insgesamt</span></div>
      <div class="stats four">
        <div class="stat lg"><b>${d.totals.workouts}</b><span>Einheiten</span></div>
        <div class="stat lg"><b>${String(d.totals.volume_t).replace(".", ",")}</b><span>Tonnen bewegt</span></div>
        <div class="stat lg"><b>${d.totals.prs}</b><span>Bestleistungen</span></div>
        <div class="stat lg"><b>${String(d.totals.cardio_km).replace(".", ",")}</b><span>km Cardio</span></div>
      </div>
    </div>
    ${d.upcoming.length ? `<h3 class="section-title">Als Nächstes</h3>
      <div class="list">${d.upcoming.map((m) => `<div class="row-item is-locked">
        <span class="row-icon">${metricIcon(m)}</span>
        <div class="row-main"><div class="row-title">${esc(m.title)}</div>
          <div class="row-meta"><span>${esc(m.desc)}</span></div>
          <div class="progress sm"><i style="width:${Math.round(m.progress * 100)}%"></i></div></div>
        <span class="row-aside">${String(m.value).replace(".", ",")} / ${m.threshold}</span>
      </div>`).join("")}</div>` : ""}
    <h3 class="section-title">Abzeichen · ${d.reached.length} freigeschaltet</h3>
    ${d.reached.length ? `<div class="badges">${d.reached.map((m) => `<div class="badge m-${esc(m.metric || "")}">
        <span class="badge-icon">${metricIcon(m)}<span class="badge-check">${icon("check")}</span></span><b>${esc(m.title)}</b><small>${esc(m.desc)}</small></div>`).join("")}</div>`
      : empty("Noch keine Abzeichen", "Die erste Einheit bringt das erste Abzeichen.")}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(4);
  data = await api("achievements");
  render();
}

export async function mount(el) { root = el; }
