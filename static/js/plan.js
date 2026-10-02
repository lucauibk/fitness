/** Plan: Splits anlegen und wechseln, Trainingstage und Übungen samt Wiederholungs-Bereich. */
import { openWizard } from "./wizard.js";
import { $, actionMenu, api, armDelete, busy, closeSheet, deleteLater, empty, esc, formData, formError, icon,
  getProfile, isPendingDelete, openSheet, prefs, skeleton, submitOnce, toast, WEEKDAYS,
  WEEKDAYS_LONG } from "./util.js";

let root;
let splits = null;
let detail = null;      // der gerade geöffnete Split
let openSplitId = null;
let sortingDay = null;   // Pfeile nur in dem Tag zeigen, den man gerade sortiert
let exercises = null;
let blockInfo = null;   // Woche X von Y, Soll/Ist-Volumen, Ereignisse
let confirmActivate = null;   // { id, timer } – zweiter Tipp auf "Wechseln" bestaetigt
// Aufgeklappte Trainingstage – eingeklappt ist der Plan eine Uebersicht statt einer langen Liste.
const openDays = new Set(prefs.get("plan.openDays") || []);
const saveOpenDays = () => prefs.set("plan.openDays", [...openDays]);
const PATTERNS = {
  h_push: "Drücken nach vorn (Bankdrücken …)", v_push: "Drücken über Kopf",
  chest_iso: "Brust isoliert (Butterfly …)", h_pull: "Rudern", v_pull: "Ziehen von oben (Latzug …)",
  rear_delt: "Hintere Schulter", lateral: "Seitheben", squat: "Kniebeuge / Beinpresse",
  hinge: "Hüftbeuge (Kreuzheben …)", lunge: "Ausfallschritt", leg_iso_front: "Beinstrecker",
  leg_iso_back: "Beinbeuger", glutes: "Po", calves: "Waden", biceps: "Bizeps",
  triceps: "Trizeps", core: "Rumpf",
};

const changed = () => window.dispatchEvent(new CustomEvent("data-changed"));
const range = (lo, hi) => (lo === hi ? `${lo}` : `${lo}–${hi}`);
/** [2, 4] -> "Mi · Fr"; ein einzelner Tag ausgeschrieben. */
const daysLabel = (days) => (!days?.length ? "kein fester Tag"
  : days.length === 1 ? WEEKDAYS_LONG[days[0]] : days.map((d) => WEEKDAYS[d]).join(" · "));

// -------------------------------------------------------------- Anzeige
function exerciseRow(item, sorting = false) {
  const meta = `<div class="row-main">
      <div class="row-title">${esc(item.name)}</div>
      <div class="row-meta">
        <span>${item.target_sets} × ${range(item.target_reps_min, item.target_reps_max)} Whd.</span>
        ${item.target_weight != null ? `<span>${String(item.target_weight).replace(".", ",")} kg</span>` : ""}
        ${item.rest_seconds ? `<span>Pause ${item.rest_seconds} s</span>` : ""}
        ${item.notes ? `<span>${esc(item.notes)}</span>` : ""}
      </div>
    </div>`;
  // Beim Sortieren ist die Zeile kein Knopf – sonst steckten die Pfeile in einem Knopf,
  // und Tastatur/Screenreader kaemen nicht an sie heran.
  if (!sorting) {
    return `<div class="row-item" role="button" tabindex="0" data-edit-item="${item.id}">${meta}</div>`;
  }
  return `<div class="row-item">${meta}
    <span class="order-btns">
      <button type="button" class="icon-btn" data-move="${item.id}" data-dir="-1" aria-label="${esc(item.name)} nach oben">${icon("up")}</button>
      <button type="button" class="icon-btn" data-move="${item.id}" data-dir="1" aria-label="${esc(item.name)} nach unten">${icon("down")}</button>
    </span>
  </div>`;
}

function dayCard(day) {
  const open = openDays.has(day.id) || sortingDay === day.id;
  return `<details class="card day-details ${sortingDay === day.id ? "sorting" : ""}" data-day="${day.id}" ${open ? "open" : ""}>
    <summary class="ex-head">
      <div>
        <div class="card-title">${esc(day.label)}</div>
        <div class="row-meta"><span>${daysLabel(day.weekdays)}</span>
          <span>${day.exercises.length} Übungen</span></div>
      </div>
      ${icon("down", "chev")}
    </summary>
    <div class="list flush">${day.exercises.filter((e) => !isPendingDelete(`split-exercises/${e.id}`))
      .map((e) => exerciseRow(e, sortingDay === day.id)).join("")
      || `<p class="hint pad">Noch keine Übungen.</p>`}</div>
    <div class="btn-row">
      <button type="button" class="btn small ghost" data-add-item="${day.id}">${icon("plus")} Übung</button>
      ${day.exercises.length > 1 ? `<button type="button" class="btn small ghost" data-sort="${day.id}">${
        sortingDay === day.id ? "Fertig" : "Reihenfolge"}</button>` : ""}
      <span class="grow"></span>
      <button type="button" class="btn small ghost" data-edit-day="${day.id}">${icon("edit")} Tag</button>
    </div>
  </details>`;
}

