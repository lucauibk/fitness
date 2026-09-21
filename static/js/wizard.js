/** Plan-Assistent: fragt Ziel, Zeit und Erfahrung ab, schlägt Splits vor und zeigt eine
 *  Vorschau, die man anpassen und übernehmen kann. Der Stand liegt als Entwurf im
 *  Gerätespeicher – Schließen verliert nichts. */
import { $, api, busy, closeSheet, esc, getProfile, icon, openSheet, prefs, toast,
  WEEKDAYS } from "./util.js";

export const GOALS = {
  aufbau: { label: "Muskelaufbau", text: "Mehr Muskeln, sichtbare Form." },
  kraft: { label: "Kraft", text: "Schwerer heben, wenige Wiederholungen." },
  abnehmen: { label: "Abnehmen / Straffen", text: "Viel Bewegung, Muskeln erhalten." },
  fitness: { label: "Allgemeine Fitness", text: "Fit und belastbar im Alltag." },
};
export const EXPERIENCE = {
  einsteiger: { label: "Einsteiger", text: "Weniger als 6 Monate regelmäßig trainiert." },
  fortgeschritten: { label: "Fortgeschritten", text: "Etwa 6 Monate bis 2 Jahre." },
  erfahren: { label: "Erfahren", text: "Mehr als 2 Jahre, Technik sitzt." },
};
const STEPS = ["Deine Daten", "Ziel", "Zeit", "Erfahrung", "Fokus", "Split", "Vorschau"];
const WEEKDAYS_LONG_LOCAL = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
export const FOCUS = {
  brust: "Brust", ruecken: "Rücken", schultern: "Schultern", arme: "Arme",
  beine: "Beine", po: "Po", rumpf: "Bauch & Rumpf",
};
const MAX_FOCUS = 3;
const MINUTES = [45, 60, 75, 90];
const NO_LIMIT = 0;   // "egal" – dann bestimmt das Zielvolumen den Umfang

let st = null;       // Entwurf
let sheet = null;

// Ein Assistenten-Entwurf je Profil (wie der Einheit-Entwurf in training.js).
const wizardKey = (profileId) => `wizard.${profileId}`;
function loadWizard() {
  const legacy = prefs.get("wizard");
  if (legacy) {
    if (!prefs.get(wizardKey(legacy.profileId))) prefs.set(wizardKey(legacy.profileId), legacy);
    prefs.set("wizard", null);
  }
  const d = prefs.get(wizardKey(getProfile()));
  return d && d.profileId === getProfile() && Array.isArray(d.weekdays) ? d : null;
}
export const hasWizardDraft = () => Boolean(loadWizard());
const save = () => {
  prefs.set(wizardKey(st.profileId), st);
  window.dispatchEvent(new CustomEvent("wizard-changed"));
};
const clear = () => {
  prefs.set(wizardKey(st?.profileId ?? getProfile()), null);
  st = null;
  window.dispatchEvent(new CustomEvent("wizard-changed"));
};
const range = (lo, hi) => (lo === hi ? `${lo}` : `${lo}–${hi}`);
const daysLabel = (d) => (d?.length ? d.map((i) => WEEKDAYS[i]).join(" · ") : "frei");

export function openWizard(profile) {
  const draft = loadWizard();
  if (draft) {
    st = draft;
    // Entwurf aus einer älteren Version: Vorschläge neu holen
    if (st.options && !st.options.every((o) => o.frequency)) {
      st.options = null;
      st.preview = null;
      if (st.step > 4) st.step = 4;
    }
  } else {
    const p = profile || {};
    // Stehen die Eckdaten schon im Profil, gleich bei "Ziel" anfangen – "Zurueck" fuehrt hin.
    const known = Boolean(p.birth_year && p.height_cm && p.sex);
    st = {
      profileId: getProfile(), step: known ? 1 : 0,
      data: { birth_year: p.birth_year ?? null, height_cm: p.height_cm ?? null,
        sex: p.sex ?? null, limitations: p.limitations ?? "" },
      goal: p.goal || null,
      minutes: p.session_minutes ?? (p.days_per_week ? NO_LIMIT : null),
      weekdays: p.train_weekdays || [], focus: p.focus || [],
      experience: p.experience || null,
      option: null, options: null, aiAvailable: false,
      preview: null, check: null, activate: true, swapping: null, alternatives: null,
    };
  }
  sheet = openSheet({ title: "Plan-Assistent", body: "", footer: "", wide: true });
  sheet.addEventListener("click", onClick);
  sheet.addEventListener("input", onInput);
  sheet.addEventListener("change", onInput);
  render();
}

