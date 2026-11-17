/** Fortschritt/Übersicht: Woche und Serie, fällige Steigerungen, Trend, Muskelgruppen,
 *  Bestleistungen und die Abzeichen – in dieser Reihenfolge, von „was ist jetzt zu tun“ zu „was ist
 *  schon erreicht“. Alles kommt in einer Runde (progress + achievements), nichts springt nach. */
import { api, empty, esc, fmtWeight, icon, openSheet, prefs, setsSummary, skeletonCards, toast } from "./util.js";
import { badgePreview, weekStrip, openBadges } from "./erfolge.js";

let root;
let data = null;
let ach = null;   // achievements; darf fehlen (offline), dann entfallen Woche und Abzeichen

const nf = (n) => Number(n).toLocaleString("de-AT");
const dm = (iso) => new Date(iso).toLocaleDateString("de-AT", { day: "numeric", month: "short" });
const dec = (v) => String(v).replace(".", ",");
const TREND_WEEKS = 12;

/** Schlichte Linie, selbst gezeichnet – im Gym ist kein fremder Server erreichbar.
 *  Der Endpunkt ist ein HTML-Punkt über der Linie (ein gestauchtes SVG machte ihn zur Ellipse).
 *  detail: Höchst-/Tiefstwert und Datum von/bis dazuschreiben (Punkte brauchen dafür `date`). */
export function sparkline(points, { height = 64, label = (p) => p.value, detail = false, format = dec } = {}) {
  if (points.length < 2) return "";
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 8;   // Prozent oben/unten, damit Linie und Punkt nicht am Rand kleben
  const xy = points.map((p, i) => [(i / (points.length - 1)) * 100, pad + (1 - (p.value - min) / span) * (100 - 2 * pad)]);
  const path = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const [lx, ly] = xy[xy.length - 1];
  const first = points[0];
  const last = points[points.length - 1];
  return `<div class="spark-wrap${detail ? " has-detail" : ""}" style="--h:${height}px">
    <svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none" role="img"
      aria-label="Verlauf von ${esc(label(first))} auf ${esc(label(last))}">
      <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"
        vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <i class="spark-dot" style="left:${lx.toFixed(1)}%;top:${ly.toFixed(1)}%"></i>
    ${detail ? `<span class="spark-max">${esc(format(max))}</span><span class="spark-min">${esc(format(min))}</span>` : ""}
  </div>${detail && first.date && last.date ? `<div class="spark-dates"><span>${dm(first.date)}</span><span>${dm(last.date)}</span></div>` : ""}`;
}

// ------------------------------------------------------- Abschnitte
/** Woche und Serie: die Serie hat genau eine Definition (achievements: Einheit, Cardio oder Mobility;
 *  geplante Ruhetage unterbrechen nicht) – dieselbe Zahl wie auf Heute. */
function weekSection() {
  if (!ach) return "";
  const planned = ach.week.filter((d) => d.planned || d.active).length;
  const active = ach.week.filter((d) => d.active).length;
  return `<div class="card">
    <div class="card-head">${icon("flame")}<span class="card-title">Diese Woche</span>
      <span class="aside">Serie: ${ach.weekly_streak} ${ach.weekly_streak === 1 ? "Woche" : "Wochen"}</span></div>
    ${weekStrip(ach.week)}
    <p class="hint">${active} von ${planned} Tagen aktiv · ${ach.daily_streak} ${ach.daily_streak === 1 ? "Tag" : "Tage"} in Folge</p>
  </div>`;
}

function readySection() {
  const ready = data.ready || [];
  if (!ready.length) return "";
  return `<div class="card">
    <div class="card-head">${icon("up")}<span class="card-title">Gewicht erhöhen</span></div>
    <div class="list flush">${ready.map((r) => `
      <div class="row-item">
        <span class="row-icon up">${icon("up")}</span>
        <div class="row-main">
          <div class="row-title">${esc(r.exercise)}</div>
          <div class="row-meta"><span>${esc(r.day)}</span>
            <span>${r.range[0]}–${r.range[1]} Whd. voll geschafft</span></div>
        </div>
        <span class="row-aside">${esc(fmtWeight(r.from, "kg"))} → <b>${esc(fmtWeight(r.to, "kg"))}</b></span>
      </div>`).join("")}</div>
    <p class="hint">Nur ein Vorschlag – beim Training kannst du jeden Wert überschreiben.</p>
  </div>`;
}

