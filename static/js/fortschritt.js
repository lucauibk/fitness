/** Fortschritt: fällige Steigerungen, Volumen und Häufigkeit, Bestleistungen, Körpergewicht. */
import { ring } from "./ernaehrung.js";
import { api, empty, esc, fmtWeight, icon, openSheet, setsSummary, skeleton, toast } from "./util.js";

let root;
let data = null;
let extra = { today: null, workouts: [], curves: [] };

const nf = (n) => Number(n).toLocaleString("de-AT");
const dm = (iso) => new Date(iso).toLocaleDateString("de-AT", { day: "numeric", month: "short" });

/** Schlichte Linie, selbst gezeichnet – im Gym ist kein fremder Server erreichbar. */
export function sparkline(points, { height = 64, label = (p) => p.value } = {}) {
  if (points.length < 2) return "";
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 100;
  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * w;
    const y = height - 6 - ((p.value - min) / span) * (height - 14);
    return [x, y];
  });
  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const [lx, ly] = coords[coords.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${height}" preserveAspectRatio="none" role="img"
      aria-label="Verlauf von ${label(points[0])} auf ${label(points[points.length - 1])}">
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"
      vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.5" fill="var(--accent)"
      vector-effect="non-scaling-stroke"/>
  </svg>`;
}

// ------------------------------------------------------- Abschnitte
function readySection() {
  const ready = data.ready || [];
  if (!ready.length) return "";
  return `<div class="card">
    <p class="eyebrow">Gewicht erhöhen</p>
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

function frequencySection() {
  const f = data.frequency;
  const vol = data.volume[data.volume.length - 1] || { groups: [], sets: 0, kg: 0 };
  const maxSets = Math.max(1, ...vol.groups.map((g) => g.sets));
  const weeks = f.weeks.slice(-8);
  const maxCount = Math.max(1, ...weeks.map((w) => w.count));
  return `<div class="card">
    <p class="eyebrow">Diese Woche</p>
    <div class="kpis compact four">
      <div><span class="big-number">${f.this_week}</span><span>Einheiten</span></div>
      <div><span class="big-number">${vol.sets}</span><span>Sätze</span></div>
      <div><span class="big-number">${nf(vol.kg)}</span><span>kg bewegt</span></div>
      <div><span class="big-number">${f.streak}</span><span>${f.streak === 1 ? "Woche" : "Wochen"} in Folge</span></div>
    </div>
    ${vol.groups.length ? `<details class="field-more"><summary>Sätze je Muskelgruppe</summary>
      <div class="bars">${vol.groups.map((g) => `
        <div class="bar-row"><span class="bar-label">${esc(g.label)}</span>
          <span class="bar"><i style="width:${(g.sets / maxSets) * 100}%"></i></span>
          <span class="bar-value">${g.sets}</span></div>`).join("")}</div></details>`
      : `<p class="hint">Diese Woche noch nichts eingetragen.</p>`}
    <h4 class="form-section">Letzte 8 Wochen</h4>
    <div class="weekbars">${weeks.map((w, i) => {
      const now = i === weeks.length - 1;   // laufende Woche immer beschriften, auch mit 0
      return `<span class="weekbar ${now ? "now" : ""}" role="img" aria-label="${now ? "Diese Woche" : `Woche ab ${dm(w.week)}`}: ${w.count} Einheiten">
        <i style="height:${Math.max(4, (w.count / maxCount) * 100)}%"></i>
        <b>${now ? w.count : w.count || ""}</b>${now ? "<small>jetzt</small>" : ""}</span>`;
    }).join("")}</div>
  </div>`;
}

function bestRow(b) {
  return `<div class="row-item" role="button" tabindex="0" data-history="${b.exercise_id}">
    <div class="row-main">
      <div class="row-title">${esc(b.exercise)}</div>
      <div class="row-meta"><span>${esc(setsSummary([b], b.unit))}</span>
        <span>${dm(b.date)}</span></div>
    </div>
    ${b.one_rm ? `<span class="row-aside">1RM ≈ <b>${String(b.one_rm).replace(".", ",")}</b></span>` : ""}
    ${icon("right", "chev")}
  </div>`;
}

/** Die fuenf juengsten Bestleistungen – das ist, was sich gerade bewegt. Der Rest klappt auf. */
function bestSection() {
  const best = [...(data.best || [])].sort((a, b) => String(b.date).localeCompare(String(a.date)));
  if (!best.length) {
    return `<div class="card"><p class="eyebrow">Bestleistungen</p>
      <p class="hint">Sobald du die erste Einheit gespeichert hast, stehen hier deine Bestwerte.</p></div>`;
  }
  const [top, rest] = [best.slice(0, 5), best.slice(5)];
  return `<div class="card">
    <p class="eyebrow">Bestleistungen · zuletzt verbessert</p>
    <div class="list flush">${top.map(bestRow).join("")}</div>
    ${rest.length ? `<details class="field-more"><summary>Alle ${best.length} anzeigen</summary>
      <div class="list flush">${rest.map(bestRow).join("")}</div></details>` : ""}
  </div>`;
}

/** Nur Zahl und Trend – Verlauf und Eintragen gibt es unter Koerper. */
function bodyweightSection() {
  const bw = data.bodyweight || [];
  const last = bw[bw.length - 1];
  const first = bw[0];
  if (!last) return "";
  const diff = first && bw.length > 1 ? (last.kg - first.kg) : null;
  return `<a class="card mini-card" href="#fortschritt/koerper">
    <span class="row-icon">${icon("weight")}</span>
    <div class="grow"><p class="eyebrow">Körpergewicht</p>
      <div class="card-title">${String(last.kg).replace(".", ",")} kg</div>
      ${diff !== null ? `<p class="hint">${diff >= 0 ? "+" : "−"}${String(Math.abs(Math.round(diff * 10) / 10)).replace(".", ",")} kg seit ${dm(first.date)}</p>` : ""}
    </div>
    ${icon("right", "chev")}
  </a>`;
}

// ----------------------------------------------- Kachelraster (MCI-Stil)
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function tile(href, title, sub, body, foot) {
  return `<a class="tile" href="${href}"><div><p class="tile-title">${title}</p><p class="tile-sub">${sub}</p></div>
    <div class="tile-body">${body}</div><div class="tile-foot">${foot}${icon("right", "chev")}</div></a>`;
}

function dotGrid() {
  const trained = new Set((extra.workouts || []).map((w) => String(w.started_at || w.finished_at || "").slice(0, 10)));
  const days = [];
  for (let i = 29; i >= 0; i--) { const d = new Date(); d.setDate(d.getDate() - i); days.push(iso(d)); }
  return `<div class="dotgrid" aria-hidden="true">${days.map((d) => `<i class="${trained.has(d) ? "on" : ""}"></i>`).join("")}</div>`;
}

function bar(value, goal, color) {
  const pct = goal ? Math.min(100, (value / goal) * 100) : 0;
  return `<span class="tbar"><i style="width:${pct}%;background:${color}"></i></span>`;
}

function tilesSection() {
  const n = extra.today?.nutrition;
  const kcal = Math.round(n?.totals.kcal || 0);
  const bw = data.bodyweight || [];
  const lastBw = bw[bw.length - 1];
  const f = data.frequency;
  const tiles = [];
  if (n) tiles.push(tile("#ernaehrung", "Kalorien", "Heute", ring(kcal, n.goals.kcal, { size: 96 }),
    `<b>${kcal}</b>${n.goals.kcal ? ` / ${n.goals.kcal}` : ""}`));
  tiles.push(tile("#fortschritt/verlauf", "Trainings", "Letzte 30 Tage", dotGrid(), `<b>${f.this_week}</b> diese Woche`));
  if (lastBw) tiles.push(`<div class="tile wide">${tile("#fortschritt/koerper", "Körpergewicht", "Letzte Einträge",
    sparkline(bw.slice(-7).map((b) => ({ value: b.kg })), { height: 56 }),
    `<b>${String(lastBw.kg).replace(".", ",")}</b> kg`).replace('<a class="tile"', '<a class="tile-in"')}</div>`);
  if (n) {
    const p = Math.round(n.totals.protein || 0);
    const c = Math.round(n.totals.carbs || 0);
    tiles.push(tile("#ernaehrung", "Protein", "Heute", bar(p, n.goals.protein, "var(--c-protein)"), `<b>${p}</b> g`));
    tiles.push(tile("#ernaehrung", "Carbs", "Heute", bar(c, n.goals.carbs, "var(--c-carbs)"), `<b>${c}</b> g`));
  }
  for (const cv of extra.curves) {
    tiles.push(tile("#fortschritt", esc(cv.name), "Letzte Einheiten",
      sparkline(cv.points, { height: 56 }), `<b>${String(cv.last).replace(".", ",")}</b> ${cv.unit}`));
  }
  return `<div class="tiles">${tiles.join("")}</div>`;
}

function render() {
  const nothing = !data.best.length && !data.bodyweight.length && !data.frequency.total;
  root.innerHTML = `${nothing ? empty("Noch keine Daten",
      "Nach der ersten gespeicherten Einheit siehst du hier deine Entwicklung.") : ""}
    ${nothing ? "" : tilesSection()}
    ${readySection()}
    ${data.frequency.total ? frequencySection() : ""}
    ${nothing ? "" : bestSection()}
    ${bodyweightSection()}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(5);
  data = await api("progress");
  render();
  loadExtra();
}

/** Kacheldaten still nachladen; fehlt etwas, bleibt die Kachel einfach weg. */
async function loadExtra() {
  const top = [...(data.best || [])].sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 2);
  const [today, workouts, ...hist] = await Promise.all([
    api("today").catch(() => null), api("workouts?limit=60").catch(() => []),
    ...top.map((b) => api(`history/${b.exercise_id}`).catch(() => [])),
  ]);
  extra = { today, workouts, curves: top.map((b, i) => {
    const pts = (hist[i] || []).slice(-7).map((p) => ({ value: p.value }));
    return pts.length > 1 ? { name: b.exercise, points: pts, last: pts[pts.length - 1].value, unit: b.unit === "reps" ? "Whd." : "kg" } : null;
  }).filter(Boolean) };
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
      body: `${points.length > 1 ? sparkline(line, { height: 96, label: (p) => p.value })
        : `<p class="hint">Für einen Verlauf braucht es mindestens zwei Einheiten.</p>`}
        <div class="list flush">${[...points].reverse().map((p) => `
          <div class="row-item"><div class="row-main">
            <div class="row-title">${esc(dm(p.date))}</div>
            <div class="row-meta"><span>${p.reps} Whd.${p.weight != null
              ? ` @ ${esc(fmtWeight(p.weight, b?.unit))}` : ""}</span></div>
          </div><span class="row-aside">${p.weight != null
            ? `1RM ≈ ${String(p.value).replace(".", ",")}` : `${p.value} Whd.`}</span></div>`).join("")}</div>`,
    });
  } catch (e) { toast(e.message, { error: true }); }
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const h = ev.target.closest("[data-history]");
    if (h) return showHistory(Number(h.dataset.history));
  });
}
