/** Fortschritt: Bestleistungen, Volumen und Häufigkeit, Körpergewicht, nächste Zielwerte. */
import { $, api, closeSheet, empty, esc, fmtWeight, formData, formError, getProfile, icon,
  openSheet, setsSummary, skeleton, submitOnce, toast } from "./util.js";
import * as outbox from "./outbox.js";

let root;
let data = null;

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
    <div class="kpis compact">
      <div><span class="big-number">${f.this_week}</span><span>Einheiten</span></div>
      <div><span class="big-number">${vol.sets}</span><span>Sätze</span></div>
      <div><span class="big-number">${nf(vol.kg)}</span><span>kg bewegt</span></div>
      <div><span class="big-number">${f.streak}</span><span>${f.streak === 1 ? "Woche" : "Wochen"} in Folge</span></div>
    </div>
    ${vol.groups.length ? `<h4 class="form-section">Sätze je Muskelgruppe</h4>
      <div class="bars">${vol.groups.map((g) => `
        <div class="bar-row"><span class="bar-label">${esc(g.label)}</span>
          <span class="bar"><i style="width:${(g.sets / maxSets) * 100}%"></i></span>
          <span class="bar-value">${g.sets}</span></div>`).join("")}</div>`
      : `<p class="hint">Diese Woche noch nichts eingetragen.</p>`}
    <h4 class="form-section">Letzte 8 Wochen</h4>
    <div class="weekbars">${weeks.map((w) => `
      <span class="weekbar" title="Woche ab ${dm(w.week)}: ${w.count} Einheiten">
        <i style="height:${Math.max(4, (w.count / maxCount) * 100)}%"></i>
        <b>${w.count || ""}</b></span>`).join("")}</div>
  </div>`;
}

function bestSection() {
  const best = data.best || [];
  if (!best.length) {
    return `<div class="card"><p class="eyebrow">Bestleistungen</p>
      <p class="hint">Sobald du die erste Einheit gespeichert hast, stehen hier deine Bestwerte.</p></div>`;
  }
  return `<div class="card">
    <p class="eyebrow">Bestleistungen</p>
    <div class="list flush">${best.map((b) => `
      <div class="row-item" role="button" tabindex="0" data-history="${b.exercise_id}">
        <div class="row-main">
          <div class="row-title">${esc(b.exercise)}</div>
          <div class="row-meta"><span>${esc(setsSummary([b], b.unit))}</span>
            <span>${dm(b.date)}</span></div>
        </div>
        ${b.one_rm ? `<span class="row-aside">1RM ≈ <b>${String(b.one_rm).replace(".", ",")}</b></span>` : ""}
        ${icon("right", "chev")}
      </div>`).join("")}</div>
  </div>`;
}

function bodyweightSection() {
  const bw = data.bodyweight || [];
  const last = bw[bw.length - 1];
  const first = bw[0];
  const diff = last && first && bw.length > 1 ? (last.kg - first.kg) : null;
  const points = bw.map((p) => ({ value: p.avg, date: p.date }));
  return `<div class="card">
    <p class="eyebrow">Körpergewicht</p>
    ${last ? `<div class="bw-head">
        <span class="big-number">${String(last.kg).replace(".", ",")} kg</span>
        <span class="hint">${dm(last.date)}${diff !== null
          ? ` · ${diff >= 0 ? "+" : "−"}${String(Math.abs(Math.round(diff * 10) / 10)).replace(".", ",")} kg seit ${dm(first.date)}` : ""}</span>
      </div>
      ${sparkline(points, { label: (p) => `${p.value} kg` })}`
      : `<p class="hint">Noch kein Gewicht eingetragen.</p>`}
    <div class="btn-row">
      <button type="button" class="btn small tonal" data-add-weight>${icon("plus")} Gewicht eintragen</button>
    </div>
  </div>`;
}

function nextSection() {
  const next = data.next || [];
  if (!next.length) return "";
  const block = next[0];
  return `<div class="card">
    <p class="eyebrow">Nächstes Mal · ${esc(block.day)}</p>
    <div class="list flush">${block.items.map((i) => `
      <div class="row-item">
        <div class="row-main">
          <div class="row-title">${esc(i.exercise)}</div>
          <div class="row-meta"><span>${esc(i.reason)}</span></div>
        </div>
        <span class="row-aside">${i.sets} × ${i.reps}${i.weight != null
          ? ` @ ${esc(fmtWeight(i.weight, i.unit))}` : ""}</span>
      </div>`).join("")}</div>
  </div>`;
}

function render() {
  const nothing = !data.best.length && !data.bodyweight.length && !data.frequency.total;
  root.innerHTML = `${nothing ? empty("Noch keine Daten",
      "Nach der ersten gespeicherten Einheit siehst du hier deine Entwicklung.") : ""}
    ${readySection()}
    ${data.frequency.total ? frequencySection() : ""}
    ${nothing ? "" : bestSection()}
    ${bodyweightSection()}
    ${nextSection()}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(5);
  data = await api("progress");
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

function addWeight() {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const last = data.bodyweight[data.bodyweight.length - 1];
  const sheet = openSheet({
    title: "Gewicht eintragen",
    body: `<form id="bwForm" class="form">
      <div class="field-row">
        <label class="field">Gewicht<input name="kg" type="number" inputmode="decimal" autofocus
          min="1" max="500" step="0.1" required value="${last?.kg ?? ""}"> </label>
        <label class="field">Datum<input name="date" type="date" required value="${iso}" max="${iso}"></label>
      </div>
      <p class="hint">Ein Eintrag pro Tag – ein zweiter überschreibt den ersten.</p>
    </form>`,
    footer: `<span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="submit" class="btn primary" form="bwForm">Speichern</button>`,
  });
  const form = $("#bwForm", sheet);
  submitOnce(form, async () => {
    try {
      const body = { ...formData(form), profile_id: getProfile() };
      // Auch ohne Netz speicherbar: der Eintrag geht sonst in den Ausgang.
      const res = await outbox.send({ path: "bodyweight", body });
      closeSheet();
      toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist"
        : "Gewicht gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { formError(form, e); }
  });
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const h = ev.target.closest("[data-history]");
    if (h) return showHistory(Number(h.dataset.history));
    if (ev.target.closest("[data-add-weight]")) return addWeight();
  });
}
