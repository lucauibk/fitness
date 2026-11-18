/** Laufende Einheit. Alles passiert zuerst im Geraetespeicher – ohne Netz, ohne Warten.
 *  Erst beim Beenden geht die komplette Einheit in einem Stueck an den Pi (bzw. in den
 *  Ausgang, wenn gerade keine Verbindung da ist). */
import { $, $$, api, armDelete, closeSheet, empty, esc, heroNotesHtml, reviewHtml, watchCoach, getProfile,
  icon, openSheet, prefs, setsSummary, skeleton, toast, WEEKDAYS_LONG } from "./util.js";
import { weekStrip } from "./erfolge.js";
import * as outbox from "./outbox.js";
import { openCoach } from "./coach.js";
import { openExercise } from "./uebungen.js";
import { nextLabel, nextSet, nextTraining } from "./naechster.js";
import { rememberRest, restFor, restNote } from "./pause.js";

let root;
let today = null;     // /api/today fuer den Startbildschirm
let timer = null;

// ------------------------------------------------------------- Entwurf
// Ein Entwurf je Profil: teilen sich zwei Personen ein Geraet, darf der Start einer
// Einheit unter Profil B den offenen Entwurf von Profil A nicht ueberschreiben.
const draftKey = (profileId) => `draft.${profileId}`;
export function getDraft() {
  const id = getProfile();
  // Frueher gab es nur einen Platz "draft" fuers ganze Geraet – einmalig umziehen.
  const legacy = prefs.get("draft");
  if (legacy) {
    if (!prefs.get(draftKey(legacy.profile_id))) prefs.set(draftKey(legacy.profile_id), legacy);
    prefs.set("draft", null);
  }
  const d = prefs.get(draftKey(id));
  return d && d.profile_id === id ? d : null;
}
export const hasDraft = () => Boolean(getDraft());
const saveDraft = (d) => { prefs.set(draftKey(d.profile_id), d); };
const clearDraft = () => { prefs.set(draftKey(getProfile()), null); };