const TRENDS = {
  count: { label: "Einheiten", value: (f) => f.count, text: (v) => String(v) },
  sets: { label: "Sätze", value: (_f, v) => v.sets, text: (v) => String(v) },
  kg: { label: "Volumen", value: (_f, v) => v.kg, text: (v) => (v >= 1000 ? `${dec((v / 1000).toFixed(1))} t` : `${v}`) },
};
let trendKey = prefs.get("progressTrend", "count");

/** Zwölf Wochen als Balken; Einheiten mit gestrichelter Linie für die geplanten Tage pro Woche. */
function trendSection() {
  const f = data.frequency.weeks.slice(-TREND_WEEKS);
  const v = data.volume.slice(-TREND_WEEKS);
  const t = TRENDS[trendKey] || TRENDS.count;
  const vals = f.map((w, i) => t.value(w, v[i] || { sets: 0, kg: 0 }));
  const goal = trendKey === "count" && ach ? ach.week.filter((d) => d.planned || d.active).length : 0;
  const max = Math.max(1, goal, ...vals);
  return `<div class="card">
    <div class="card-head"><span class="card-title">Trend</span><span class="aside">${vals.length} Wochen</span></div>
    <div class="chips" role="radiogroup" aria-label="Kennzahl">${Object.entries(TRENDS).map(([k, x]) =>
      `<button type="button" role="radio" class="chip${k === trendKey ? " active" : ""}" aria-checked="${k === trendKey}" data-trend="${k}">${x.label}</button>`).join("")}</div>
    <div class="weekbars trend">${goal ? `<i class="goal-line" style="bottom:calc(${(goal / max) * 100}% * .72 + 1.5em)" aria-hidden="true"></i>` : ""}${vals.map((val, i) => {
      const now = i === vals.length - 1;
      const label = now ? "jetzt" : (vals.length - 1 - i) % 3 === 0 ? dm(f[i].week) : "";
      return `<span class="weekbar ${now ? "now" : ""}" role="img" aria-label="${now ? "Diese Woche" : `Woche ab ${dm(f[i].week)}`}: ${t.text(val)} ${t.label}">
        <b>${t.text(val)}</b><span class="wb-bar"><i style="height:${Math.max(4, (val / max) * 100)}%"></i></span><small>${label}</small></span>`;
    }).join("")}</div>
    ${goal ? `<p class="hint">Gestrichelt: ${goal} geplante Tage pro Woche.</p>` : ""}
  </div>`;
}

/** Sätze je Muskelgruppe diese Woche, mit dem Zielband 10–20 Sätze (Volumenlandmarken). Nicht versteckt:
 *  das ist die aufschlussreichste Auswertung. */
const BAND = [10, 20];
function muscleSection() {
  const vol = data.volume[data.volume.length - 1] || { groups: [] };
  if (!vol.groups.length) return "";
  const max = Math.max(BAND[1], ...vol.groups.map((g) => g.sets));
  return `<div class="card">
    <div class="card-head"><span class="card-title">Muskelgruppen</span><span class="aside">Sätze diese Woche</span></div>
    <div class="bars muscle-bars">${vol.groups.map((g) => `
      <div class="bar-row"><span class="bar-label">${esc(g.label)}</span>
        <span class="bar band"><i class="zone" style="left:${(BAND[0] / max) * 100}%;width:${((BAND[1] - BAND[0]) / max) * 100}%"></i>
          <i class="fill${g.sets >= BAND[0] ? " in-band" : ""}" style="width:${(g.sets / max) * 100}%"></i></span>
        <span class="bar-value">${g.sets}</span></div>`).join("")}</div>
    <p class="hint">Der helle Bereich (${BAND[0]}–${BAND[1]} Sätze) ist das übliche Wochenziel je Muskelgruppe.</p>
  </div>`;
}