/** Block-Kopf: wo im Block stehe ich, was ist diese Woche geplant, was hat sich geaendert. */
function blockCard() {
  const b = blockInfo?.block;
  if (!b) return "";
  const maxSets = Math.max(1, ...blockInfo.weeks.map((w) => w.sets));
  const vol = blockInfo.volume;
  const top = Math.max(10, ...vol.map((v) => Math.max(v.planned, v.done)));
  return `<div class="card block-card">
    <p class="eyebrow">${b.deload ? "Entlastungswoche" : "Trainingsblock"}</p>
    <h3 class="card-title">Woche ${b.week} von ${b.weeks}${b.deload ? " · leichter, Kraft erholt sich" : ` · ${b.rir} Wdh. in Reserve`}</h3>
    <div class="weekbars" role="img" aria-label="Sätze pro Woche im Block">${blockInfo.weeks.map((w) => `
      <span class="weekbar ${w.week === b.week ? "now" : ""}"><i style="height:${Math.max(8, (w.sets / maxSets) * 100)}%"></i>
        <b>${w.sets}</b><small>${w.week === b.weeks ? "Deload" : `W${w.week}`}</small></span>`).join("")}</div>
    <details class="field-more"><summary>Diese Woche je Muskel</summary>
      <div class="bars">${vol.map((v) => `<div class="bar-row"><span class="bar-label">${esc(v.label)}</span>
        <span class="bar"><i style="width:${(v.done / top) * 100}%"></i></span>
        <span class="bar-value">${v.done}/${v.planned}</span></div>`).join("")}</div></details>
    ${blockInfo.events.length ? `<details class="field-more"><summary>Was sich geändert hat</summary>
      <div class="list flush">${blockInfo.events.map((e) => `<div class="row-item"><div class="row-main">
        <div class="row-title">${esc(e.text)}</div><div class="row-meta"><span>${esc(e.date)}</span></div></div></div>`).join("")}</div></details>` : ""}
  </div>`;
}

function render() {
  if (!splits?.length) {
    root.innerHTML = `${empty("Noch kein Split", "Ein Split ist dein Trainingsplan – zum Beispiel Push, "
        + "Pull und Beine. Der Assistent erstellt dir einen passenden.",
        { label: "Plan mit Assistent erstellen", attrs: "data-wizard" })}
      <div class="btn-row"><button type="button" class="btn ghost" data-add-split>${icon("plus")} Selbst anlegen</button></div>`;
    return;
  }
  const visible = splits.filter((s) => !isPendingDelete(`splits/${s.id}`));
  const open = detail && visible.some((s) => s.id === detail.id) ? detail : null;

  root.innerHTML = `<div class="btn-row"><button type="button" class="btn primary" data-wizard>${icon("spark")} Neuen Plan erstellen</button></div>
    ${blockCard()}<div class="list">${visible.map((s) => `
      <div class="row-item">
        <button type="button" class="row-main row-btn" data-open-split="${s.id}" aria-expanded="${open?.id === s.id}">
          <span class="row-title">${esc(s.name)}</span>
          <span class="row-meta"><span>${s.days.length} Tage</span>
            <span>${s.days.map((d) => esc(d.label)).join(" · ") || "noch leer"}</span></span>
        </button>
        ${s.active ? `<span class="pill ok">aktiv</span>` : `<button type="button" class="btn small tonal" data-activate="${s.id}">Wechseln</button>`}
        <button type="button" class="icon-btn" data-delete-split="${s.id}" aria-label="„${esc(s.name)}“ löschen">${icon("trash")}</button>
      </div>`).join("")}</div>
    ${open ? `<div class="plan-head">
        <h3 class="group-title">${esc(open.name)}</h3>
        <button type="button" class="btn small ghost" data-plan-menu aria-label="Aktionen für den Plan">⋯ Plan</button>
      </div>
      ${open.days.filter((d) => !isPendingDelete(`split-days/${d.id}`)).map(dayCard).join("")}
      <div class="btn-row">
        <button type="button" class="btn ghost" data-add-day="${open.id}">${icon("plus")} Trainingstag</button>
      </div>` : `<p class="hint pad">Tippe auf einen Split, um seine Tage und Übungen zu sehen.</p>
      <div class="btn-row"><button type="button" class="btn small ghost" data-plan-menu>⋯ Plan</button></div>`}`;
  if (refocus) { root.querySelector(refocus)?.focus({ preventScroll: true }); refocus = null; }
}

