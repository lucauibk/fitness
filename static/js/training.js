/** Laufende Einheit. Alles passiert zuerst im Geraetespeicher – ohne Netz, ohne Warten.
 *  Erst beim Beenden geht die komplette Einheit in einem Stueck an den Pi (bzw. in den
 *  Ausgang, wenn gerade keine Verbindung da ist). */
import { $, $$, api, armDelete, closeSheet, empty, esc, formData, formError, getProfile,
  icon, openSheet, prefs, setsSummary, skeleton, toast } from "./util.js";
import * as outbox from "./outbox.js";

let root;
let today = null;     // /api/today fuer den Startbildschirm
let timer = null;

// ------------------------------------------------------------- Entwurf
export const getDraft = () => prefs.get("draft");
export const hasDraft = () => Boolean(prefs.get("draft"));
const saveDraft = (d) => { prefs.set("draft", d); };
const clearDraft = () => { prefs.set("draft", null); };

/** Aus einem Trainingstag (mit Vorschlägen) einen Entwurf bauen. */
export function startDraft(day) {
  const draft = {
    client_id: outbox.uuid(),
    profile_id: getProfile(),
    split_day_id: day.id,
    title: day.label,
    started_at: new Date().toISOString(),
    exercises: day.exercises.map((e) => ({
      exercise_id: e.exercise_id,
      name: e.name,
      unit: e.unit,
      kind: e.kind,
      rest: e.rest_seconds || null,
      adjusted: e.adjusted || null,
      reps_min: e.target_reps_min,
      reps_max: e.target_reps_max,
      last: e.last,
      sets: Array.from({ length: e.target_sets || 3 }, (_, i) => ({
        set_no: i + 1,
        reps: e.suggest.reps ?? e.target_reps_min ?? null,
        weight: e.suggest.weight ?? null,
        done: false,
      })),
    })),
    notes: "",
  };
  saveDraft(draft);
  window.dispatchEvent(new CustomEvent("outbox-changed")); // Punkt in der Navigation
  return draft;
}

function elapsed(draft) {
  const ms = Date.now() - new Date(draft.started_at).getTime();
  const min = Math.max(0, Math.floor(ms / 60000));
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")} min`;
}

const doneCount = (draft) => draft.exercises
  .reduce((n, e) => n + e.sets.filter((s) => s.done).length, 0);
const totalCount = (draft) => draft.exercises.reduce((n, e) => n + e.sets.length, 0);

// ------------------------------------------------------------ Anzeige
function setRow(ex, exIndex, s, i) {
  const unitLabel = ex.unit === "koerper" ? "kg extra" : "kg";
  return `<div class="set-row ${s.done ? "is-done" : ""}" data-ex="${exIndex}" data-set="${i}">
    <span class="set-no">${s.set_no}</span>
    <label class="set-field"><span>Whd.</span>
      <input type="number" inputmode="numeric" min="0" max="1000" step="1"
             value="${s.reps ?? ""}" data-field="reps" aria-label="Wiederholungen Satz ${s.set_no}">
    </label>
    <label class="set-field"><span>${unitLabel}</span>
      <input type="number" inputmode="decimal" min="0" max="1000" step="0.5"
             value="${s.weight ?? ""}" data-field="weight" aria-label="Gewicht Satz ${s.set_no}">
    </label>
    <button type="button" class="set-check" data-toggle aria-pressed="${s.done}"
            aria-label="Satz ${s.set_no} ${s.done ? "nicht erledigt" : "erledigt"}">${icon("check")}</button>
  </div>`;
}

function exerciseCard(ex, index) {
  const range = ex.reps_min === ex.reps_max ? `${ex.reps_min}` : `${ex.reps_min}–${ex.reps_max}`;
  const done = ex.sets.filter((s) => s.done).length;
  return `<div class="card exercise-card ${done === ex.sets.length ? "is-done" : ""}">
    <div class="ex-head">
      <div>
        <div class="card-title">${esc(ex.name)}</div>
        <div class="row-meta">
          <span>Ziel ${range} Whd.</span>
          ${ex.adjusted ? `<span class="soon-text">angepasst: ${esc(ex.adjusted)}</span>` : ""}
          ${ex.last?.length ? `<span>letztes Mal ${esc(setsSummary(ex.last, ex.unit))}</span>` : ""}
        </div>
      </div>
      <span class="pill ${done === ex.sets.length ? "ok" : ""}">${done}/${ex.sets.length}</span>
    </div>
    <div class="sets">${ex.sets.map((s, i) => setRow(ex, index, s, i)).join("")}</div>
    <div class="btn-row">
      <button type="button" class="btn small ghost" data-add-set="${index}">${icon("plus")} Satz</button>
      ${ex.sets.length > 1 ? `<button type="button" class="btn small ghost" data-drop-set="${index}">Satz weniger</button>` : ""}
      <span class="grow"></span>
      <button type="button" class="btn small ghost danger" data-drop-ex="${index}" aria-label="Übung entfernen">${icon("trash")}</button>
    </div>
  </div>`;
}

