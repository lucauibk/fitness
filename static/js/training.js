/** Laufende Einheit. Alles passiert zuerst im Geraetespeicher – ohne Netz, ohne Warten.
 *  Erst beim Beenden geht die komplette Einheit in einem Stueck an den Pi (bzw. in den
 *  Ausgang, wenn gerade keine Verbindung da ist). */
import { $, $$, api, armDelete, closeSheet, empty, esc, feedbackHtml, getProfile,
  icon, openSheet, prefs, setsSummary, skeleton, toast } from "./util.js";
import * as outbox from "./outbox.js";

let root;
let today = null;     // /api/today fuer den Startbildschirm
let timer = null;

// ------------------------------------------------------------- Entwurf
// Der Entwurf liegt geraeteweit im prefs-Speicher; ohne den Profil-Abgleich wuerde bei
// einem Profilwechsel mitten in einer Einheit der Entwurf des anderen Profils erscheinen.
export function getDraft() {
  const d = prefs.get("draft");
  return d && d.profile_id === getProfile() ? d : null;
}
export const hasDraft = () => Boolean(getDraft());
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
      pattern: e.pattern,
      rest: e.rest_seconds || null,
      adjusted: e.adjusted || null,
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
function setRow(ex, exIndex, s, i) {
  const unitLabel = ex.unit === "koerper" ? "kg extra" : "kg";
  // Das letzte Mal direkt an der Zeile statt nur einmal in der Kartenkopfzeile – das ist
  // der Unterschied zwischen Zahlen eintippen und sich gezielt steigern.
  const last = ex.last?.[i];
  return `<div class="set-row ${s.done ? "is-done" : ""}" data-ex="${exIndex}" data-set="${i}">
    <button type="button" class="set-no ${s.warmup ? "is-warmup" : ""}" data-warmup
            aria-label="Satz ${s.set_no}${s.warmup ? ", Aufwärmsatz – antippen für normalen Satz" : " – antippen für Aufwärmsatz"}"
            aria-pressed="${Boolean(s.warmup)}">${s.warmup ? "W" : s.set_no}</button>
    <label class="set-field"><span>Whd.${last?.reps != null ? ` · zuletzt ${last.reps}` : ""}</span>
      <input type="number" inputmode="numeric" min="0" max="1000" step="1"
             value="${s.reps ?? ""}" data-field="reps" aria-label="Wiederholungen Satz ${s.set_no}">
    </label>
    <label class="set-field"><span>${unitLabel}${last?.weight != null ? ` · zuletzt ${String(last.weight).replace(".", ",")}` : ""}</span>
      <input type="number" inputmode="decimal" min="0" max="1000" step="0.5"
             value="${s.weight ?? ""}" data-field="weight" aria-label="Gewicht Satz ${s.set_no}">
    </label>
    <label class="set-field set-field-rpe"><span>RPE</span>
      <input type="number" inputmode="decimal" min="0" max="10" step="0.5"
             value="${s.rpe ?? ""}" data-field="rpe" aria-label="Anstrengung (RPE) Satz ${s.set_no}, 0 bis 10">
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
      ${ex.unit === "kg" ? `<button type="button" class="btn small ghost" data-plates="${index}">${icon("weight")} Scheiben</button>` : ""}
      ${ex.pattern ? `<button type="button" class="btn small ghost" data-swap="${index}">${icon("swap")} Tauschen</button>` : ""}
      <span class="grow"></span>
      <button type="button" class="btn small ghost danger" data-drop-ex="${index}">${icon("trash")} Entfernen</button>
    </div>
  </div>`;
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
  // Einmal pro Aufbau registrieren, nicht im delegierten Klick-Handler – sonst haengt
  // jeder Tipp einen weiteren Listener an denselben (neu gebauten) Knoten.
  armDelete($("[data-cancel]", root), () => {
    stopRest();
    clearDraft();
    today = null;
    toast("Einheit verworfen");
    window.dispatchEvent(new CustomEvent("outbox-changed"));
    load();
  }, "Verwerfen", "Alles verwerfen?");
  $$("[data-drop-ex]", root).forEach((btn) => {
    armDelete(btn, () => update((d) => {
      d.exercises.splice(Number(btn.dataset.dropEx), 1);
    }), "Entfernen", "Entfernen?");
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
  saveDraft(draft);

  const row = root.querySelector(`.set-row[data-ex="${exIndex}"][data-set="${setIndex}"]`);
  if (row) row.outerHTML = setRow(ex, exIndex, s, setIndex);

  const card = $$(".exercise-card", root)[exIndex];
  if (card) {
    const done = ex.sets.filter((x) => x.done).length;
    card.classList.toggle("is-done", done === ex.sets.length);
    const pill = $(".pill", card);
    pill.textContent = `${done}/${ex.sets.length}`;
    pill.classList.toggle("ok", done === ex.sets.length);
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
        exercise_id: ex.id, name: ex.name, unit: ex.unit, pattern: ex.pattern, kind: ex.kind,
        reps_min: 8, reps_max: 12, last: [],
        sets: [{ set_no: 1, reps: null, weight: null, rpe: null, done: false }],
      }));
    });
  }).catch((e) => toast(e.message, { error: true }));
}