/** Zustand vergessen (Profilwechsel): sonst fragt load() den Split des vorigen Profils ab. */
export function reset() {
  splits = null; detail = null; openSplitId = null; blockInfo = null; sortingDay = null; exercises = null;
}

// Neu erstellter Plan: gleich diesen zeigen statt des vorher geöffneten.
window.addEventListener("plan-created", (ev) => {
  openSplitId = ev.detail?.splitId ?? null;
  splits = null;
  detail = null;
});

export async function load() {
  if (!splits) root.innerHTML = skeleton(4);
  splits = await api("splits");
  blockInfo = await api("planner/block").catch(() => null);
  // Wunsch: der zuletzt geöffnete Plan, sonst der aktive, sonst irgendeiner – fällt einer weg (gelöscht,
  // anderes Profil), geht es mit dem nächsten weiter statt mit leerer Ansicht.
  const order = [openSplitId, splits.find((s) => s.active)?.id, splits[0]?.id]
    .filter((id, i, all) => id && all.indexOf(id) === i && splits.some((s) => s.id === id));
  detail = null;
  openSplitId = null;
  for (const id of order) {
    try { detail = await api(`splits/${id}`); openSplitId = id; break; } catch { /* nächster Kandidat */ }
  }
  render();
}

// ------------------------------------------------------------ Formulare
const footer = (formId, deletable) => `
  ${deletable ? `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>` : ""}
  <span class="grow"></span>
  <button type="button" class="btn ghost" data-close>Abbrechen</button>
  <button type="submit" class="btn primary" form="${formId}">Speichern</button>`;

function editSplit(split) {
  const isNew = !split;
  const sheet = openSheet({
    title: isNew ? "Neuer Split" : "Split",
    body: `<form id="splitForm" class="form">
      <label class="field">Name<input name="name" maxlength="80" required autofocus
        value="${esc(split?.name || "")}" placeholder="z. B. Push / Pull / Beine"></label>
    </form>`,
    footer: footer("splitForm", !isNew),
  });
  const form = $("#splitForm", sheet);
  submitOnce(form, async () => {
    try {
      const body = formData(form);
      if (isNew) {
        const created = await api("splits", { method: "POST", body });
        openSplitId = created.id;
      } else {
        await api(`splits/${split.id}`, { method: "PATCH", body });
      }
      closeSheet();
      toast("Split gespeichert");
      splits = null;
      changed();
    } catch (e) { formError(form, e); }
  });
  if (!isNew) {
    armDelete($("[data-delete]", sheet), () => {
      closeSheet();
      removePlan(split);
    }, "Löschen", "Split mit allen Tagen löschen? Absolvierte Einheiten bleiben erhalten.");
  }
}

/** Plan löschen (mit Rückgängig); der aktive Plan wird durch den nächsten ersetzt. */
function removePlan(split) {
  if (openSplitId === split.id) { openSplitId = null; detail = null; }
  deleteLater(`splits/${split.id}`, `„${split.name}“ gelöscht`, {
    onDone: () => { splits = null; blockInfo = null; changed(); },
  });
  splits = null;
  render();
}

/** Zweiter Tipp bestätigt – ein versehentliches Löschen soll nicht gehen. */
let confirmDelete = null;
function askDeletePlan(split, btn) {
  if (confirmDelete?.id === split.id) {
    clearTimeout(confirmDelete.timer);
    confirmDelete = null;
    return removePlan(split);
  }
  clearTimeout(confirmDelete?.timer);
  btn.classList.add("armed");
  btn.setAttribute("aria-label", `„${split.name}“ wirklich löschen – nochmal tippen`);
  confirmDelete = { id: split.id, timer: setTimeout(() => {
    confirmDelete = null;
    btn.classList.remove("armed");
    btn.setAttribute("aria-label", `„${split.name}“ löschen`);
  }, 3500) };
  toast(`Nochmal tippen, um „${split.name}“ zu löschen`);
}