// ------------------------------------------------------------ Anzeige
const choice = (group, value, label, text, active) => `
  <button type="button" class="choice ${active ? "active" : ""}" data-pick="${group}" data-value="${value}"
    aria-pressed="${active}">
    <span class="choice-title">${esc(label)}</span>
    ${text ? `<span class="choice-text">${esc(text)}</span>` : ""}
  </button>`;

function stepData() {
  const d = st.data;
  const year = new Date().getFullYear();
  return `<p class="hint">Alles freiwillig. Einschränkungen wie „Knie“, „Rücken“, „Schulter“ oder
      „Ellbogen“ wählt der Assistent direkt bei den Übungen aus.</p>
    <div class="form">
      <div class="field-row">
        <label class="field">Geburtsjahr<input data-field="birth_year" type="number" inputmode="numeric"
          min="1920" max="${year - 6}" value="${d.birth_year ?? ""}"></label>
        <label class="field">Größe (cm)<input data-field="height_cm" type="number" inputmode="decimal"
          min="100" max="250" step="0.5" value="${d.height_cm ?? ""}"></label>
      </div>
      <label class="field">Geschlecht
        <select data-field="sex">
          ${[["", "keine Angabe"], ["weiblich", "weiblich"], ["männlich", "männlich"], ["divers", "divers"]]
            .map(([v, l]) => `<option value="${v}"${(d.sex || "") === v ? " selected" : ""}>${l}</option>`).join("")}
        </select>
      </label>
      <label class="field">Einschränkungen oder Verletzungen
        <textarea data-field="limitations" rows="2" maxlength="500"
          placeholder="optional, z. B. Knie links">${esc(d.limitations || "")}</textarea>
      </label>
    </div>`;
}

const stepGoal = () => `<p class="hint">Was willst du vor allem erreichen?</p>
  <div class="choices">${Object.entries(GOALS).map(([k, g]) => choice("goal", k, g.label, g.text, st.goal === k)).join("")}</div>`;

const stepTime = () => `<h3 class="form-section">An welchen Tagen kannst du trainieren?</h3>
  <div class="choices row days">${WEEKDAYS.map((w, i) => `<button type="button"
      class="choice ${st.weekdays.includes(i) ? "active" : ""}" data-weekday="${i}"
      aria-pressed="${st.weekdays.includes(i)}" aria-label="${WEEKDAYS_LONG_LOCAL[i]}">
      <span class="choice-title">${w}</span></button>`).join("")}</div>
  <p class="hint">${st.weekdays.length < 2 ? "Wähle 2 bis 6 Tage."
    : st.weekdays.length > 6 ? "Höchstens 6 Tage – ein Ruhetag pro Woche muss sein."
    : `${st.weekdays.length} Tage pro Woche${backToBack(st.weekdays) ? " · einige direkt hintereinander – das berücksichtigt der Vorschlag" : ""}.`}</p>
  <h3 class="form-section">Wie lange pro Einheit?</h3>
  <div class="choices row">${MINUTES.map((m) => choice("minutes", m, `${m} min`, "", st.minutes === m)).join("")}
    ${choice("minutes", NO_LIMIT, "egal", "", st.minutes === NO_LIMIT)}</div>
  <p class="hint">${st.minutes === NO_LIMIT
    ? "Ohne Obergrenze: der Plan richtet sich allein nach dem sinnvollen Wochenvolumen."
    : "Die Zeit bestimmt, wie viele Sätze pro Einheit möglich sind (inklusive Satzpausen)."}</p>`;

const backToBack = (wd) => {
  const d = [...wd].sort();
  return d.some((x, i) => i && x - d[i - 1] === 1) || (d.includes(0) && d.includes(6));
};

