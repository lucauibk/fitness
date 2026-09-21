/** Cardio: Einheit eintragen (mit kcal-Schätzung) und die letzten Einheiten. */
import { $, api, esc, formData, formError, getProfile, hasDirtyForm, icon, prefs, skeleton, submitOnce,
  toast, parseYmd } from "./util.js";
import * as outbox from "./outbox.js";

let root;
let activities = null;
let recent = null;
let weight = null;

const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

function estimate(form) {
  const a = activities.find((x) => x.key === form.activity.value);
  const min = Number(form.minutes.value) || 0;
  return a && min ? Math.round(a.met * (weight || 75) * min / 60) : 0;
}

function weekSummary(list) {
  const monday = new Date();
  monday.setHours(0, 0, 0, 0);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const week = list.filter((c) => parseYmd(c.date) >= monday);
  return {
    minutes: Math.round(week.reduce((n, c) => n + c.minutes, 0)),
    km: Math.round(week.reduce((n, c) => n + (c.distance_km || 0), 0) * 10) / 10,
    kcal: Math.round(week.reduce((n, c) => n + (c.kcal || 0), 0)),
  };
}

function render() {
  const last = prefs.get("cardio.last") || "laufen";
  const list = [...outbox.pending("cardio").map((c) => ({ ...c,
    label: activities.find((a) => a.key === c.activity)?.label })), ...recent];
  const w = weekSummary(list);
  root.innerHTML = `<div class="card">
      <p class="eyebrow">Diese Woche</p>
      <div class="kpis compact">
        <div><span class="big-number">${w.minutes}</span><span>Minuten</span></div>
        <div><span class="big-number">${String(w.km).replace(".", ",")}</span><span>km</span></div>
        <div><span class="big-number">${w.kcal}</span><span>kcal</span></div>
      </div>
      <p class="hint">Richtwert der WHO: 150–300 Minuten moderate Bewegung pro Woche.</p>
    </div>
    <div class="card">
      <p class="eyebrow">Cardio eintragen</p>
      <form id="cardioForm" class="form">
        <label class="field">Aktivität
          <select name="activity">${activities.map((a) => `<option value="${a.key}"${a.key === last ? " selected" : ""}>${esc(a.label)}</option>`).join("")}</select>
        </label>
        <div class="field-row">
          <label class="field">Dauer (min)<input name="minutes" type="number" inputmode="decimal" min="1" max="600" required></label>
          <label class="field">Strecke (km)<input name="distance_km" type="number" inputmode="decimal" min="0" max="500" step="0.01" placeholder="optional"></label>
        </div>
        <div class="field-row">
          <label class="field">Puls ⌀<input name="avg_hr" type="number" inputmode="numeric" min="30" max="240" placeholder="optional"></label>
          <label class="field">Datum<input name="date" type="date" value="${todayIso()}" max="${todayIso()}" required></label>
        </div>
        <p class="hint" data-estimate>Kalorien werden aus Aktivität, Dauer und Gewicht geschätzt.</p>
        <div class="btn-row"><button type="submit" class="btn primary">${icon("check")} Speichern</button></div>
      </form>
    </div>
    ${list.length ? `<h3 class="group-title">Zuletzt</h3><div class="list">${list.slice(0, 8).map((c) => `
      <div class="row-item ${c.pending ? "is-pending" : ""}">
        <span class="row-icon">${icon("run")}</span>
        <div class="row-main"><div class="row-title">${esc(c.label || c.activity)}</div>
          <div class="row-meta"><span>${parseYmd(c.date).toLocaleDateString("de-AT", { weekday: "short", day: "numeric", month: "short" })}</span>
            <span>${Math.round(c.minutes)} min</span>
            ${c.distance_km ? `<span>${String(c.distance_km).replace(".", ",")} km</span>` : ""}
            ${c.kcal ? `<span>≈ ${Math.round(c.kcal)} kcal</span>` : ""}</div></div>
        ${c.pending ? `<span class="pill">wartet</span>` : ""}
      </div>`).join("")}</div>
      <p class="hint pad">Alle Einheiten: <a href="#fortschritt/verlauf">Fortschritt → Verlauf</a></p>` : ""}`;

  const form = $("#cardioForm", root);
  const upd = () => {
    const k = estimate(form);
    $("[data-estimate]", root).textContent = k ? `≈ ${k} kcal (geschätzt)`
      : "Kalorien werden aus Aktivität, Dauer und Gewicht geschätzt.";
  };
  form.addEventListener("input", upd);
  submitOnce(form, async () => {
    try {
      const data = formData(form);
      prefs.set("cardio.last", data.activity);
      const body = { ...data, profile_id: getProfile(), client_id: outbox.uuid() };
      const res = await outbox.send({ path: "cardio", body });
      delete form.dataset.dirty;
      toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist" : "Cardio gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (e) { formError(form, e); }
  });
}

export async function load() {
  if (root.contains(document.activeElement) && document.activeElement.matches("input, select")) return;
  if (hasDirtyForm(root)) return;
  if (!activities) root.innerHTML = skeleton(4);
  const [acts, list, bw] = await Promise.all([
    activities || api("cardio/activities"), api("cardio?limit=30"), api("bodyweight")]);
  activities = acts;
  recent = list;
  weight = bw.length ? bw[bw.length - 1].kg : null;
  render();
}

export async function mount(el) {
  root = el;
}