function editDay(day, splitId) {
  const isNew = !day;
  const sheet = openSheet({
    title: isNew ? "Neuer Trainingstag" : "Trainingstag",
    body: `<form id="dayForm" class="form">
      <label class="field">Bezeichnung<input name="label" maxlength="40" required autofocus
        value="${esc(day?.label || "")}" placeholder="z. B. Push"></label>
      <fieldset class="field"><legend>Wochentage</legend>
        <div class="chips">
          ${WEEKDAYS.map((w, i) => `<label class="chip"><input type="checkbox" data-weekday value="${i}"
            ${day?.weekdays?.includes(i) ? "checked" : ""}>${w}</label>`).join("")}
        </div>
      </fieldset>
      <p class="hint">Mehrere möglich, z. B. Mi und Fr. Ohne Auswahl schlägt „Heute“ einfach
        den nächsten Teil der Reihe vor.</p>
    </form>`,
    footer: footer("dayForm", !isNew),
  });
  const form = $("#dayForm", sheet);
  submitOnce(form, async () => {
    try {
      const data = formData(form);
      // Checkboxen mit gleichem Zweck sammeln – formData() kennt nur Einzelwerte
      const weekdays = [...form.querySelectorAll("[data-weekday]:checked")].map((el) => Number(el.value));
      const body = { label: data.label, weekdays };
      if (isNew) await api("split-days", { method: "POST", body: { ...body, split_id: splitId } });
      else await api(`split-days/${day.id}`, { method: "PATCH", body });
      closeSheet();
      toast("Trainingstag gespeichert");
      changed();
    } catch (e) { formError(form, e); }
  });
  if (!isNew) {
    // Wie beim Split: erst ausblenden, nach dem Rueckgaengig-Toast wirklich loeschen.
    armDelete($("[data-delete]", sheet), () => {
      closeSheet();
      deleteLater(`split-days/${day.id}`, `„${day.label}“ gelöscht`);
    }, "Löschen", "Trainingstag löschen?");
  }
}

function itemFields(item, exList, dayId) {
  const chosen = item?.exercise_id;
  const byGroup = {};
  for (const e of exList.filter((x) => !x.archived || x.id === chosen)) {
    (byGroup[e.muscle_group || "sonstige"] ||= []).push(e);
  }
  const labels = { brust: "Brust", ruecken: "Rücken", beine: "Beine", schultern: "Schultern",
    arme: "Arme", rumpf: "Rumpf", sonstige: "Sonstige" };
  return `<label class="field">Übung
      <select name="exercise_id" required ${item ? "disabled" : ""}>
        ${item ? "" : `<option value="">Bitte wählen…</option>`}
        ${Object.entries(byGroup).map(([g, list]) => `<optgroup label="${labels[g] || g}">${
          list.map((e) => `<option value="${e.id}"${e.id === chosen ? " selected" : ""}>${esc(e.name)}</option>`).join("")
        }</optgroup>`).join("")}
      </select>
    </label>
    <div class="field-row">
      <label class="field narrow">Sätze<input name="target_sets" type="number" inputmode="numeric"
        min="1" max="20" required value="${item?.target_sets ?? 3}"></label>
      <label class="field narrow">Whd. von<input name="target_reps_min" type="number" inputmode="numeric"
        min="1" max="100" required value="${item?.target_reps_min ?? 8}"></label>
      <label class="field narrow">Whd. bis<input name="target_reps_max" type="number" inputmode="numeric"
        min="1" max="100" required value="${item?.target_reps_max ?? 12}"></label>
    </div>
    <p class="hint">Schaffst du in <b>allen</b> Sätzen die obere Zahl, schlägt die App beim
      nächsten Mal mehr Gewicht vor und startet wieder bei der unteren.</p>
    <details class="field-more">
      <summary>Startgewicht, Pause und Notiz</summary>
      <label class="field">Gewicht<input name="target_weight" type="number" inputmode="decimal"
        min="0" max="1000" step="0.5" value="${item?.target_weight ?? ""}" placeholder="optional"></label>
      <label class="field">Pause (Sekunden)<input name="rest_seconds" type="number" inputmode="numeric"
        min="30" max="300" step="15" value="${item?.rest_seconds ?? ""}"
        placeholder="Standard: 120 Grundübung, 75 sonst"></label>
      <label class="field">Notiz<input name="notes" maxlength="200"
        value="${esc(item?.notes || "")}" placeholder="z. B. langsam ablassen"></label>
    </details>
    <input type="hidden" name="split_day_id" value="${dayId}">`;
}