const stepFocus = () => `<p class="hint">Welche Muskeln sollen mehr Aufmerksamkeit bekommen?
    Bis zu ${MAX_FOCUS} – oder keine für einen ausgewogenen Plan.</p>
  <div class="chips focus">${Object.entries(FOCUS).map(([k, l]) => `<button type="button"
      class="chip ${st.focus.includes(k) ? "active" : ""}" data-focus="${k}"
      aria-pressed="${st.focus.includes(k)}">${l}</button>`).join("")}</div>
  <p class="hint">${st.focus.length ? `Fokus: ${st.focus.map((f) => FOCUS[f]).join(", ")} – dafür gibt es mehr Sätze und bei Bedarf eine Zusatzübung.`
    : "Kein Fokus: alle Muskelgruppen gleichmäßig."}</p>`;

const stepExperience = () => `<p class="hint">Wie lange trainierst du schon im Studio?</p>
  <div class="choices">${Object.entries(EXPERIENCE).map(([k, e]) => choice("experience", k, e.label, e.text, st.experience === k)).join("")}</div>`;

const FREQ_ORDER = ["beine", "po", "brust", "ruecken", "schultern", "arme"];
const FREQ_LABEL = { beine: "Beine", po: "Po", brust: "Brust", ruecken: "Rücken", schultern: "Schultern", arme: "Arme" };
const times = (v) => `${String(v).replace(".", ",")}×`;

function freqLine(freq, focus = []) {
  return `<span class="freq">${FREQ_ORDER.map((g) => `<span class="${focus.includes(g) ? "hot" : ""}">
    ${FREQ_LABEL[g]} <b>${times(freq[g] ?? 0)}</b></span>`).join("")}</span>`;
}

function stepSplit() {
  if (!st.options) return `<p class="hint">Lade Vorschläge …</p>`;
  const wd = [...st.weekdays].sort();
  return `<p class="hint">Für deine ${wd.length} Tage (${daysLabel(wd)})${st.focus.length
      ? ` mit Fokus ${st.focus.map((f) => FOCUS[f]).join(", ")}` : ""} – sortiert nach Eignung:</p>
    <div class="choices">${st.options.map((o) => `
      <button type="button" class="choice split-choice ${st.option === o.key ? "active" : ""}" data-pick="option"
        data-value="${o.key}" aria-pressed="${st.option === o.key}">
        <span class="choice-title">${esc(o.name)}${o.recommended ? ` <span class="pill ok">empfohlen</span>` : ""}</span>
        <span class="choice-text">${esc(o.text)}</span>
        ${(o.pro || []).length || (o.contra || []).length ? `<ul class="why">
          ${(o.pro || []).map((r) => `<li class="pro">${esc(r)}</li>`).join("")}
          ${(o.contra || []).map((r) => `<li class="contra">${esc(r)}</li>`).join("")}
        </ul>` : ""}
        <span class="choice-text">pro Woche: ${freqLine(o.frequency, o.focus)}</span>
        <span class="choice-text">${o.rotate ? "Tage wechseln automatisch: " : ""}${o.days.map((d) =>
          `${esc(d.label)}${d.weekdays.length ? ` (${daysLabel(d.weekdays)})` : ""}`).join(" · ")}</span>
      </button>`).join("")}</div>`;
}

function exerciseRow(ex, di, ei, day) {
  const key = `${di}:${ei}`;
  const open = st.swapping === key;
  return `<div class="row-item wiz-ex">
      <div class="row-main">
        <div class="row-title">${esc(ex.name)}</div>
        <div class="row-meta"><span>${ex.target_sets} × ${range(ex.target_reps_min, ex.target_reps_max)} Whd.</span>
          ${ex.focus ? `<span class="pill">Fokus</span>` : ""}</div>
      </div>
      <button type="button" class="icon-btn" data-swap="${key}" aria-label="${esc(ex.name)} tauschen"
        aria-expanded="${open}">${icon("swap")}</button>
      ${day.exercises.length > 1 ? `<button type="button" class="icon-btn" data-remove="${key}"
        aria-label="${esc(ex.name)} entfernen">${icon("x")}</button>` : ""}
    </div>
    ${open ? `<div class="swap-list">${st.alternatives === null ? `<p class="hint">Lade …</p>`
      : st.alternatives.length ? st.alternatives.map((a) => `<button type="button" class="chip"
          data-replace="${a.id}">${esc(a.name)}</button>`).join("")
      : `<p class="hint">Keine Alternative mit gleichem Bewegungsablauf.</p>`}</div>` : ""}`;
}