const exOption = (e) => `<div class="row-item" role="button" tabindex="0" data-pick-ex="${e.id}">
  <div class="row-main"><div class="row-title">${esc(e.name)}</div></div></div>`;

// ------------------------------------------------------------- Tauschen
/** Geraet besetzt, Muskel schon wund: passende Alternative aus dem Katalog statt die
 *  Uebung ersatzlos zu streichen. Nur zur Planungszeit gab es das bisher. */
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
    update((d) => {
      const e = d.exercises[exIndex];
      Object.assign(e, { exercise_id: alt.id, name: alt.name, unit: alt.unit,
        pattern: alt.pattern, kind: alt.kind, last: [], adjusted: null });
      e.sets = e.sets.map((s, i) => ({ set_no: i + 1, reps: null, weight: null, rpe: null,
        warmup: s.warmup, done: false }));
    });
    toast(`${alt.name} statt vorheriger Übung`);
  });
}

// --------------------------------------------------------- Beenden
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
    clearDraft();
    today = null;
    toast(res.queued ? "Gespeichert – wird gesendet, sobald du wieder Empfang hast"
      : `Einheit gespeichert · ${sets.length} Sätze`);
    window.dispatchEvent(new CustomEvent("outbox-changed"));
    window.dispatchEvent(new CustomEvent("data-changed"));
    // Das Urteil kommt mit der Antwort zurueck. Ohne Netz liegt die Einheit im Ausgang –
    // dann gibt es die Bewertung erst nach dem Nachsenden, im Verlauf.
    const fb = res.data?.feedback;
    if (fb) {
      openSheet({ title: "Einheit bewertet", body: feedbackHtml(fb),
        footer: `<span class="grow"></span>
          <button type="button" class="btn primary" data-close>Passt</button>` });
    }
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
}

function startRest(seconds) {
  unlockAudio();   // muss synchron in der Nutzer-Geste passieren, die die Pause ausloest
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
  prefs.set("rest", { until: Date.now() + seconds * 1000 });
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
// Im Hintergrund wird setInterval gedrosselt – beim Zurueckkommen sofort nachrechnen,
// statt auf den naechsten (verspaeteten) Tick zu warten.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && prefs.get("rest")) tick();
});

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
      // Ein Satz gilt nur mit Wiederholungen als gemacht.
      const reps = row.querySelector('[data-field="reps"]').value;
      if (!reps) {
        toast("Trag zuerst die Wiederholungen ein.", { error: true });
        row.querySelector('[data-field="reps"]').focus();
        return;
      }
      const res = updateSet(i, j, (s) => { s.done = !s.done; });
      if (res?.s.done) {
        const rest = res.ex.rest || (res.ex.kind === "compound" ? REST.compound : REST.isolation);
        startRest(rest);
      }
      return;
    }
    const add = t("[data-add-set]");
    if (add) {
      return update((d) => {
        const ex = d.exercises[Number(add.dataset.addSet)];
        const last = ex.sets[ex.sets.length - 1];
        ex.sets.push({ set_no: ex.sets.length + 1, reps: last?.reps ?? null,
          weight: last?.weight ?? null, rpe: null, done: false });
      });
    }
    const drop = t("[data-drop-set]");
    if (drop) {
      return update((d) => {
        const ex = d.exercises[Number(drop.dataset.dropSet)];
        if (ex.sets.length > 1) ex.sets.pop();
      });
    }
    // data-drop-ex und data-cancel sind zweistufig (armDelete) und werden einmal pro
    // renderDraft() registriert, nicht hier im delegierten Handler.
    if (t("[data-add-ex]")) return addExercise();
    if (t("[data-finish]")) { stopRest(); return finish(); }
    const plates = t("[data-plates]");
    if (plates) return openPlates(Number(plates.dataset.plates));
    const swap = t("[data-swap]");
    if (swap) return openSwap(Number(swap.dataset.swap));
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