async function editItem(item, dayId) {
  const isNew = !item;
  if (!exercises) {
    // Sheet sofort zeigen – auf dem Pi kann die Liste ein paar Sekunden dauern.
    const wait = openSheet({ title: isNew ? "Übung hinzufügen" : esc(item.name), body: skeleton(4) });
    try { exercises = await api("exercises"); } catch (e) { closeSheet(); return toast(e.message, { error: true }); }
    if (!wait.isConnected) return;   // inzwischen weggetippt
  }
  const sheet = openSheet({
    title: isNew ? "Übung hinzufügen" : esc(item.name),
    body: `<form id="itemForm" class="form">${itemFields(item, exercises, dayId ?? item.split_day_id)}</form>`,
    footer: footer("itemForm", !isNew),
  });
  const form = $("#itemForm", sheet);
  submitOnce(form, async () => {
    try {
      const data = formData(form);
      if (data.target_reps_min > data.target_reps_max) {
        throw new Error("Wiederholungen: „von“ darf nicht größer als „bis“ sein.");
      }
      if (isNew) {
        await api("split-exercises", { method: "POST", body: data });
      } else {
        delete data.exercise_id;   // Feld ist gesperrt, kommt gar nicht erst mit
        delete data.split_day_id;
        await api(`split-exercises/${item.id}`, { method: "PATCH", body: data });
      }
      closeSheet();
      toast("Gespeichert");
      changed();
    } catch (e) { formError(form, e); }
  });
  if (!isNew) {
    armDelete($("[data-delete]", sheet), () => {
      closeSheet();
      deleteLater(`split-exercises/${item.id}`, "Übung aus dem Plan entfernt");
    }, "Löschen", "Aus dem Plan entfernen?");
  }
}

/** Reihenfolge per Hoch/Runter – am Handy zuverlässiger als Ziehen. */
let moving = false;      // schnelles Doppeltippen darf keine zweite Umsortierung anstossen
let refocus = null;      // nach dem Neuzeichnen denselben Pfeil wieder fokussieren
async function move(itemId, dir) {
  const day = detail?.days.find((d) => d.exercises.some((e) => e.id === itemId));
  if (!day || moving) return;
  const list = [...day.exercises];
  const i = list.findIndex((e) => e.id === itemId);
  const j = i + dir;
  if (j < 0 || j >= list.length) return;
  [list[i], list[j]] = [list[j], list[i]];
  moving = true;
  try {
    await Promise.all(list.map((e, pos) => (e.position === pos ? null
      : api(`split-exercises/${e.id}`, { method: "PATCH", body: { position: pos } }))));
    refocus = `[data-move="${itemId}"][data-dir="${dir}"]`;
    changed();
  } catch (e) { toast(e.message, { error: true }); } finally { moving = false; }
}

// ------------------------------------------------------- Übungs-Katalog
async function manageExercises() {
  const wait = openSheet({ title: "Übungen verwalten", body: skeleton(6) });
  try { exercises = await api("exercises"); } catch (e) { closeSheet(); return toast(e.message, { error: true }); }
  if (!wait.isConnected) return;
  const labels = { brust: "Brust", ruecken: "Rücken", beine: "Beine", schultern: "Schultern",
    arme: "Arme", rumpf: "Rumpf" };
  const listHtml = (q = "") => {
    const ms = exercises.filter((e) => !e.archived && e.name.toLowerCase().includes(q));
    return ms.map((e) => `<div class="row-item" role="button" tabindex="0" data-ex="${e.id}">
      <div class="row-main"><div class="row-title">${esc(e.name)}</div>
        <div class="row-meta"><span>${labels[e.muscle_group] || "–"}</span>
          ${e.unit === "koerper" ? `<span>ohne Zusatzgewicht</span>` : ""}</div>
      </div>${icon("edit", "chev")}</div>`).join("")
      || `<p class="hint pad">Keine Übung passt.</p>`;
  };
  const sheet = openSheet({
    title: "Übungen",
    body: `<label class="search">${icon("search")}<input type="search" data-q
        placeholder="Übung suchen…" aria-label="Übung suchen"></label>
      <div class="btn-row"><button type="button" class="btn small tonal" data-new-ex>${icon("plus")} Neue Übung</button></div>
      <div class="list" data-list>${listHtml()}</div>`,
    wide: true,
  });
  const q = $("[data-q]", sheet);
  q.addEventListener("input", () => { $("[data-list]", sheet).innerHTML = listHtml(q.value.trim().toLowerCase()); });
  sheet.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-new-ex]")) return editExercise(null);
    const row = ev.target.closest("[data-ex]");
    if (row) editExercise(exercises.find((e) => e.id === Number(row.dataset.ex)));
  });
}

const textOf = (v) => (Array.isArray(v) ? v.join("\n") : v || "");

