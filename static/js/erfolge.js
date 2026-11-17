/** Erfolge: Serie, Abzeichen und die nächsten Ziele. Kein eigener Bereich mehr – die Übersicht
 *  zeigt eine Vorschau (badgePreview), „Alle“ öffnet das Sheet (openBadges). */
import { api, empty, esc, icon, openSheet, toast } from "./util.js";

const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
// Ein Symbol je Kennzahl – zehn gleiche Pokale lesen sich wie ein Muster, nicht wie Erfolge.
const METRIC_ICON = { workouts: "dumbbell", best_daily: "flame", best_weekly: "flame", volume_t: "weight",
  prs: "trophy", cardio_km: "run", checkins: "user", bodyfat: "progress" };
const metricIcon = (m) => icon(METRIC_ICON[m.metric] || "trophy");
const dec = (v) => String(v).replace(".", ",");

export function weekStrip(week) {
  return `<div class="week-strip" role="list" aria-label="Diese Woche">${week.map((d, i) => `
    <span role="listitem" class="wday ${d.active ? "active" : ""} ${d.today ? "today" : ""} ${d.planned ? "planned" : ""} ${d.future ? "future" : ""}"
      aria-label="${WD[i]}: ${d.active ? "aktiv" : d.planned ? "Training geplant" : "Ruhetag"}">
      <b>${WD[i]}</b><i>${d.active ? icon("check") : ""}</i></span>`).join("")}</div>`;
}

const upcomingRow = (m) => `<div class="row-item is-locked">
  <span class="row-icon">${metricIcon(m)}</span>
  <div class="row-main"><div class="row-title">${esc(m.title)}</div>
    <div class="row-meta"><span>${esc(m.desc)}</span></div>
    <div class="progress sm"><i style="width:${Math.round(m.progress * 100)}%"></i></div></div>
  <span class="row-aside">${dec(m.value)} / ${m.threshold}</span>
</div>`;

const badge = (m) => `<div class="badge m-${esc(m.metric || "")}">
  <span class="badge-icon">${metricIcon(m)}<span class="badge-check">${icon("check")}</span></span><b>${esc(m.title)}</b><small>${esc(m.desc)}</small></div>`;

/** Karte für die Übersicht: die drei neuesten Abzeichen, das nächste Ziel, ein Knopf für alles. */
export function badgePreview(d) {
  const latest = d.reached.slice(0, 3);
  const next = d.upcoming[0];
  return `<div class="card">
    <div class="card-head">${icon("trophy")}<span class="card-title">Abzeichen</span>
      <span class="aside">${d.reached.length} freigeschaltet</span></div>
    ${latest.length ? `<div class="badges preview">${latest.map(badge).join("")}</div>`
      : `<p class="hint">Die erste Einheit bringt das erste Abzeichen.</p>`}
    ${next ? `<div class="list flush">${upcomingRow(next)}</div>` : ""}
    <div class="btn-row"><button type="button" class="btn ghost" data-badges>Alle ansehen</button></div>
  </div>`;
}

/** Alle Abzeichen, Gesamtzahlen und kommende Ziele in einem Sheet. */
export async function openBadges() {
  let d;
  try { d = await api("achievements"); } catch (e) { return toast(e.offline ? "Dafür braucht es eine Verbindung." : e.message, { error: true }); }
  openSheet({ title: "Erfolge", wide: true, body: `
    <div class="stats four">
      <div class="stat lg"><b>${d.totals.workouts}</b><span>Einheiten</span></div>
      <div class="stat lg"><b>${dec(d.totals.volume_t)}</b><span>Tonnen bewegt</span></div>
      <div class="stat lg"><b>${d.totals.prs}</b><span>Bestleistungen</span></div>
      <div class="stat lg"><b>${dec(d.totals.cardio_km)}</b><span>km Cardio</span></div>
    </div>
    <p class="hint">Bestwerte der Serie: ${d.best_daily} Tage · ${d.best_weekly} Wochen. Aktiv zählt: Einheit, Cardio oder Mobility;
      geplante Ruhetage unterbrechen die Serie nicht.</p>
    ${d.upcoming.length ? `<h3 class="section-title">Als Nächstes</h3><div class="list">${d.upcoming.map(upcomingRow).join("")}</div>` : ""}
    <h3 class="section-title">Abzeichen · ${d.reached.length} freigeschaltet</h3>
    ${d.reached.length ? `<div class="badges">${d.reached.map(badge).join("")}</div>`
      : empty("Noch keine Abzeichen", "Die erste Einheit bringt das erste Abzeichen.")}` });
}