function renderDraft(draft) {
  const done = doneCount(draft);
  const total = totalCount(draft);
  root.innerHTML = `<div class="page-head">
      <h2 class="page-title">${esc(draft.title || "Einheit")}</h2>
      <p class="page-sub"><span data-elapsed>${elapsed(draft)}</span> · ${done}/${total} Sätze</p>
    </div>
    <div class="progress lg"><i style="width:${total ? (done / total) * 100 : 0}%"></i></div>
    ${draft.exercises.map(exerciseCard).join("")}
    <div class="btn-row wide">
      <button type="button" class="btn ghost" data-add-ex>${icon("plus")} Übung</button>
    </div>
    <label class="field">Notiz<textarea name="notes" rows="2" data-notes
      placeholder="optional, z. B. Rücken hat gezwickt">${esc(draft.notes || "")}</textarea></label>
    <div class="btn-row wide sticky-actions">
      <button type="button" class="btn danger ghost" data-cancel>Verwerfen</button>
      <span class="grow"></span>
      <button type="button" class="btn primary" data-finish>${icon("check")} Einheit beenden</button>
    </div>`;
  if (!timer) {
    timer = setInterval(() => {
      const el = $("[data-elapsed]", root);
      const d = getDraft();
      if (el && d) el.textContent = elapsed(d);
    }, 30000);
  }
}

function renderStart() {
  const t = today;
  if (!t?.split) {
    root.innerHTML = `${empty("Noch kein Plan", "Erstelle unter <b>Plan</b> einen Split oder lass dir einen vom Assistenten bauen.",
      { label: "Zum Plan", attrs: 'data-goto="training/plan"' })}
      <div class="btn-row"><button type="button" class="btn ghost" data-free>${icon("plus")} Freies Training</button></div>`;
    return;
  }
  const rec = t.day;
  const lvl = t.checkin?.level;
  const others = t.days.filter((d) => d.id !== rec?.id);
  root.innerHTML = `${lvl && lvl.key !== "voll" ? `<p class="callout">${icon("warn")} ${esc(lvl.title)} – die Vorgaben sind angepasst.</p>` : ""}
    ${rec ? `<div class="card hero">
      <p class="eyebrow">${t.fixed ? "Heute geplant" : "Als Nächstes dran"}</p>
      <h3 class="hero-title">${esc(rec.label)}</h3>
      <p class="hint">${rec.exercises.length} Übungen · ${rec.exercises.reduce((n, e) => n + e.target_sets, 0)} Sätze</p>
      <div class="btn-row"><button type="button" class="btn primary" data-start="${rec.id}">${icon("play")} Einheit starten</button></div>
    </div>` : ""}
    ${others.length ? `<h3 class="group-title">Anderer Tag</h3>
      <div class="list">${others.map((d) => `<div class="row-item" role="button" tabindex="0" data-start="${d.id}">
        <div class="row-main"><div class="row-title">${esc(d.label)}</div></div>${icon("right", "chev")}</div>`).join("")}</div>` : ""}
    <div class="btn-row"><button type="button" class="btn ghost" data-free>${icon("plus")} Freies Training</button></div>`;
}

export async function load() {
  const draft = getDraft();
  if (draft) return renderDraft(draft);
  if (!today) root.innerHTML = skeleton(3);
  today = await api("today");
  renderStart();
}

async function begin(dayId) {
  try {
    const day = today?.day?.id === dayId ? today.day : await api(`day/${dayId}`);
    startDraft(day);
    load();
  } catch (e) {
    toast(e.offline ? "Dieser Trainingstag wurde noch nicht geladen – einmal mit Netz öffnen." : e.message, { error: true });
  }
}