export function editExercise(ex) {
  const isNew = !ex;
  const groups = { brust: "Brust", ruecken: "Rücken", beine: "Beine", schultern: "Schultern",
    arme: "Arme", rumpf: "Rumpf" };
  const sheet = openSheet({
    title: isNew ? "Neue Übung" : "Übung",
    body: `<form id="exForm" class="form">
      <label class="field">Name<input name="name" maxlength="80" required autofocus
        value="${esc(ex?.name || "")}"></label>
      <fieldset class="field"><legend>Muskelgruppe</legend><div class="chips">
        ${Object.entries(groups).map(([k, label]) => `<label class="chip">
          <input type="radio" name="muscle_group" value="${k}" ${ex?.muscle_group === k ? "checked" : ""}>${label}</label>`).join("")}
      </div></fieldset>
      <details class="field-more"${ex?.pattern ? " open" : ""}>
        <summary>Bewegungsmuster (für den Assistenten)</summary>
        <label class="field">Muster
          <select name="pattern">
            <option value="">keines – der Assistent nutzt die Übung nicht</option>
            ${Object.entries(PATTERNS).map(([k, l]) => `<option value="${k}"${ex?.pattern === k ? " selected" : ""}>${l}</option>`).join("")}
          </select>
        </label>
      </details>
      <details class="field-more"${ex?.instructions ? "" : ""}>
        <summary>Anleitung (für die Übungsbibliothek)</summary>
        <label class="field">Gerät<input name="equipment" maxlength="80" value="${esc(ex?.equipment || "")}"
          placeholder="z. B. Kabelzug"></label>
        <label class="field">Ausführung – ein Schritt pro Zeile
          <textarea name="instructions" rows="4" maxlength="2000">${esc(textOf(ex?.instructions))}</textarea></label>
        <label class="field">Typische Fehler – einer pro Zeile
          <textarea name="mistakes" rows="3" maxlength="1000">${esc(textOf(ex?.mistakes))}</textarea></label>
      </details>
      <fieldset class="field"><legend>Gewicht</legend><div class="segmented">
        <label><input type="radio" name="unit" value="kg" ${ex?.unit !== "koerper" ? "checked" : ""}><span>mit Gewicht</span></label>
        <label><input type="radio" name="unit" value="koerper" ${ex?.unit === "koerper" ? "checked" : ""}><span>Körpergewicht</span></label>
      </div></fieldset>
    </form>`,
    footer: footer("exForm", !isNew),
  });
  const form = $("#exForm", sheet);
  submitOnce(form, async () => {
    try {
      const body = formData(form);
      if (isNew) await api("exercises", { method: "POST", body });
      else await api(`exercises/${ex.id}`, { method: "PATCH", body });
      closeSheet();
      exercises = null;
      toast("Übung gespeichert");
      changed();
    } catch (e) { formError(form, e); }
  });
  if (!isNew) {
    // Der Katalog ist fuer alle Profile gemeinsam – geloescht wird auch aus fremden Plaenen.
    armDelete($("[data-delete]", sheet), async () => {
      closeSheet();
      try {
        const res = await api(`exercises/${ex.id}`, { method: "DELETE" });
        exercises = null;
        toast(res.archived
          ? "Übung ausgeblendet – deine bisherigen Sätze bleiben erhalten"
          : "Übung gelöscht");
        changed();
      } catch (e) { toast(e.message, { error: true }); }
    }, "Löschen", "Aus allen Plänen löschen?");
  }
}

// -------------------------------------------------------- Plan verbessern
const LEVEL_ICON = { warn: "warn", info: "spark" };

function volumeRows(data) {
  const max = Math.max(10, ...data.volume.map((v) => Math.max(v.actual, v.target)));
  return `<div class="bars">${data.volume.map((v) => `
    <div class="bar-row"><span class="bar-label ${v.focus ? "hot" : ""}">${esc(v.label)}</span>
      <span class="bar ${v.actual < v.target - 2 ? "low" : ""}"><i style="width:${(v.actual / max) * 100}%"></i></span>
      <span class="bar-value">${v.actual}${v.actual === v.target ? "" : ` / ${v.target}`}
        <small>${v.frequency}×</small></span>
    </div>`).join("")}</div>
  <p class="hint">Sätze pro Woche (Ist / Richtwert) und wie oft die Gruppe drankommt.
    Richtwerte kommen aus deinem Ziel, deiner Erfahrung, dem Fokus und deiner Zeit.</p>`;
}

function suggestionRows(data) {
  if (!data.suggestions.length) {
    return `<div class="row-item"><span class="row-icon up">${icon("check")}</span>
      <div class="row-main"><div class="row-title">Nichts zu verbessern gefunden.</div>
      <div class="row-meta"><span>Volumen, Häufigkeit und Übungsauswahl passen zu deinen Angaben.</span></div>
      </div></div>`;
  }
  return data.suggestions.map((s, i) => `<div class="row-item">
      <span class="row-icon ${s.level === "warn" ? "warn" : ""}">${icon(LEVEL_ICON[s.level] || "spark")}</span>
      <div class="row-main">
        <div class="row-title">${esc(s.text)}</div>
        <div class="row-meta"><span>${esc(s.why)}</span></div>
      </div>
      ${s.action?.type === "open_exercise"
        ? `<button type="button" class="btn small ghost" data-open-ex="${s.action.exercise_id}">Öffnen</button>`
        : s.action ? `<button type="button" class="btn small tonal" data-apply="${i}">Übernehmen</button>` : ""}
    </div>`).join("");
}