/** Aus einem Trainingstag (mit Vorschlägen) einen Entwurf bauen. */
export function startDraft(day) {
  const draft = {
    client_id: outbox.uuid(),
    profile_id: getProfile(),
    split_day_id: day.id,
    title: day.label,
    block: day.block ? { week: day.block.week, weeks: day.block.weeks, rir: day.block.rir, deload: day.block.deload } : null,
    started_at: new Date().toISOString(),
    exercises: day.exercises.map((e) => ({
      exercise_id: e.exercise_id,
      name: e.name,
      unit: e.unit,
      kind: e.kind,
      pattern: e.pattern,
      rest: e.rest_seconds || null,
      adjusted: e.adjusted || null,
      estimate: Boolean(e.suggest?.estimate),   // erstes Mal: Startgewicht ist geschätzt, Tasten kalibrieren
      reps_min: e.target_reps_min,
      reps_max: e.target_reps_max,
      last: e.last,
      sets: Array.from({ length: e.target_sets || 3 }, (_, i) => ({
        set_no: i + 1,
        reps: e.suggest.reps ?? e.target_reps_min ?? null,
        weight: e.suggest.weight ?? null,
        rpe: null,
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
function setRow(ex, exIndex, s, i, current = false) {
  // Aufwaermsaetze zaehlen nicht mit: nach "W" kommt Satz 1, nicht Satz 2.
  const no = ex.sets.slice(0, i + 1).filter((x) => !x.warmup).length;
  const name = s.warmup ? "Aufwärmsatz" : `Satz ${no}`;
  // Das letzte Mal nur dort, wo es vom eingetragenen Wert abweicht – die Felder sind
  // ohnehin mit dem Vorschlag vorbelegt, eine Wiederholung derselben Zahl ist nur Rauschen.
  const last = ex.last?.[i];
  // Waren letztes Mal alle Saetze gleich, steht das schon in der Kopfzeile ("4×10 @ 65 kg").
  const uniform = ex.last?.length > 1 && ex.last.every((x) => x.reps === ex.last[0].reps && x.weight === ex.last[0].weight);
  const hint = (prev, cur) => (!uniform && prev != null && prev !== cur
    ? `<small class="set-last">zuletzt ${String(prev).replace(".", ",")}</small>` : "");
  return `<div class="set-row ${s.done ? "is-done" : ""}${current ? " is-current" : ""}" data-ex="${exIndex}" data-set="${i}">
    <button type="button" class="set-no ${s.warmup ? "is-warmup" : ""}" data-warmup
            aria-label="${s.warmup ? "Aufwärmsatz – antippen für normalen Satz" : `Satz ${no} – antippen für Aufwärmsatz`}"
            aria-pressed="${Boolean(s.warmup)}">${s.warmup ? "W" : no}</button>
    <label class="set-field">
      <input type="number" inputmode="numeric" min="0" max="1000" step="1"
             value="${s.reps ?? ""}" data-field="reps" aria-label="Wiederholungen ${name}">
      ${hint(last?.reps, s.reps)}
    </label>
    <label class="set-field">
      <input type="number" inputmode="decimal" min="0" max="1000" step="0.5"
             value="${s.weight ?? ""}" data-field="weight" aria-label="Gewicht ${name}">
      ${hint(last?.weight, s.weight)}
    </label>
    <button type="button" class="set-check" data-toggle aria-pressed="${s.done}"
            aria-label="${name} ${s.done ? "nicht erledigt" : "erledigt"}">${icon("check")}</button>
  </div>`;
}

/** Der letzte abgehakte Arbeitssatz – an ihm haengt die Anstrengung (RPE) der Uebung. */
const lastWorkSet = (ex) => [...ex.sets].reverse().find((x) => x.done && !x.warmup);

/** RPE einmal pro Uebung statt als Spalte in jeder Zeile: erscheint, sobald die Uebung
 *  fertig ist, auch in der zugeklappten Karte – optional, ein Tipp. */
/** Erstes Mal an dieser Übung: nach dem ersten Satz sagt man, wie es war – die übrigen Sätze passen sich an. */
function calibRow(ex, index) {
  return `<div class="rpe-row calib" role="group" aria-label="Wie war der Satz bei ${esc(ex.name)}?">
    <span class="hint">Startgewicht war geschätzt – wie war der Satz?</span>
    <div class="rpe-chips">
      <button type="button" class="chip-btn" data-calib="${index}" data-level="easy">leicht</button>
      <button type="button" class="chip-btn" data-calib="${index}" data-level="ok">passt</button>
      <button type="button" class="chip-btn" data-calib="${index}" data-level="hard">zu schwer</button>
    </div></div>`;
}

function rpeRow(ex, index) {
  const s = lastWorkSet(ex);
  if (!s) return "";
  return `<div class="rpe-row" role="group" aria-label="Anstrengung letzter Satz ${esc(ex.name)}">
    <span class="hint">Wie schwer war der letzte Satz?</span>
    <div class="rpe-chips">${[6, 7, 8, 9, 10].map((v) => `<button type="button" class="chip-btn"
      data-rpe="${index}" data-value="${v}" aria-pressed="${s.rpe === v}"
      aria-label="RPE ${v}${v === 10 ? " – nichts mehr drin" : ""}">${v}</button>`).join("")}</div>
  </div>`;
}

/** Welche Uebung offen ist: die zuletzt angetippte, sonst die erste mit einem offenen Satz.
 *  -1 = alle zu (alles erledigt oder bewusst zugeklappt). */
function focusIndex(draft) {
  const f = draft.focus;
  if (Number.isInteger(f) && (f === -1 || draft.exercises[f])) return f;
  return draft.exercises.findIndex((e) => e.sets.some((x) => !x.done));
}

function exerciseCard(ex, index, open, draft) {
  const range = ex.reps_min === ex.reps_max ? `${ex.reps_min}` : `${ex.reps_min}–${ex.reps_max}`;
  const done = ex.sets.filter((s) => s.done).length;
  const complete = done === ex.sets.length;
  const current = open ? ex.sets.findIndex((x) => !x.done) : -1;   // der Satz, der als Nächstes dran ist
  const unitLabel = ex.unit === "koerper" ? "kg extra" : "kg";
  // Die Kopfzeile ist der Schalter. Zugeklappt reicht Name + Stand, Ziel und letztes Mal
  // braucht man nur bei der Uebung, an der man gerade ist.
  return `<div class="card exercise-card ${complete ? "is-done" : ""} ${open ? "is-open" : "is-closed"}">
    <button type="button" class="ex-head" data-focus="${index}" aria-expanded="${open}">
      <div>
        <div class="card-title">${esc(ex.name)}</div>
        ${open ? `<div class="row-meta">
          <span>${range} Whd.${ex.last?.length ? ` · zuletzt ${esc(setsSummary(ex.last, ex.unit))}` : ""}</span>
          ${ex.adjusted ? `<span class="soon-text">angepasst: ${esc(ex.adjusted)}</span>` : ""}
        </div>` : ""}
      </div>
      <span class="pill ${complete ? "ok" : ""}">${done}/${ex.sets.length}</span>
      ${icon("down", "chev")}
    </button>
    ${open ? `<div class="sets">
      <div class="set-head" aria-hidden="true"><span></span><span>Whd.</span><span>${unitLabel}</span><span></span></div>
      ${ex.sets.map((s, i) => setRow(ex, index, s, i, i === current)).join("")}</div>
    ${stepsHtml(ex, index, current)}
    <div class="btn-row">
      <button type="button" class="btn small ghost" data-add-set="${index}">${icon("plus")} Satz</button>
      <span class="grow"></span>
      <button type="button" class="btn small ghost" data-info="${index}" aria-label="Anleitung und Muskeln: ${esc(ex.name)}">${icon("dumbbell")} Anleitung</button>
      <button type="button" class="icon-btn" data-ex-menu="${index}" aria-label="Weitere Aktionen für ${esc(ex.name)}">${icon("more")}</button>
    </div>` : ""}
    ${ex.estimate && !ex.calibrated && done >= 1 && !complete ? calibRow(ex, index) : ""}
    ${complete ? (open || draft?.rpeFor === index ? rpeRow(ex, index) : rpeSummary(ex)) : ""}
  </div>`;
}

/** Kurz angeben, wie schwer die Übung war – bleibt nach dem Weiterklappen als kleine Zeile stehen. */
function rpeSummary(ex) {
  const s = lastWorkSet(ex);
  return s?.rpe ? `<p class="hint rpe-sum">Anstrengung RPE ${s.rpe}</p>` : "";
}

/** Plus/Minus für den aktuellen Satz: Wiederholungen ±1, Gewicht ±2,5 – ohne Tastatur. Nur Feld
 *  und Entwurf werden gepatcht, nichts wird neu gezeichnet. */
function stepsHtml(ex, exIndex, cur) {
  const s = ex.sets[cur];
  if (!s) return `<div class="set-steps" data-steps="${exIndex}"></div>`;
  const kgLabel = ex.unit === "koerper" ? "kg extra" : "kg";
  const btn = (field, delta, name, label) => `<button type="button" class="icon-btn tonal-icon" data-step-field="${field}"
    data-delta="${delta}" data-ex="${exIndex}" data-set="${cur}" aria-label="${name}">${icon(label)}</button>`;
  return `<div class="set-steps" data-steps="${exIndex}">
    <div class="step-group" role="group" aria-label="Wiederholungen">${btn("reps", -1, "Eine Wiederholung weniger", "minus")}<span>Whd.</span>${btn("reps", 1, "Eine Wiederholung mehr", "plus")}</div>
    <div class="step-group" role="group" aria-label="Gewicht">${btn("weight", -2.5, "2,5 Kilo weniger", "minus")}<span>${kgLabel}</span>${btn("weight", 2.5, "2,5 Kilo mehr", "plus")}</div>
  </div>`;
}

/** Seltene Aktionen einer Uebung – vorher fuenf Knoepfe unter jeder Karte. */
function openExerciseMenu(index) {
  const draft = getDraft();
  const ex = draft?.exercises[index];
  if (!ex) return;
  const last = ex.sets[ex.sets.length - 1];
  // Icon + Text als letzter Knoten: armDelete tauscht genau diesen Text aus.
  const item = (attrs, name, label, cls = "") =>
    `<button type="button" class="menu-item ${cls}" ${attrs}>${icon(name)} ${label}</button>`;
  const sheet = openSheet({
    title: esc(ex.name),
    body: `<div class="menu-list">
      ${item("data-m-info", "dumbbell", "Anleitung")}
      ${item("data-m-warmup", "plus", "Aufwärmsatz vorne einfügen")}
      ${ex.pattern ? item("data-m-swap", "swap", "Übung tauschen") : ""}
      <p class="menu-sep">Weitere</p>
      ${ex.sets.length > 1 ? item("data-m-drop-set", "x", `Letzten Satz entfernen${last.done ? " (abgehakt)" : ""}`) : ""}
      ${ex.unit === "kg" ? item("data-m-plates", "weight", "Scheiben ausrechnen") : ""}
      ${ex.pattern ? item("data-m-pain", "warn", "Schmerz bei dieser Übung") : ""}
      ${item("data-m-drop-ex", "trash", "Übung entfernen", "danger")}
    </div>`,
  });
  const after = (fn) => () => { closeSheet(); fn(); };
  // Ausführung und typische Fehler nachlesen, ohne die Einheit zu verlassen. Für die Übungen
  // des aktiven Plans liegt die Anleitung schon im Offline-Speicher (main.js, warmOfflineCache).
  sheet.querySelector("[data-m-info]").addEventListener("click", after(() => openExercise(ex.exercise_id, { session: true })));
  sheet.querySelector("[data-m-warmup]").addEventListener("click", after(() => update((d) => {
    const e = d.exercises[index];
    const first = e.sets.find((x) => !x.warmup) || e.sets[0];
    // Aufwaermen mit etwa der Haelfte, auf 2,5 kg gerundet – anpassen kann man es danach.
    const w = first?.weight != null ? Math.round(first.weight / 2 / 2.5) * 2.5 : null;
    e.sets.unshift({ reps: first?.reps ?? null, weight: w, rpe: null, done: false, warmup: true });
    e.sets.forEach((x, i) => { x.set_no = i + 1; });
  })));
  const dropSet = sheet.querySelector("[data-m-drop-set]");
  if (dropSet) {
    const drop = after(() => update((d) => { const e = d.exercises[index]; if (e.sets.length > 1) e.sets.pop(); }));
    // Ein schon abgehakter Satz ist gemachte Arbeit – nur mit zweitem Tipp entfernen.
    if (last.done) armDelete(dropSet, drop, "Letzten Satz entfernen (abgehakt)", "Wirklich entfernen?");
    else dropSet.addEventListener("click", drop);
  }
  sheet.querySelector("[data-m-plates]")?.addEventListener("click", after(() => openPlates(index)));
  sheet.querySelector("[data-m-swap]")?.addEventListener("click", after(() => openSwap(index)));
  sheet.querySelector("[data-m-pain]")?.addEventListener("click", after(() => reportPain(index)));
  armDelete(sheet.querySelector("[data-m-drop-ex]"), after(() => update((d) => {
    d.exercises.splice(index, 1);
    if (Number.isInteger(d.focus) && d.focus >= index) d.focus = null;
  })), "Übung entfernen", "Wirklich entfernen?");
}

// -------------------------------------------------------- Hantelscheiben
const PLATES = [25, 20, 15, 10, 5, 2.5, 1.25, 0.5];

function plateBreakdown(target, bar) {
  let perSide = Math.max(0, (target - bar) / 2);
  const out = [];
  for (const p of PLATES) {
    while (perSide + 1e-6 >= p) { out.push(p); perSide -= p; }
  }
  return out;
}

function openPlates(exIndex) {
  const draft = getDraft();
  const ex = draft?.exercises[exIndex];
  if (!ex) return;
  const state = { target: ex.sets.find((s) => s.weight)?.weight || 40, bar: prefs.get("barWeight") || 20 };
  const sheet = openSheet({ title: `${esc(ex.name)} · Scheiben`, body: "" });
  const body = $(".sheet-body", sheet);
  const fmt = (n) => String(n).replace(".", ",");
  body.innerHTML = `<div class="field-row">
      <label class="field">Zielgewicht (kg)<input type="number" inputmode="decimal" min="0" max="500" step="0.5" data-target value="${state.target}"></label>
      <label class="field">Stange (kg)<input type="number" inputmode="decimal" min="0" max="50" step="0.5" data-bar value="${state.bar}"></label>
    </div>
    <p class="hint">Pro Seite:</p>
    <div data-result></div>`;
  // Nur das Ergebnis aktualisieren, nicht die Eingaben neu bauen – sonst springt der
  // Cursor bei jedem Tastendruck ans Ende (bzw. verliert den Fokus).
  function paintResult() {
    const plates = plateBreakdown(state.target, state.bar);
    $("[data-result]", body).innerHTML = plates.length
      ? `<p class="big-number">${plates.map(fmt).join(" + ")}</p>`
      : `<p class="hint">Kein Zusatzgewicht nötig – nur die Stange.</p>`;
  }
  $("[data-target]", body).addEventListener("input", (ev) => { state.target = Number(ev.target.value) || 0; paintResult(); });
  $("[data-bar]", body).addEventListener("input", (ev) => {
    state.bar = Number(ev.target.value) || 0;
    prefs.set("barWeight", state.bar);
    paintResult();
  });
  paintResult();
}

function blockHint(b) {
  return b ? `Woche ${b.week}/${b.weeks} · ${b.deload ? "Entlastung" : `${b.rir} in Reserve`}` : "";
}

function renderDraft(draft) {
  const done = doneCount(draft);
  const total = totalCount(draft);
  const focus = focusIndex(draft);
  root.innerHTML = `<div class="run-head">
      <button type="button" class="icon-btn" data-minimize aria-label="Zu Heute – die Einheit läuft weiter">${icon("left")}</button>
      <div class="run-title"><b>${esc(draft.title || "Einheit")}</b>
        <span class="page-sub"><span data-elapsed>${elapsed(draft)}</span> · ${done}/${total} Sätze${draft.block ? ` · ${esc(blockHint(draft.block))}` : ""}</span></div>
      <button type="button" class="icon-btn" data-run-menu aria-label="Mehr zur Einheit">${icon("more")}</button>
    </div>
    <div class="progress lg"><i style="width:${total ? (done / total) * 100 : 0}%"></i></div>
    ${draft.exercises.map((ex, i) => exerciseCard(ex, i, i === focus, draft)).join("")}
    <div class="btn-row wide">
      <button type="button" class="btn ghost" data-add-ex>${icon("plus")} Übung</button>
    </div>
    <div class="btn-row wide sticky-actions">
      <button type="button" class="coach-fab" data-coach-session aria-label="Coach fragen" title="Coach fragen">${icon("chat")}</button>
      <button type="button" class="btn primary grow" data-finish>${icon("check")} Einheit beenden</button>
    </div>`;
  if (!timer) {
    timer = setInterval(() => {
      const el = $("[data-elapsed]", root);
      const d = getDraft();
      if (el && d) el.textContent = elapsed(d);
    }, 30000);
  }
  if (!prefs.get("hint.warmup") && draft.exercises.length) {
    prefs.set("hint.warmup", true);
    toast("Tipp: Satznummer antippen macht daraus einen Aufwärmsatz.", { duration: 6000 });
  }
}

/** Notiz, Verwerfen: selten gebraucht, deshalb hinter dem "⋯" im Kopf statt als Leiste unten. */
function openRunMenu() {
  const item = (attrs, name, label, cls = "") =>
    `<button type="button" class="menu-item ${cls}" ${attrs}>${icon(name)} ${label}</button>`;
  const sheet = openSheet({ title: "Einheit", body: `<div class="menu-list">
    ${item("data-m-notes", "edit", "Notiz zur Einheit")}
    ${item("data-m-discard", "trash", "Einheit verwerfen", "danger")}</div>` });
  const after = (fn) => () => { closeSheet(); fn(); };
  sheet.querySelector("[data-m-notes]").addEventListener("click", after(openNotes));
  armDelete(sheet.querySelector("[data-m-discard]"), after(() => {
    stopRest();
    clearDraft();
    today = null;
    toast("Einheit verworfen");
    window.dispatchEvent(new CustomEvent("outbox-changed"));
    load();
  }), "Einheit verwerfen", "Alles verwerfen?");
}

function openNotes() {
  const draft = getDraft();
  if (!draft) return;
  const sheet = openSheet({ title: "Notiz zur Einheit", body: `<label class="field">Notiz
    <textarea name="notes" rows="4" maxlength="500" data-notes placeholder="optional, z. B. Rücken hat gezwickt">${esc(draft.notes || "")}</textarea></label>`,
    footer: `<button type="button" class="btn primary" data-close>Fertig</button>` });
  // Bei jedem Tastendruck in den Entwurf, wie die Zahlen in den Sätzen
  $("[data-notes]", sheet).addEventListener("input", (ev) => {
    const d = getDraft();
    if (d) { d.notes = ev.target.value; saveDraft(d); }
  });
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
  const tw = t.today_workout;
  const nt = nextTraining(t);
  // Heute schon trainiert: das sagt die Karte, statt wieder "Einheit starten" anzubieten (wie auf Heute).
  const others = t.days.filter((d) => tw || d.id !== rec?.id);
  const hero = tw ? `<div class="card hero hero-main is-done">
      <p class="eyebrow">${icon("check")} Heute erledigt</p>
      <h3 class="hero-title">${esc(tw.day_label)}</h3>
      <p class="hint">${esc(tw.headline)}</p>
      <div class="btn-row hero-cta"><a class="btn tonal" href="#fortschritt/verlauf">Im Verlauf ansehen</a></div>
    </div>`
    : nt ? `<div class="card hero hero-main">
      <p class="eyebrow">${esc(nt.eyebrow)}</p>
      <h3 class="hero-title">${esc(nt.label)}</h3>
      <p class="meta-line">${nt.meta.map((m) => `<span>${esc(m)}</span>`).join("")}</p>
      ${lvl && lvl.key !== "voll" ? `<p class="callout">${icon("warn")} ${esc(lvl.title)} – die Vorgaben sind angepasst.</p>` : ""}
      ${heroNotesHtml(t)}
      <div class="btn-row hero-cta"><button type="button" class="btn primary" data-start="${nt.id}">${icon("play")} Einheit starten</button></div>
    </div>` : "";
  root.innerHTML = `${hero}
    ${t.streak?.week ? `<div class="card week-card" aria-label="Diese Woche">${weekStrip(t.streak.week)}</div>` : ""}
    <h3 class="section-title">${tw ? "Nochmal trainieren" : "Anderes Training"}</h3>
    <div class="list start-list">${others.map((d) => `<div class="row-item" role="button" tabindex="0" data-start="${d.id}">
        <div class="row-main"><div class="row-title">${esc(d.label)}</div>
          ${d.weekdays?.length ? `<div class="row-meta"><span>${d.weekdays.map((i) => WEEKDAYS_LONG[i]).join(", ")}</span></div>` : ""}</div>
        ${icon("right", "chev")}</div>`).join("")}
      <div class="row-item" role="button" tabindex="0" data-free>
        <span class="row-icon">${icon("plus")}</span>
        <div class="row-main"><div class="row-title">Freies Training</div>
          <div class="row-meta"><span>ohne Plan, Übungen selbst wählen</span></div></div>
        ${icon("right", "chev")}</div></div>`;
}

export async function load() {
  const draft = getDraft();
  // Vollbild nur hier setzen: load() laeuft ausschliesslich, waehrend Training/Einheit
  // wirklich sichtbar ist (subTabs ruft nur das aktive Unter-Modul). main.js raeumt beim
  // Verlassen des Tabs auf.
  document.body.classList.toggle("training-fullscreen", Boolean(draft));
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

/** Nur einen Satz aendern (Haken, Aufwaermen): Zeile, Kartenkopf und Fortschrittsbalken
 *  gezielt austauschen statt die ganze Seite neu zu bauen – sonst springt der Fokus aus
 *  einem gerade bearbeiteten Feld in einer anderen Zeile und die Seite scrollt zurueck. */
function updateSet(exIndex, setIndex, fn) {
  const draft = getDraft();
  if (!draft) return null;
  const ex = draft.exercises[exIndex];
  const s = ex.sets[setIndex];
  fn(s, ex);
  if (ex.sets.every((x) => x.done)) draft.rpeFor = exIndex;   // nur die zuletzt fertige Übung fragt nach der Anstrengung
  saveDraft(draft);

  const row = root.querySelector(`.set-row[data-ex="${exIndex}"][data-set="${setIndex}"]`);
  if (row) row.outerHTML = setRow(ex, exIndex, s, setIndex);

  const card = $$(".exercise-card", root)[exIndex];
  if (card) {
    const done = ex.sets.filter((x) => x.done).length;
    const complete = done === ex.sets.length;
    card.classList.toggle("is-done", complete);
    const pill = $(".pill", card);
    pill.textContent = `${done}/${ex.sets.length}`;
    pill.classList.toggle("ok", complete);
    // Nur die RPE-Zeile tauschen: die Kalibrierfrage ("war das Startgewicht okay?") traegt dieselbe
    // Klasse rpe-row und wurde frueher beim naechsten Haken mit weggeraeumt, bevor man sie sah.
    $(".rpe-row:not(.calib)", card)?.remove();
    $(".rpe-sum", card)?.remove();
    if (complete) {
      $(".calib", card)?.remove();
      card.insertAdjacentHTML("beforeend", rpeRow(ex, exIndex));
    } else if (ex.estimate && !ex.calibrated && done >= 1) {
      if (!$(".calib", card)) card.insertAdjacentHTML("beforeend", calibRow(ex, exIndex));
    } else $(".calib", card)?.remove();
    // Aktueller Satz und Plus/Minus ziehen mit
    const cur = ex.sets.findIndex((x) => !x.done);
    $$(".set-row", card).forEach((r, k) => r.classList.toggle("is-current", k === cur));
    const steps = $(".set-steps", card);
    if (steps) steps.outerHTML = stepsHtml(ex, exIndex, cur);
  }

  const done = doneCount(draft);
  const total = totalCount(draft);
  const sub = $(".page-sub", root);
  if (sub) sub.innerHTML = `<span data-elapsed>${elapsed(draft)}</span> · ${done}/${total} Sätze`;
  const bar = root.querySelector(".progress.lg > i");
  if (bar) bar.style.width = `${total ? (done / total) * 100 : 0}%`;
  return { draft, ex, s };
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
  const wait = openSheet({ title: "Übung hinzufügen", body: skeleton(5) });
  api("exercises").then((all) => {
    if (!wait.isConnected) return;   // inzwischen weggetippt
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
      update((d) => d.focus = d.exercises.push({
        exercise_id: ex.id, name: ex.name, unit: ex.unit, pattern: ex.pattern, kind: ex.kind,
        reps_min: 8, reps_max: 12, last: [],
        sets: [{ set_no: 1, reps: null, weight: null, rpe: null, done: false }],
      }) - 1);
    });
  }).catch((e) => { closeSheet(); toast(e.message, { error: true }); });
}

const exOption = (e) => `<div class="row-item" role="button" tabindex="0" data-pick-ex="${e.id}">
  <div class="row-main"><div class="row-title">${esc(e.name)}</div></div></div>`;

// ------------------------------------------------------------- Tauschen
/** Geraet besetzt, Muskel schon wund: passende Alternative aus dem Katalog statt die
 *  Uebung ersatzlos zu streichen. Nur zur Planungszeit gab es das bisher. */
const PAIN_AREAS = [["knee", "Knie"], ["lower_back", "Rücken"], ["shoulder", "Schulter"], ["elbow", "Ellbogen"],
  ["wrist", "Handgelenk"], ["hip", "Hüfte"]];

/** Schmerz melden: Bereich ins Profil, belastende Übungen im Plan ersetzen, dann gleich hier tauschen. */
function reportPain(exIndex) {
  const sheet = openSheet({
    title: "Wo tut es weh?",
    body: `<p class="hint">Belastende Übungen werden im Plan ersetzt und künftig nicht mehr vorgeschlagen.
        Bei anhaltenden Schmerzen bitte ärztlich abklären lassen.</p>
      <div class="chips">${PAIN_AREAS.map(([k, l]) => `<button type="button" class="chip" data-area="${k}">${l}</button>`).join("")}</div>`,
  });
  sheet.addEventListener("click", async (ev) => {
    const t = ev.target.closest("[data-area]");
    if (!t) return;
    try {
      await api("planner/pain", { method: "POST", body: { area: t.dataset.area } });
      closeSheet();
      toast("Gemerkt – belastende Übungen werden im Plan ersetzt");
      window.dispatchEvent(new CustomEvent("data-changed"));
      openSwap(exIndex);
    } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
  });
}

async function openSwap(exIndex) {
  const draft = getDraft();
  const ex = draft?.exercises[exIndex];
  if (!ex?.pattern) return;
  const used = draft.exercises.map((e) => e.exercise_id).join(",");
  let alts;
  try {
    alts = await api(`wizard/alternatives?pattern=${ex.pattern}&exclude=${used}`);
  } catch (e) {
    return toast(e.offline ? "Alternativen brauchen eine Verbindung." : e.message, { error: true });
  }
  if (!alts.length) return toast("Keine passende Alternative im Katalog gefunden.", { error: true });
  const sheet = openSheet({
    title: `${esc(ex.name)} tauschen`,
    body: `<div class="list">${alts.map((a) => `<div class="row-item" role="button" tabindex="0" data-pick-alt="${a.id}">
      <div class="row-main"><div class="row-title">${esc(a.name)}</div></div>${icon("right", "chev")}</div>`).join("")}</div>`,
  });
  sheet.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-pick-alt]");
    if (!t) return;
    const alt = alts.find((a) => a.id === Number(t.dataset.pickAlt));
    closeSheet();
    update((d) => swapInDraft(d.exercises[exIndex], alt));
    toast(`${alt.name} statt vorheriger Übung`);
  });
}

/** Uebung in der laufenden Einheit tauschen: neue Uebung, Saetze leer (das alte Gewicht passt nicht). */
function swapInDraft(e, alt) {
  Object.assign(e, { exercise_id: alt.id, name: alt.name, unit: alt.unit,
    pattern: alt.pattern, kind: alt.kind, last: [], adjusted: null, estimate: false });
  e.sets = e.sets.map((s, i) => ({ set_no: i + 1, reps: null, weight: null, rpe: null,
    warmup: s.warmup, done: false }));
}

// ------------------------------------------------------- Coach im Training
/** Was der Coach von der Einheit wissen muss – Namen und letztes Mal holt der Server selbst. */
function sessionSnapshot(d) {
  return {
    title: d.title || null, started_at: d.started_at, block: d.block, notes: d.notes || null,
    current: focusIndex(d),
    exercises: d.exercises.map((e) => ({
      exercise_id: e.exercise_id, reps_min: e.reps_min ?? null, reps_max: e.reps_max ?? null,
      sets: e.sets.map((s) => ({ reps: s.reps ?? null, weight: s.weight ?? null, rpe: s.rpe ?? null,
        done: Boolean(s.done), warmup: Boolean(s.warmup) })),
    })),
  };
}

const doneSets = (e) => e.sets.filter((s) => s.done).length;

/** Offene Arbeitssaetze nach dem Vorschlag: Anzahl, Gewicht, Wiederholungen. Abgehakte bleiben. */
function adjustSets(e, a) {
  if (a.sets != null) {
    const work = e.sets.filter((s) => !s.warmup);
    if (a.sets > work.length) {
      const tpl = work.at(-1) || {};
      for (let n = work.length; n < a.sets; n += 1) {
        e.sets.push({ reps: tpl.reps ?? null, weight: tpl.weight ?? null, rpe: null, done: false });
      }
    } else {
      let remove = work.length - a.sets;
      for (let i = e.sets.length - 1; i >= 0 && remove > 0; i -= 1) {
        if (!e.sets[i].warmup && !e.sets[i].done) { e.sets.splice(i, 1); remove -= 1; }
      }
    }
  }
  for (const s of e.sets) {
    if (s.done || s.warmup) continue;
    if (a.weight != null) s.weight = a.weight;
    if (a.reps != null) s.reps = a.reps;
  }
  e.sets.forEach((x, i) => { x.set_no = i + 1; });
}

/** Verbindung zwischen Coach-Chat und Entwurf: der Server kennt die Einheit nicht, Vorschlaege
 *  fuer "nur heute" wendet deshalb das Geraet an – mit Rueckgaengig, solange nichts abgehakt wurde. */
const sessionBridge = {
  snapshot: () => sessionSnapshot(getDraft()),
  since: () => getDraft()?.started_at,
  check(a) {
    const e = getDraft()?.exercises[a.index];
    if (!e) throw new Error("Die Einheit läuft nicht mehr.");
    if (e.exercise_id !== a.from_id) throw new Error(`An der Stelle steht nicht mehr ${a.from}.`);
  },
  apply(a, key) {
    update((d) => {
      const e = d.exercises[a.index];
      d.coachUndo = { ...(d.coachUndo || {}), [key]: { index: a.index, before: structuredClone(e), done: doneSets(e) } };
      if (a.type === "session_swap") {
        swapInDraft(e, { id: a.to_id, name: a.to, unit: a.unit, pattern: a.pattern, kind: a.kind });
        d.coachUndo[key].done = 0;
      } else adjustSets(e, a);
    });
  },
  canUndo: (key) => Boolean(getDraft()?.coachUndo?.[key]),
  checkUndo(key) {
    const u = getDraft()?.coachUndo?.[key];
    const e = u && getDraft().exercises[u.index];
    if (!e) throw new Error("Das lässt sich nicht mehr zurücknehmen.");
    if (doneSets(e) !== u.done) throw new Error("Seitdem wurden Sätze abgehakt – bitte direkt in der Übung ändern.");
  },
  undo(key) {
    update((d) => {
      const u = d.coachUndo?.[key];
      if (!u) return;
      d.exercises[u.index] = u.before;
      delete d.coachUndo[key];
    });
  },
};

function openSessionCoach() {
  if (!getDraft()) return;
  openCoach({ session: sessionBridge });
}

/** Nach der zuletzt fertigen Uebung die naechste offene aufklappen und hinscrollen. */
function advanceFocus(fromIndex) {
  const draft = getDraft();
  if (!draft) return;
  const n = draft.exercises.length;
  let next = -1;
  for (let k = 1; k <= n; k += 1) {
    const i = (fromIndex + k) % n;
    if (draft.exercises[i].sets.some((x) => !x.done)) { next = i; break; }
  }
  draft.focus = next;
  saveDraft(draft);
  renderDraft(draft);
  if (next >= 0) {
    // Die RPE-Frage der gerade fertigen Uebung soll noch zu sehen sein, direkt darunter
    // die naechste Karte – sonst scrollt die Frage weg, bevor man sie beantworten kann.
    const target = $(".rpe-row", $$(".exercise-card", root)[fromIndex]) || $$(".exercise-card", root)[next];
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
}

// --------------------------------------------------------- Beenden
/** Frueh beenden (weniger als die Haelfte geschafft) braucht einen zweiten Tipp – ein
 *  Fehltipp auf "Einheit beenden" wuerde sonst eine halbe Einheit abschliessen. */
let finishArmed = null;
function confirmFinish(btn) {
  const draft = getDraft();
  if (!draft) return;
  const done = doneCount(draft);
  const total = totalCount(draft);
  if (done === 0 || done * 2 >= total || finishArmed) {
    clearTimeout(finishArmed);
    finishArmed = null;
    return finish();
  }
  const label = btn.innerHTML;
  btn.classList.add("armed");
  btn.textContent = `Erst ${done} von ${total} – trotzdem beenden?`;
  finishArmed = setTimeout(() => {
    finishArmed = null;
    if (btn.isConnected) { btn.classList.remove("armed"); btn.innerHTML = label; }
  }, 3500);
}

async function finish() {
  const draft = getDraft();
  if (!draft) return;
  // Abgehakt, aber ohne Wiederholungen (moeglich, wenn das Feld nach dem Abhaken geleert
  // wird): nicht still verwerfen, sondern melden – sonst fehlt der Satz ohne Erklaerung.
  const invalid = draft.exercises.flatMap((ex) => ex.sets.filter((s) => s.done && !s.reps).map(() => ex));
  if (invalid.length) {
    const what = invalid.length === 1 ? "Ein abgehakter Satz hat" : `${invalid.length} abgehakte Sätze haben`;
    toast(`${what} keine Wiederholungen (${invalid[0].name}) – bitte eintragen oder den Haken entfernen.`, { error: true });
    return;
  }
  const sets = draft.exercises.flatMap((ex) => ex.sets.filter((s) => s.done)
    .map((s) => ({ exercise_id: ex.exercise_id, set_no: s.set_no, reps: s.reps, weight: s.weight,
      rpe: s.rpe, warmup: Boolean(s.warmup) })));
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
    // Erst nach dem Speichern: scheitert die Pruefung oben, laeuft die Pause weiter.
    stopRest();
    clearDraft();
    today = null;
    const review = res.data?.review;
    // Mit Auswertung sagt das Sheet schon, dass gespeichert ist – ein Toast daneben verdeckt es nur.
    if (res.queued) toast("Gespeichert – wird gesendet, sobald du wieder Empfang hast");
    else if (!review) toast(`Einheit gespeichert · ${sets.length} Sätze`);
    window.dispatchEvent(new CustomEvent("outbox-changed"));
    window.dispatchEvent(new CustomEvent("data-changed"));
    // Die Zahlen kommen mit der Antwort zurueck, der Coach-Text eine halbe Minute spaeter.
    // Ohne Netz liegt die Einheit im Ausgang – dann gibt es die Auswertung erst im Verlauf.
    if (review) {
      const sheet = openSheet({ title: "Deine Einheit", body: reviewHtml(review),
        footer: `<span class="grow"></span>
          <button type="button" class="btn primary" data-close>Passt</button>` });
      if (review.coach?.status === "pending") watchCoach(res.data.workout.id, sheet);
    }
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---------------------------------------------------------- Pausen-Timer
let restTick = null;

// Gemerkte Pausenlänge je Übung (pause.js) – je Profil, nur auf diesem Gerät
const restLenKey = () => `restlen.${getProfile()}`;
const savedRests = () => prefs.get(restLenKey(), {}) || {};
const restExercise = (r) => (Number.isInteger(r?.from) ? getDraft()?.exercises?.[r.from] : null);
let restToast = null;   // nur eine Rückmeldung, auch wenn man mehrmals tippt

function restBar() {
  let bar = document.getElementById("restBar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "restBar";
    bar.className = "rest-bar";
    bar.setAttribute("role", "timer");
    bar.innerHTML = `<span class="rest-label">Pause</span><b data-rest-time></b>
      <button type="button" class="btn small ghost" data-rest="-15" aria-label="15 Sekunden kürzer, für diese Übung merken">−15 s</button>
      <button type="button" class="btn small ghost" data-rest="15" aria-label="15 Sekunden länger, für diese Übung merken">+15 s</button>
      <button type="button" class="btn small tonal" data-rest="skip">Weiter</button>
      <small class="rest-next" data-rest-next hidden></small>`;
    bar.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-rest]");
      if (!b) return;
      if (b.dataset.rest === "skip") return stopRest();
      const r = prefs.get("rest");
      if (!r) return;
      const delta = Number(b.dataset.rest);
      r.until += delta * 1000;
      prefs.set("rest", r);
      // Die Änderung gilt auch für die nächsten Pausen dieser Übung
      const ex = restExercise(r);
      if (ex) {
        const { saved, seconds } = rememberRest(savedRests(), ex, delta);
        prefs.set(restLenKey(), saved);
        restToast?.remove();
        restToast = toast(restNote(ex, seconds));
      }
      tick();
    });
    document.body.appendChild(bar);
  }
  return bar;
}

// Ein einziger AudioContext, wiederverwendet statt pro Pause neu erzeugt – und angelegt
// direkt beim Start der Pause (also innerhalb des Klicks, der sie ausloest). iOS Safari
// laesst einen AudioContext nur "running" starten, wenn seine Erzeugung auf eine echte
// Nutzer-Geste zurueckgeht; einer, der erst im setInterval-Callback entsteht, bleibt stumm.
let audioCtx = null;

function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch { /* kein Ton moeglich */ }
}

function beep() {
  if (!audioCtx) return;
  try {
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.frequency.value = 880;
    g.gain.setValueAtTime(0.2, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.5);
    o.connect(g).connect(audioCtx.destination);
    o.start();
    o.stop(audioCtx.currentTime + 0.5);
  } catch { /* kein Ton moeglich */ }
}

/** navigator.vibrate ist unter iOS Safari ein No-op, und ein im Hintergrund gedrosselter
 *  Timer meldet das Pausenende sonst gar nicht – eine Notification ist der einzige Weg,
 *  der auch bei gesperrtem Bildschirm ankommt. Es wird nie um Erlaubnis gebeten (das waere
 *  ein eigener Eingriff); ist sie schon erteilt, wird sie genutzt. */
function notifyRestOver() {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try { new Notification("Pause vorbei", { body: "Weiter geht's.", tag: "rest-over" }); } catch { /* egal */ }
}

function tick() {
  const r = prefs.get("rest");
  if (!r) return stopRest();
  const left = Math.round((r.until - Date.now()) / 1000);
  const bar = restBar();
  if (left <= 0) {
    navigator.vibrate?.([200, 100, 200]);
    beep();
    if (document.hidden) notifyRestOver();
    return stopRest();
  }
  bar.querySelector("[data-rest-time]").textContent =
    `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  bar.hidden = false;
  showNext(bar, r.from);
  document.body.classList.add("resting");
}

/** Unter der Zeit steht, was nach der Pause kommt – Uebung, Satz, Ziel. So muss man in der
 *  Pause nicht scrollen, um die naechste Karte zu finden. Jedes Mal aus dem Entwurf gelesen,
 *  damit eine in der Pause geaenderte Zahl gleich stimmt. */
function showNext(bar, from) {
  const el = bar.querySelector("[data-rest-next]");
  const label = nextLabel(nextSet(getDraft(), from));
  const text = label ? `Als Nächstes: ${label}` : "";
  if (el.textContent !== text) {
    el.textContent = text;
    el.hidden = !text;
  }
  // Die Leiste wird mit der Zeile hoeher – Aktionsleiste und Innenabstand ruecken mit (--rest-h)
  if (!bar.hidden) document.body.style.setProperty("--rest-h", `${bar.offsetHeight}px`);
}

function startRest(seconds, from = null) {
  unlockAudio();   // muss synchron in der Nutzer-Geste passieren, die die Pause ausloest
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
  prefs.set("rest", { until: Date.now() + seconds * 1000, from });
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
  document.body.classList.remove("resting");
  document.body.style.removeProperty("--rest-h");
}

// Laeuft nach einem Neuladen weiter
if (prefs.get("rest")) { restTick = setInterval(tick, 500); }
// Im Hintergrund wird setInterval gedrosselt – beim Zurueckkommen sofort nachrechnen,
// statt auf den naechsten (verspaeteten) Tick zu warten.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && prefs.get("rest")) tick();
  syncWakeLock();
});

// ------------------------------------------------------ Bildschirm wach
// Waehrend einer laufenden Einheit bleibt der Bildschirm an – sonst sperrt das Handy in
// der Pause und man entsperrt vor jedem Haken neu. Gekoppelt an die Klasse
// training-fullscreen: die setzt nur load() bei offenem Entwurf, beim Beenden/Verwerfen
// und beim Verlassen des Tabs (main.js) faellt sie weg. Im Hintergrund gibt der Browser
// die Sperre selbst frei; beim Zurueckkommen wird sie neu angefordert. Ohne Wake-Lock-API
// oder bei Ablehnung (z. B. Energiesparmodus) bleibt alles wie bisher.
let wakeLock = null;   // WakeLockSentinel oder das noch laufende request()-Promise

async function syncWakeLock() {
  if (!navigator.wakeLock) return;
  const want = document.body.classList.contains("training-fullscreen") && !document.hidden;
  if (want && !wakeLock) {
    const req = navigator.wakeLock.request("screen");
    wakeLock = req;
    try {
      const lock = await req;
      // Inzwischen beendet oder Tab gewechselt: gleich wieder freigeben.
      if (wakeLock !== req) { lock.release().catch(() => {}); return; }
      wakeLock = lock;
      lock.addEventListener("release", () => { if (wakeLock === lock) wakeLock = null; });
    } catch {
      if (wakeLock === req) wakeLock = null;
    }
  } else if (!want && wakeLock) {
    const lock = wakeLock;
    wakeLock = null;
    lock.release?.().catch(() => {});
  }
}

new MutationObserver(syncWakeLock).observe(document.body, { attributes: true, attributeFilter: ["class"] });

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

    const warmup = t("[data-warmup]");
    if (warmup) {
      const row = warmup.closest(".set-row");
      const [i, j] = [Number(row.dataset.ex), Number(row.dataset.set)];
      updateSet(i, j, (s) => { s.warmup = !s.warmup; });
      return;
    }
    const toggle = t("[data-toggle]");
    if (toggle) {
      const row = toggle.closest(".set-row");
      const [i, j] = [Number(row.dataset.ex), Number(row.dataset.set)];
      // Ein Satz gilt nur mit Wiederholungen als gemacht. Den Haken entfernen geht immer –
      // sonst haengt ein Satz fest, dessen Feld nach dem Abhaken geleert wurde.
      const reps = row.querySelector('[data-field="reps"]').value;
      if (!reps && !row.classList.contains("is-done")) {
        toast("Trag zuerst die Wiederholungen ein.", { error: true });
        row.querySelector('[data-field="reps"]').focus();
        return;
      }
      const res = updateSet(i, j, (s) => { s.done = !s.done; });
      // Nach dem letzten Satz der Einheit keine Pause mehr – als Naechstes kommt "Beenden".
      const d = getDraft();
      const allDone = d && doneCount(d) === totalCount(d);
      if (res?.s.done && !allDone) {
        startRest(restFor(res.ex, savedRests()), i);
      } else if (allDone) {
        stopRest();   // die Pause vom vorletzten Satz lief sonst weiter
      }
      // Letzter Satz einer Uebung: gleich die naechste offene aufklappen.
      if (res?.s.done && res.ex.sets.every((x) => x.done)) advanceFocus(i);
      return;
    }
    if (t("[data-minimize]")) { location.hash = "heute"; return; }   // Einheit läuft weiter, Entwurf bleibt
    if (t("[data-run-menu]")) return openRunMenu();
    const info = t("[data-info]");
    if (info) {
      const e = getDraft()?.exercises[Number(info.dataset.info)];
      if (e) openExercise(e.exercise_id, { session: true });
      return;
    }
    const stepBtn = t("[data-step-field]");
    if (stepBtn) {
      const [i, j] = [Number(stepBtn.dataset.ex), Number(stepBtn.dataset.set)];
      const field = stepBtn.dataset.stepField;
      const input = root.querySelector(`.set-row[data-ex="${i}"][data-set="${j}"] [data-field="${field}"]`);
      if (!input) return;
      const old = input.value === "" ? null : Number(input.value);
      const next = Math.max(0, Math.round(((old || 0) + Number(stepBtn.dataset.delta)) * 100) / 100);
      input.value = next;
      setValue(i, j, field, next);
      fillFollowing(i, j, field, old, next);
      return;
    }
    const focus = t("[data-focus]");
    if (focus) {
      const i = Number(focus.dataset.focus);
      update((d) => { d.focus = focusIndex(d) === i ? -1 : i; });
      // Die aufgeklappte Karte immer ins Bild holen – sonst springt die Seite und man sucht sie
      $$(".exercise-card", root)[i]?.scrollIntoView({ block: "start", behavior: "auto" });
      return;
    }
    const calib = t("[data-calib]");
    if (calib) {
      const i = Number(calib.dataset.calib);
      const factor = { easy: 1.1, ok: 1, hard: 0.9 }[calib.dataset.level];
      return update((d) => {
        const ex = d.exercises[i];
        const last = [...ex.sets].reverse().find((x) => x.done && !x.warmup);
        if (last) last.rpe = { easy: 6, ok: 8, hard: 10 }[calib.dataset.level];
        // 0 kg (Körpergewicht) bleibt 0; „leicht“/„zu schwer“ ändert mindestens um einen Schritt
        ex.sets.filter((x) => !x.done && x.weight > 0).forEach((x) => {
          const step = x.weight < 20 ? 1 : 2.5;
          let w = Math.round((x.weight * factor) / step) * step;
          if (factor > 1) w = Math.max(w, x.weight + step);
          if (factor < 1) w = Math.min(w, x.weight - step);
          x.weight = Math.max(step, w);
        });
        ex.calibrated = true;
      });
    }
    const rpe = t("[data-rpe]");
    if (rpe) {
      const i = Number(rpe.dataset.rpe);
      const v = Number(rpe.dataset.value);
      const d = getDraft();
      const s = d && lastWorkSet(d.exercises[i]);
      if (!s) return;
      s.rpe = s.rpe === v ? null : v;   // nochmal tippen nimmt die Angabe zurueck
      saveDraft(d);
      rpe.closest(".rpe-row").outerHTML = rpeRow(d.exercises[i], i);
      return;
    }
    const menu = t("[data-ex-menu]");
    if (menu) return openExerciseMenu(Number(menu.dataset.exMenu));
    const add = t("[data-add-set]");
    if (add) {
      return update((d) => {
        const ex = d.exercises[Number(add.dataset.addSet)];
        const last = ex.sets[ex.sets.length - 1];
        ex.sets.push({ set_no: ex.sets.length + 1, reps: last?.reps ?? null,
          weight: last?.weight ?? null, rpe: null, done: false });
      });
    }
    // data-cancel ist zweistufig (armDelete) und wird einmal pro renderDraft() registriert,
    // nicht hier im delegierten Handler; Entfernen, Scheiben, Tauschen stecken im Menue.
    if (t("[data-add-ex]")) return addExercise();
    if (t("[data-coach-session]")) return openSessionCoach();
    const fin = t("[data-finish]");
    if (fin) return confirmFinish(fin);
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

  // Gewicht/Wiederholungen an die folgenden Saetze weitergeben – erst beim Verlassen des
  // Felds (change), nicht pro Tastendruck: sonst wuerde schon das Leeren zum Neu-Tippen
  // die Saetze darunter mitreissen. Uebernommen wird nur, was noch leer ist oder noch
  // denselben Wert hatte wie dieses Feld vorher (also nicht selbst angepasst wurde).
  let before = null;
  el.addEventListener("focusin", (ev) => {
    const field = ev.target.closest('[data-field="weight"], [data-field="reps"]');
    before = field ? { field, value: field.value === "" ? null : Number(field.value) } : null;
  });
  el.addEventListener("change", (ev) => {
    const field = ev.target.closest('[data-field="weight"], [data-field="reps"]');
    if (!field || before?.field !== field) return;
    const value = field.value === "" ? null : Number(field.value);
    if (value === null || Number.isNaN(value) || value === before.value) return;
    const row = field.closest(".set-row");
    fillFollowing(Number(row.dataset.ex), Number(row.dataset.set), field.dataset.field, before.value, value);
    before = { field, value };
  });
}

function fillFollowing(exIndex, setIndex, key, old, value) {
  const draft = getDraft();
  if (!draft) return;
  const sets = draft.exercises[exIndex].sets;
  const src = sets[setIndex];
  for (let j = setIndex + 1; j < sets.length; j += 1) {
    const s = sets[j];
    if (s.done || Boolean(s.warmup) !== Boolean(src.warmup)) continue;
    if (s[key] !== null && s[key] !== old) continue;
    s[key] = value;
    // Nur das Feld nachziehen, nicht neu zeichnen – die Tastatur bleibt offen.
    const input = root.querySelector(`.set-row[data-ex="${exIndex}"][data-set="${j}"] [data-field="${key}"]`);
    if (input) input.value = value;
  }
  saveDraft(draft);
}
