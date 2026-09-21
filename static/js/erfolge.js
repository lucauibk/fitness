/** Erfolge: Tages- und Wochenserie, Abzeichen und die nächsten Ziele. */
import { api, esc, icon, skeleton } from "./util.js";

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
      <div class="kpis compact">
        <div><span class="big-number">${icon("flame")} ${d.daily_streak}</span><span>Tage in Folge</span></div>
        <div><span class="big-number">${d.weekly_streak}</span><span>Wochen in Folge</span></div>
      </div>
      ${weekStrip(d.week)}
      <p class="hint">Aktiv zählt: Einheit, Cardio oder Mobility. Geplante Ruhetage unterbrechen die Serie nicht.
        Bestwerte: ${d.best_daily} Tage · ${d.best_weekly} Wochen.</p>
    </div>
    <div class="card">
      <p class="eyebrow">Insgesamt</p>
      <div class="kpis compact four">
        <div><span class="big-number">${d.totals.workouts}</span><span>Einheiten</span></div>
        <div><span class="big-number">${String(d.totals.volume_t).replace(".", ",")}</span><span>Tonnen bewegt</span></div>
        <div><span class="big-number">${d.totals.prs}</span><span>Bestleistungen</span></div>
        <div><span class="big-number">${String(d.totals.cardio_km).replace(".", ",")}</span><span>km Cardio</span></div>
      </div>
    </div>
    ${d.upcoming.length ? `<h3 class="group-title">Als Nächstes</h3>
      <div class="list">${d.upcoming.map((m) => `<div class="row-item">
        <span class="row-icon">${metricIcon(m)}</span>
        <div class="row-main"><div class="row-title">${esc(m.title)}</div>
          <div class="row-meta"><span>${esc(m.desc)}</span></div>
          <div class="progress sm"><i style="width:${Math.round(m.progress * 100)}%"></i></div></div>
        <span class="row-aside">${String(m.value).replace(".", ",")} / ${m.threshold}</span>
      </div>`).join("")}</div>` : ""}
    <h3 class="group-title">Abzeichen<span class="count-muted">${d.reached.length}</span></h3>
    ${d.reached.length ? `<div class="badges">${d.reached.map((m) => `<div class="badge m-${esc(m.metric || "")}">
        <span class="badge-icon">${metricIcon(m)}</span><b>${esc(m.title)}</b><small>${esc(m.desc)}</small></div>`).join("")}</div>`
      : `<p class="hint pad">Noch keine – die erste Einheit bringt das erste Abzeichen.</p>`}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(4);
  data = await api("achievements");
  render();
}

export async function mount(el) { root = el; }