function adviceCard(data) {
  const a = data.split_advice;
  if (!a) return "";
  return `<div class="card">
    <p class="eyebrow">Passt der Split?</p>
    <p>${esc(a.text)}</p>
    ${a.best ? `<ul class="plain">${a.best.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      <div class="btn-row"><button type="button" class="btn tonal" data-wizard>${icon("spark")} Plan mit „${esc(a.best.name)}“ erstellen</button></div>` : ""}
  </div>`;
}

async function improvePlan() {
  let data;
  const wait = openSheet({ title: "Plan verbessern", body: skeleton(5), wide: true });
  try {
    data = await api("plan/analyze");
  } catch (e) { closeSheet(); return toast(e.message, { error: true }); }
  if (!wait.isConnected) return;
  if (!data.split) { closeSheet(); return toast("Es gibt noch keinen Plan zum Verbessern.", { error: true }); }
  const sheet = openSheet({ title: "Plan verbessern", body: "", wide: true });
  const body = $(".sheet-body", sheet);

  function paint() {
    body.innerHTML = `<p class="hint">${esc(data.split.name)} · ${data.sessions} Einheiten pro Woche</p>
      ${adviceCard(data)}
      <h3 class="form-section">Vorschläge</h3>
      <div class="list">${suggestionRows(data)}</div>
      <h3 class="form-section">Volumen</h3>
      ${volumeRows(data)}
      ${data.ai_available ? `<div class="btn-row"><button type="button" class="btn tonal" data-ai>
          ${icon("spark")} Zweite Meinung von der KI</button></div>
        <div data-ai-out></div>` : ""}`;
  }
  paint();

  sheet.addEventListener("click", async (ev) => {
    const applyBtn = ev.target.closest("[data-apply]");
    if (applyBtn) {
      const suggestion = data.suggestions[Number(applyBtn.dataset.apply)];
      return busy(applyBtn, async () => {
        try {
          const res = await api("plan/apply", { method: "POST", body: { action: suggestion.action } });
          data = { ...res, ai_available: data.ai_available };
          toast("Übernommen");
          changed();
          paint();
        } catch (e) { toast(e.message, { error: true }); }
      });
    }
    const openEx = ev.target.closest("[data-open-ex]");
    if (openEx) {
      const list = exercises || await api("exercises");
      exercises = list;
      closeSheet();
      return editExercise(list.find((x) => x.id === Number(openEx.dataset.openEx)));
    }
    if (ev.target.closest("[data-wizard]")) {
      const profile = await api(`profiles/${getProfile()}`).catch(() => null);
      closeSheet();
      return openWizard(profile);
    }
    const aiBtn = ev.target.closest("[data-ai]");
    if (aiBtn) {
      return busy(aiBtn, async () => {
        const out = $("[data-ai-out]", sheet);
        out.innerHTML = `<p class="hint">Die KI sieht sich den Plan an …</p>`;
        try {
          const r = await api("check/ai", { method: "POST", timeout: 50000 });
          out.innerHTML = r.text
            ? `<div class="card ai-note"><p class="eyebrow">${icon("spark")} Einschätzung der KI</p>${
              esc(r.text).replace(/\n/g, "<br>")}</div>`
            : `<p class="hint">${esc(r.error)}</p>`;
        } catch (e) {
          out.innerHTML = `<p class="hint">${esc(e.offline ? "Ohne Verbindung geht die KI-Prüfung nicht." : e.message)}</p>`;
        }
      });
    }
  });
}

/** Seltene Plan-Aktionen – vorher vier Knoepfe ueber dem Plan. */
function openPlanMenu() {
  const open = detail && splits?.some((s) => s.id === detail.id) ? detail : null;
  actionMenu(open ? esc(open.name) : "Plan", [
    open?.days.length && { label: "Plan verbessern", icon: "spark", run: improvePlan },
    blockInfo?.block && { label: "Neuen Block starten", icon: "refresh", run: async () => {
      try { await api("planner/new-block", { method: "POST" }); toast("Neuer Block gestartet"); changed(); await load(); }
      catch (e) { toast(e.message, { error: true }); }
    } },
    open && { label: "Plan löschen", icon: "trash", run: () => removePlan(splits.find((x) => x.id === open.id) || open) },
    open && { label: "Split umbenennen", icon: "edit", run: () => editSplit(splits.find((s) => s.id === open.id)) },
    { label: "Neuen Plan mit Assistent", icon: "spark", run: async () => {
      const profile = await api(`profiles/${getProfile()}`).catch(() => null);
      openWizard(profile);
    } },
    { label: "Leeren Split anlegen", icon: "plus", run: () => editSplit(null) },
    { label: "Übungskatalog bearbeiten", icon: "dumbbell", run: manageExercises },
  ]);
}

// --------------------------------------------------------------- Mount
export async function mount(el) {
  root = el;
  // toggle blubbert nicht – im Capture mitlesen, welche Tage offen sind.
  el.addEventListener("toggle", (ev) => {
    const d = ev.target.closest?.("[data-day]");
    if (!d || ev.target !== d) return;
    const id = Number(d.dataset.day);
    if (d.open) openDays.add(id); else openDays.delete(id);
    saveOpenDays();
  }, true);
  el.addEventListener("click", async (ev) => {
    const t = (sel) => ev.target.closest(sel);
    const act = t("[data-activate]");
    if (act) {
      ev.stopPropagation();
      const id = act.dataset.activate;
      // Zweiter Tipp bestaetigt – ein einzelner Tipp wechselt sonst den Plan ohne
      // Rueckfrage, inkonsistent zu jeder anderen folgenreichen Aktion in der App.
      if (confirmActivate?.id !== id) {
        clearTimeout(confirmActivate?.timer);
        const prevBtn = confirmActivate && $(`[data-activate="${confirmActivate.id}"]`, root);
        if (prevBtn) prevBtn.textContent = "Wechseln";
        act.textContent = "Wirklich wechseln?";
        confirmActivate = { id, timer: setTimeout(() => {
          if (confirmActivate?.id === id) { act.textContent = "Wechseln"; confirmActivate = null; }
        }, 3000) };
        return;
      }
      clearTimeout(confirmActivate.timer);
      confirmActivate = null;
      const previous = splits?.find((s) => s.active)?.id;
      try {
        await api(`splits/${id}/activate`, { method: "POST" });
        splits = null;
        changed();
        toast("Split gewechselt", previous ? {
          action: "Rückgängig",
          onAction: async () => {
            try { await api(`splits/${previous}/activate`, { method: "POST" }); splits = null; changed(); }
            catch (e) { toast(e.message, { error: true }); }
          },
        } : undefined);
      } catch (e) { toast(e.message, { error: true }); }
      return;
    }
    const sort = t("[data-sort]");
    if (sort) {
      ev.stopPropagation();
      const id = Number(sort.dataset.sort);
      sortingDay = sortingDay === id ? null : id;
      return render();
    }
    const move_ = t("[data-move]");
    if (move_) { ev.stopPropagation(); return move(Number(move_.dataset.move), Number(move_.dataset.dir)); }
    if (t("[data-add-split]")) return editSplit(null);
    const editS = t("[data-edit-split]");
    if (editS) return editSplit(splits.find((s) => s.id === Number(editS.dataset.editSplit)));
    const delS = t("[data-delete-split]");
    if (delS) return askDeletePlan(splits.find((x) => x.id === Number(delS.dataset.deleteSplit)), delS);
    const openS = t("[data-open-split]");
    if (openS) {
      try {
        detail = await api(`splits/${Number(openS.dataset.openSplit)}`);
        openSplitId = detail.id;
      } catch (e) {
        return toast(e.offline ? "Dieser Split ist offline noch nicht geladen – einmal mit Netz öffnen." : e.message, { error: true });
      }
      return render();
    }
    const addDay = t("[data-add-day]");
    if (addDay) return editDay(null, Number(addDay.dataset.addDay));
    const editD = t("[data-edit-day]");
    if (editD) return editDay(detail.days.find((d) => d.id === Number(editD.dataset.editDay)));
    const addItem = t("[data-add-item]");
    if (addItem) { openDays.add(Number(addItem.dataset.addItem)); saveOpenDays(); return editItem(null, Number(addItem.dataset.addItem)); }
    const editI = t("[data-edit-item]");
    if (editI) {
      const id = Number(editI.dataset.editItem);
      const item = detail.days.flatMap((d) => d.exercises).find((e) => e.id === id);
      if (item) return editItem(item);
    }
    if (t("[data-plan-menu]")) return openPlanMenu();
    if (t("[data-exercises]")) return manageExercises();
    if (t("[data-check]")) return improvePlan();
    if (t("[data-wizard]")) {
      const profile = await api(`profiles/${getProfile()}`).catch(() => null);
      return openWizard(profile);
    }
  });
}