function bestRow(b) {
  const trend = (b.trend || []).map((value) => ({ value }));
  const delta = trend.length > 1 ? trend[trend.length - 1].value - trend[0].value : 0;
  const unit = b.unit === "reps" ? "Whd." : "kg";
  const now = b.one_rm_now || b.one_rm;
  return `<div class="row-item" role="button" tabindex="0" data-history="${b.exercise_id}">
    <div class="row-main">
      <div class="row-title">${esc(b.exercise)}</div>
      <div class="row-meta"><span>${esc(setsSummary([b], b.unit))}</span><span>${dm(b.date)}</span></div>
    </div>
    ${trend.length > 1 ? `<span class="best-spark" aria-hidden="true">${sparkline(trend, { height: 28 })}</span>` : ""}
    ${now ? `<span class="row-aside"><b>${dec(Math.round(now * 10) / 10)}</b> ${unit}${
      Math.abs(delta) >= 0.1 ? `<small class="${delta > 0 ? "up" : "down"}">${delta > 0 ? "+" : "−"}${dec(Math.round(Math.abs(delta) * 10) / 10)}</small>` : ""}</span>` : ""}
    ${icon("right", "chev")}
  </div>`;
}

/** Die fuenf juengsten Bestleistungen – das ist, was sich gerade bewegt. Der Rest klappt auf. */
function bestSection() {
  const best = [...(data.best || [])].sort((a, b) => String(b.date).localeCompare(String(a.date)));
  if (!best.length) {
    return `<div class="card"><div class="card-head">${icon("trophy")}<span class="card-title">Bestleistungen</span></div>
      <p class="hint">Sobald du die erste Einheit gespeichert hast, stehen hier deine Bestwerte.</p></div>`;
  }
  const [top, rest] = [best.slice(0, 5), best.slice(5)];
  return `<div class="card">
    <div class="card-head">${icon("trophy")}<span class="card-title">Bestleistungen</span><span class="aside">zuletzt verbessert</span></div>
    <div class="list flush">${top.map(bestRow).join("")}</div>
    ${rest.length ? `<details class="field-more"><summary>Alle ${best.length} anzeigen</summary>
      <div class="list flush">${rest.map(bestRow).join("")}</div></details>` : ""}
    <p class="hint">Zahl = geschätztes Maximum für eine Wiederholung, Pfeil = Veränderung über die letzten Einheiten.</p>
  </div>`;
}

function render() {
  const nothing = !data.best.length && !data.bodyweight.length && !data.frequency.total;
  root.innerHTML = nothing
    ? empty("Noch keine Daten", "Nach der ersten gespeicherten Einheit siehst du hier deine Entwicklung.")
    : `${weekSection()}${readySection()}${data.frequency.total ? trendSection() + muscleSection() : ""}${bestSection()}${ach ? badgePreview(ach) : ""}`;
}

export async function load() {
  if (!data) root.innerHTML = skeletonCards([130, 200, 220]);
  [data, ach] = await Promise.all([api("progress"), api("achievements").catch(() => null)]);
  render();
}

// ---------------------------------------------------------- Details
async function showHistory(exerciseId) {
  const b = data.best.find((x) => x.exercise_id === exerciseId);
  try {
    const points = await api(`history/${exerciseId}`);
    const line = points.map((p) => ({ value: p.value, date: p.date }));
    openSheet({
      title: esc(b?.exercise || "Verlauf"),
      body: `${points.length > 1 ? sparkline(line, { height: 120, detail: true })
        : `<p class="hint">Für einen Verlauf braucht es mindestens zwei Einheiten.</p>`}
        <div class="list flush">${[...points].reverse().map((p) => `
          <div class="row-item"><div class="row-main">
            <div class="row-title">${esc(dm(p.date))}</div>
            <div class="row-meta"><span>${p.reps} Whd.${p.weight != null
              ? ` @ ${esc(fmtWeight(p.weight, b?.unit))}` : ""}</span></div>
          </div><span class="row-aside">${p.weight != null
            ? `≈ ${dec(p.value)} kg` : `${p.value} Whd.`}</span></div>`).join("")}</div>`,
    });
  } catch (e) { toast(e.message, { error: true }); }
}

export async function mount(el) {
  root = el;
  data = null; ach = null;   // frisch (z. B. nach Profilwechsel)
  el.addEventListener("click", (ev) => {
    const h = ev.target.closest("[data-history]");
    if (h) return showHistory(Number(h.dataset.history));
    const tr = ev.target.closest("[data-trend]");
    if (tr) { trendKey = tr.dataset.trend; prefs.set("progressTrend", trendKey); return render(); }
    if (ev.target.closest("[data-badges]")) return openBadges();
  });
}