/** Wochensätze je Muskelgruppe: Ziel aus der Trainingswissenschaft gegen den Plan. */
function volumeTable(pv) {
  const v = pv.volume;
  if (!v) return "";
  const rows = Object.keys(v.target).filter((g) => v.target[g] || v.planned[g]);
  const max = Math.max(6, ...rows.map((g) => Math.max(v.target[g], v.planned[g])));
  return `<h4 class="form-section">Sätze pro Woche</h4>
    <div class="bars">${rows.map((g) => `<div class="bar-row">
      <span class="bar-label">${esc(v.labels[g])}</span>
      <span class="bar"><i style="width:${(v.planned[g] / max) * 100}%"></i></span>
      <span class="bar-value">${v.planned[g]}${v.planned[g] === v.target[g] ? "" : ` / ${v.target[g]}`}</span>
    </div>`).join("")}</div>
    <p class="hint">Balken = im Plan, Zahl dahinter = Richtwert. Etwa 10+ harte Sätze je Muskel
      und Woche gelten als Schwelle für nahezu maximalen Aufbau.</p>`;
}

function stepPreview() {
  const pv = st.preview;
  if (!pv) return `<p class="hint">Erstelle Plan …</p>`;
  const findings = st.check?.findings || [];
  return `<label class="field">Name<input data-plan-name maxlength="80" value="${esc(pv.name)}"></label>
    ${pv.basis?.length ? `<details class="card basis" open><summary class="eyebrow">Darauf basiert dein Plan</summary>
      <ul class="plain">${pv.basis.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
      ${pv.frequency ? `<p class="hint">Häufigkeit pro Woche: ${freqLine(pv.frequency, pv.focus || [])}</p>` : ""}
      ${volumeTable(pv)}</details>` : ""}
    ${pv.notes?.length ? `<div class="card ai-note"><p class="eyebrow">${icon("spark")} Angepasst von der KI</p>
      <ul class="plain">${pv.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
    ${st.aiError ? `<p class="callout">${esc(st.aiError)}</p>` : ""}
    ${pv.days.map((d, di) => `<div class="card wiz-day">
      <div class="card-title">${esc(d.label)}</div>
      <div class="row-meta"><span>${d.weekdays.length ? daysLabel(d.weekdays) : "im Wechsel"}</span><span>${d.exercises.length} Übungen</span></div>
      <div class="list flush">${d.exercises.map((ex, ei) => exerciseRow(ex, di, ei, d)).join("")}</div>
    </div>`).join("")}
    <h3 class="form-section">Plan-Check</h3>
    <div class="list">${findings.length ? findings.map((f) => `<div class="row-item">
        <span class="row-icon ${f.level === "warn" ? "warn" : ""}">${icon(f.level === "warn" ? "warn" : "check")}</span>
        <div class="row-main"><div class="row-title">${esc(f.text)}</div></div></div>`).join("")
      : `<div class="row-item"><span class="row-icon up">${icon("check")}</span>
        <div class="row-main"><div class="row-title">Keine Lücken gefunden.</div></div></div>`}</div>
    ${st.aiAvailable ? `<div class="btn-row"><button type="button" class="btn tonal" data-ai>
        ${icon("spark")} Mit KI verfeinern</button></div>
      <p class="hint">Die KI berücksichtigt auch Alter und Einschränkungen und nutzt nur Übungen aus deinem Katalog.</p>` : ""}
    <div class="toggles"><label class="toggle"><input type="checkbox" data-activate ${st.activate ? "checked" : ""}>
      <span></span>Gleich als aktiven Plan verwenden</label></div>
    <p class="hint">Wochentage und Gewichte kannst du danach unter „Plan“ anpassen.</p>`;
}

function canNext() {
  const wd = st.weekdays.length;
  return [true, Boolean(st.goal), Boolean(wd >= 2 && wd <= 6 && st.minutes !== null), Boolean(st.experience),
    true, Boolean(st.option), Boolean(st.preview)][st.step];
}

function render() {
  if (!sheet?.isConnected) return;
  $("#sheetTitle", sheet).textContent = `Plan-Assistent · ${STEPS[st.step]}`;
  const body = $(".sheet-body", sheet);
  const views = [stepData, stepGoal, stepTime, stepExperience, stepFocus, stepSplit, stepPreview];
  body.innerHTML = `<div class="steps-bar" aria-label="Schritt ${st.step + 1} von ${STEPS.length}">
      ${STEPS.map((_, i) => `<i class="${i <= st.step ? "on" : ""}"></i>`).join("")}</div>
    <div class="wizard-step"><p class="eyebrow">Schritt ${st.step + 1} von ${STEPS.length}</p>
      ${st.step > 0 ? `<button type="button" class="link-btn danger-text" data-reset>Neu beginnen</button>` : ""}</div>
    ${views[st.step]()}`;
  let foot = $(".sheet-foot", sheet);
  if (!foot) {
    foot = document.createElement("div");
    foot.className = "sheet-foot";
    sheet.appendChild(foot);
  }
  const last = st.step === STEPS.length - 1;
  foot.innerHTML = `
    ${st.step > 0 ? `<button type="button" class="btn ghost" data-back>${icon("left")} Zurück</button>` : ""}
    <span class="grow"></span>
    ${last ? `<button type="button" class="btn primary" data-save ${canNext() ? "" : "disabled"}>${icon("check")} Plan übernehmen</button>`
      : `<button type="button" class="btn primary" data-next ${canNext() ? "" : "disabled"}>Weiter ${icon("right")}</button>`}`;
}

// ------------------------------------------------------------ Aktionen
const planBody = () => ({ ...st.preview });

async function recheck() {
  try {
    const res = await api("wizard/check", { method: "POST", body: planBody() });
    st.check = res.check;
    save();
    render();
  } catch { /* Befunde bleiben auf dem letzten Stand */ }
}

function failed(e) {
  toast(e.offline ? "Für den Assistenten braucht es eine Verbindung zum Server." : e.message, { error: true });
}

async function next() {
  try {
    if (st.step === 0) {
      const body = { ...st.data, limitations: (st.data.limitations || "").trim() || null };
      await api(`profiles/${getProfile()}`, { method: "PATCH", body });
    }
    if (st.step === 4) {
      const weekdays = [...st.weekdays].sort();
      await api(`profiles/${getProfile()}`, { method: "PATCH", body: {
        goal: st.goal, train_weekdays: weekdays, session_minutes: st.minutes || null,
        experience: st.experience, focus: st.focus } });
      const q = new URLSearchParams({ weekdays: weekdays.join(","), experience: st.experience,
        goal: st.goal, focus: st.focus.join(",") });
      q.set("minutes", st.minutes ?? 0);
      const res = await api(`wizard/options?${q}`);
      st.options = res.options;
      st.aiAvailable = res.ai_available;
      if (!st.options.some((o) => o.key === st.option)) {
        st.option = (st.options.find((o) => o.recommended) || st.options[0]).key;
        st.preview = null;
      }
    }
    if (st.step === 5) {
      const res = await api("wizard/preview", { method: "POST", body: {
        option: st.option, goal: st.goal, experience: st.experience,
        minutes: st.minutes ?? 0, weekdays: [...st.weekdays].sort(), focus: st.focus } });
      st.preview = res.preview;
      st.check = res.check;
      st.aiError = null;
      st.swapping = null;
    }
    st.step = Math.min(st.step + 1, STEPS.length - 1);
    save();
    render();
    $(".sheet-body", sheet)?.scrollTo(0, 0);
  } catch (e) { failed(e); }
}

async function onClick(ev) {
  const t = (sel) => ev.target.closest(sel);
  if (t("[data-back]")) {
    st.step = Math.max(0, st.step - 1);
    save();
    return render();
  }
  if (t("[data-reset]")) {
    const profile = await api(`profiles/${getProfile()}`).catch(() => null);
    clear();
    closeSheet();
    return openWizard(profile);
  }
  const next_ = t("[data-next]");
  if (next_) return busy(next_, next);
  const wdBtn = t("[data-weekday]");
  if (wdBtn) {
    const i = Number(wdBtn.dataset.weekday);
    st.weekdays = st.weekdays.includes(i) ? st.weekdays.filter((x) => x !== i) : [...st.weekdays, i];
    st.options = null;
    st.preview = null;
    save();
    return render();
  }
  const focusBtn = t("[data-focus]");
  if (focusBtn) {
    const f = focusBtn.dataset.focus;
    if (st.focus.includes(f)) st.focus = st.focus.filter((x) => x !== f);
    else if (st.focus.length < MAX_FOCUS) st.focus = [...st.focus, f];
    else toast(`Höchstens ${MAX_FOCUS} Schwerpunkte.`);
    st.preview = null;
    st.options = null;
    save();
    return render();
  }
  const pick = t("[data-pick]");
  if (pick) {
    const { pick: group, value } = pick.dataset;
    const val = group === "minutes" ? Number(value) : value;
    if (st[group] !== val) {
      st[group] = val;
      // Andere Eckdaten -> Vorschläge und Vorschau sind veraltet
      if (group !== "option") st.options = null;
      st.preview = null;
      st.check = null;
    }
    save();
    return render();
  }
  const swap = t("[data-swap]");
  if (swap) {
    const key = swap.dataset.swap;
    if (st.swapping === key) { st.swapping = null; return render(); }
    st.swapping = key;
    st.alternatives = null;
    render();
    const [di, ei] = key.split(":").map(Number);
    const day = st.preview.days[di];
    const ex = day.exercises[ei];
    try {
      const exclude = day.exercises.map((x) => x.exercise_id).join(",");
      st.alternatives = await api(`wizard/alternatives?pattern=${encodeURIComponent(ex.pattern)}&exclude=${exclude}`);
    } catch (e) { st.alternatives = []; failed(e); }
    return render();
  }
  const replace = t("[data-replace]");
  if (replace) {
    const [di, ei] = st.swapping.split(":").map(Number);
    const alt = st.alternatives.find((a) => a.id === Number(replace.dataset.replace));
    const ex = st.preview.days[di].exercises[ei];
    Object.assign(ex, { exercise_id: alt.id, name: alt.name, muscle_group: alt.muscle_group,
      unit: alt.unit, pattern: alt.pattern });
    st.swapping = null;
    save();
    render();
    return recheck();
  }
  const remove = t("[data-remove]");
  if (remove) {
    const [di, ei] = remove.dataset.remove.split(":").map(Number);
    st.preview.days[di].exercises.splice(ei, 1);
    st.swapping = null;
    save();
    render();
    return recheck();
  }
  const aiBtn = t("[data-ai]");
  if (aiBtn) {
    return busy(aiBtn, async () => {
      aiBtn.textContent = "Die KI sieht sich den Plan an …";
      try {
        const res = await api("wizard/refine", { method: "POST", body: planBody(), timeout: 95000 });
        st.preview = { ...res.preview, name: st.preview.name };
        st.check = res.check;
        st.aiError = res.error || null;
        save();
      } catch (e) { failed(e); }
      render();
    });
  }
  const saveBtn = t("[data-save]");
  if (saveBtn) {
    return busy(saveBtn, async () => {
      try {
        const split = await api("wizard/save", { method: "POST",
          body: { plan: planBody(), activate: st.activate } });
        clear();
        closeSheet();
        toast(`„${split.name}“ angelegt${split.active ? " und aktiviert" : ""}`);
        window.dispatchEvent(new CustomEvent("data-changed"));
        // closeSheet() geht per history.back() zurueck – erst danach umschalten,
        // sonst setzt der Ruecksprung die Adresse wieder auf die alte Seite.
        window.addEventListener("popstate", () => { location.hash = "training/plan"; }, { once: true });
        setTimeout(() => { if (location.hash !== "#training/plan") location.hash = "training/plan"; }, 400);
      } catch (e) { failed(e); }
    });
  }
}

function onInput(ev) {
  const f = ev.target.closest("[data-field]");
  if (f) {
    const v = f.value.trim();
    st.data[f.dataset.field] = v === "" ? null : (f.type === "number" ? Number(v) : f.value);
    return save();
  }
  if (ev.target.matches("[data-plan-name]") && st.preview) {
    st.preview.name = ev.target.value.trim() || st.preview.name;
    return save();
  }
  if (ev.target.matches("[data-activate]")) {
    st.activate = ev.target.checked;
    save();
  }
}