function beginFree() {
  saveDraft({ client_id: outbox.uuid(), profile_id: getProfile(), split_day_id: null,
    title: "Freies Training", started_at: new Date().toISOString(), exercises: [], notes: "" });
  window.dispatchEvent(new CustomEvent("outbox-changed"));
  load();
  addExercise();
}

// ------------------------------------------------------- Bearbeiten
function update(fn) {
  const draft = getDraft();
  if (!draft) return;
  fn(draft);
  saveDraft(draft);
  renderDraft(draft);
}

/** Eine Zahl im Entwurf aendern, ohne neu zu zeichnen – sonst springt die Tastatur zu. */
function setValue(exIndex, setIndex, field, raw) {
  const draft = getDraft();
  if (!draft) return;
  const value = raw === "" ? null : Number(raw);
  if (value !== null && (Number.isNaN(value) || value < 0)) return;
  draft.exercises[exIndex].sets[setIndex][field] = value;
  saveDraft(draft);
}

function addExercise() {
  api("exercises").then((all) => {
    const active = all.filter((e) => !e.archived);
    const sheet = openSheet({
      title: "Übung hinzufügen",
      body: `<label class="search">${icon("search")}<input type="search" data-q
          placeholder="Übung suchen…" aria-label="Übung suchen"></label>
        <div class="list" data-results>${active.map(exOption).join("")}</div>`,
    });
    const input = $("[data-q]", sheet);
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      $("[data-results]", sheet).innerHTML = active
        .filter((e) => e.name.toLowerCase().includes(q)).map(exOption).join("")
        || `<p class="hint pad">Keine Übung passt.</p>`;
    });
    sheet.addEventListener("click", (ev) => {
      const t = ev.target.closest("[data-pick-ex]");
      if (!t) return;
      const ex = active.find((e) => e.id === Number(t.dataset.pickEx));
      closeSheet();
      update((d) => d.exercises.push({
        exercise_id: ex.id, name: ex.name, unit: ex.unit, reps_min: 8, reps_max: 12, last: [],
        sets: [{ set_no: 1, reps: null, weight: null, done: false }],
      }));
    });
  }).catch((e) => toast(e.message, { error: true }));
}

const exOption = (e) => `<div class="row-item" role="button" tabindex="0" data-pick-ex="${e.id}">
  <div class="row-main"><div class="row-title">${esc(e.name)}</div></div></div>`;

// --------------------------------------------------------- Beenden
async function finish() {
  const draft = getDraft();
  if (!draft) return;
  const sets = [];
  for (const ex of draft.exercises) {
    let n = 0;
    for (const s of ex.sets) {
      if (!s.done || !s.reps) continue;
      n += 1;
      sets.push({ exercise_id: ex.exercise_id, set_no: n, reps: s.reps, weight: s.weight });
    }
  }
  if (!sets.length) {
    toast("Noch kein Satz abgehakt.", { error: true });
    return;
  }
  const body = {
    profile_id: draft.profile_id || getProfile(),
    split_day_id: draft.split_day_id,
    title: draft.title,
    started_at: draft.started_at,
    finished_at: new Date().toISOString(),
    notes: draft.notes || null,
    client_id: draft.client_id,
    sets,
  };
  try {
    const res = await outbox.send({ path: "workouts", body });
    clearDraft();
    today = null;
    toast(res.queued ? "Gespeichert – wird gesendet, sobald du wieder Empfang hast"
      : `Einheit gespeichert · ${sets.length} Sätze`);
    window.dispatchEvent(new CustomEvent("outbox-changed"));
    window.dispatchEvent(new CustomEvent("data-changed"));
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---------------------------------------------------------- Pausen-Timer
const REST = { compound: 120, isolation: 75 };
let restTick = null;

function restBar() {
  let bar = document.getElementById("restBar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "restBar";
    bar.className = "rest-bar";
    bar.setAttribute("role", "timer");
    bar.innerHTML = `<span class="rest-label">Pause</span><b data-rest-time></b>
      <button type="button" class="btn small ghost" data-rest="-15">−15 s</button>
      <button type="button" class="btn small ghost" data-rest="15">+15 s</button>
      <button type="button" class="btn small tonal" data-rest="skip">Weiter</button>`;
    bar.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-rest]");
      if (!b) return;
      if (b.dataset.rest === "skip") return stopRest();
      const r = prefs.get("rest");
      if (r) { r.until += Number(b.dataset.rest) * 1000; prefs.set("rest", r); tick(); }
    });
    document.body.appendChild(bar);
  }
  return bar;
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.frequency.value = 880;
    g.gain.setValueAtTime(0.2, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.5);
  } catch { /* kein Ton moeglich */ }
}

function tick() {
  const r = prefs.get("rest");
  if (!r) return stopRest();
  const left = Math.round((r.until - Date.now()) / 1000);
  const bar = restBar();
  if (left <= 0) {
    navigator.vibrate?.([200, 100, 200]);
    beep();
    return stopRest();
  }
  bar.querySelector("[data-rest-time]").textContent =
    `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  bar.hidden = false;
}

function startRest(seconds) {
  const custom = prefs.get("restSeconds");
  prefs.set("rest", { until: Date.now() + (custom || seconds) * 1000 });
  clearInterval(restTick);
  restTick = setInterval(tick, 500);
  tick();
}

function stopRest() {
  prefs.set("rest", null);
  clearInterval(restTick);
  restTick = null;
  const bar = document.getElementById("restBar");
  if (bar) bar.hidden = true;
}

// Laeuft nach einem Neuladen weiter
if (prefs.get("rest")) { restTick = setInterval(tick, 500); }

// ------------------------------------------------------------- Mount
export async function mount(el) {
  root = el;

  el.addEventListener("click", (ev) => {
    const t = (sel) => ev.target.closest(sel);
    const start = t("[data-start]");
    if (start) return begin(Number(start.dataset.start));
    if (t("[data-free]")) return beginFree();
    const goto = t("[data-goto]");
    if (goto) { location.hash = goto.dataset.goto; return; }

    const toggle = t("[data-toggle]");
    if (toggle) {
      const row = toggle.closest(".set-row");
      const [i, j] = [Number(row.dataset.ex), Number(row.dataset.set)];
      // Ein Satz gilt nur mit Wiederholungen als gemacht.
      const reps = row.querySelector('[data-field="reps"]').value;
      if (!reps) {
        toast("Trag zuerst die Wiederholungen ein.", { error: true });
        row.querySelector('[data-field="reps"]').focus();
        return;
      }
      let nowDone = false;
      let kind = null;
      let rest = null;
      update((d) => {
        const s = d.exercises[i].sets[j];
        s.done = !s.done;
        nowDone = s.done;
        kind = d.exercises[i].kind;
        rest = d.exercises[i].rest;   // empfohlene Pause aus dem Plan
      });
      if (nowDone) startRest(rest || (kind === "compound" ? REST.compound : REST.isolation));
      return;
    }
    const add = t("[data-add-set]");
    if (add) {
      return update((d) => {
        const ex = d.exercises[Number(add.dataset.addSet)];
        const last = ex.sets[ex.sets.length - 1];
        ex.sets.push({ set_no: ex.sets.length + 1, reps: last?.reps ?? null,
          weight: last?.weight ?? null, done: false });
      });
    }
    const drop = t("[data-drop-set]");
    if (drop) {
      return update((d) => {
        const ex = d.exercises[Number(drop.dataset.dropSet)];
        if (ex.sets.length > 1) ex.sets.pop();
      });
    }
    const dropEx = t("[data-drop-ex]");
    if (dropEx) {
      return armDelete(dropEx, () => update((d) => {
        d.exercises.splice(Number(dropEx.dataset.dropEx), 1);
      }), icon("trash"), "Entfernen?");
    }
    if (t("[data-add-ex]")) return addExercise();
    if (t("[data-finish]")) { stopRest(); return finish(); }
    const cancel = t("[data-cancel]");
    if (cancel) {
      return armDelete(cancel, () => {
        stopRest();
        clearDraft();
        today = null;
        toast("Einheit verworfen");
        window.dispatchEvent(new CustomEvent("outbox-changed"));
        load();
      }, "Verwerfen", "Alles verwerfen?");
    }
  });

  // Zahlen und Notiz direkt in den Entwurf schreiben – bei jedem Tastendruck, damit
  // ein geschlossener Tab nichts verliert.
  el.addEventListener("input", (ev) => {
    const field = ev.target.closest("[data-field]");
    if (field) {
      const row = field.closest(".set-row");
      return setValue(Number(row.dataset.ex), Number(row.dataset.set),
        field.dataset.field, field.value);
    }
    if (ev.target.closest("[data-notes]")) {
      const draft = getDraft();
      if (draft) { draft.notes = ev.target.value; saveDraft(draft); }
    }
  });
}
