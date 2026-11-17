/** Profil: Kopf, Ziele (Training, Ernährung) und eine Liste; Formulare nur in Sheets. */
import { $, actionMenu, api, closeSheet, esc, formData, formError, getProfile, icon, openSheet, prefs, safeColor, skeletonCards,
  submitOnce, toast, WEEKDAYS, parseYmd, ymd } from "./util.js";
import { openTopf } from "./topf.js";
import { openWizard, GOALS, EXPERIENCE, FOCUS, hasWizardDraft } from "./wizard.js";
import { openFastAssistant } from "./ernaehrung.js";

let root;
let data = null;

const option = (value, label, current) =>
  `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`;

function summary(p) {
  const parts = [
    p.goal ? GOALS[p.goal].label : null,
    p.train_weekdays?.length ? p.train_weekdays.map((d) => WEEKDAYS[d]).join(" · ")
      : p.days_per_week ? `${p.days_per_week} Tage pro Woche` : null,
    p.experience ? EXPERIENCE[p.experience].label : null,
    p.focus?.length ? `Fokus: ${p.focus.map((f) => FOCUS[f]).join(", ")}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Noch nicht festgelegt – der Assistent fragt es ab.";
}

const ACT = {
  niedrig: ["Wenig", "Bürojob, überwiegend sitzend"],
  mittel: ["Mittel", "Viel auf den Beinen, z. B. Verkauf, Pflege, Handwerk leicht"],
  hoch: ["Hoch", "Körperliche Arbeit, den ganzen Tag in Bewegung"],
};

const kcalText = (v) => `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toLocaleString("de-AT")}`;
const comma = (v) => String(v).replace(".", ",");

const fact = (value, label) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`;

const num = (v) => Number(v).toLocaleString("de-AT", { maximumFractionDigits: 0 });
const liters = (ml) => comma(Math.round(ml / 50) * 50 / 1000);

/** Eiweiß zuerst, dann Fett und Kohlenhydrate – Farben je Nährstoff aus der Ernährungs-Ansicht (.is-*). */
function macroStats(g) {
  const tile = (cls, label, v, lead = false) => `<div class="stat ${cls}${lead ? " lead" : ""}">
    <b>${v ?? "–"}<small> g</small></b><span>${label}</span></div>`;
  return `<div class="stats goal-stats">${tile("is-protein", "Eiweiß", g.protein, true)}
    ${tile("is-fat", "Fett", g.fat)}${tile("is-carbs", "Kohlenhydrate", g.carbs)}</div>`;
}

/** Rechenweg als eine Zeile: Grundumsatz × Alltag + Training ± Ziel = kcal (Zahlen vom Server). */
function calcLine(c) {
  const b = c.breakdown;
  if (!b) return "";
  return `<p class="hint calc-line">Grundumsatz ${num(b.bmr)} × Alltag ${comma(b.pal)} + Training ${num(b.training_kcal)}
    ${b.adjustment ? `${kcalText(b.adjustment)} Ziel ` : ""}= ${num(c.kcal)} kcal${b.floor_applied ? " (Untergrenze)" : ""}</p>`;
}

function nutritionCard() {
  const n = data.nutrition;
  const g = n.goals || {};
  const c = n.computed;
  const w = n.water;
  return `<div class="card nutri-goal">
    <div class="card-head">${icon("food")}<span class="card-title">Ernährungsziel</span>
      <span class="aside">${n.auto ? "automatisch berechnet" : "eigene Werte"}</span></div>
    ${g.kcal ? `<div class="goal-kcal"><b>${num(g.kcal)}</b><span>kcal pro Tag</span></div>
      ${macroStats(g)}
      ${w.goal ? `<p class="hint goal-water">${icon("water")} Trinkziel ${liters(w.goal)} L</p>` : ""}
      ${c && n.auto ? calcLine(c) : ""}`
    : `<p class="hint">Für eine Berechnung fehlen: ${esc((n.missing || []).join(", "))}. Der Assistent fragt sie ab.</p>`}
    <div class="btn-row"><button type="button" class="btn primary" data-goal-wizard>${g.kcal ? "Ziel neu einstellen" : "Ziel einstellen"}</button></div>
    <details class="switch-card profil-settings">
      <summary>Eigene Werte und Trinkziel ${icon("down", "chev")}</summary>
      <form id="nutriForm" class="form">
        <div class="toggles"><label class="toggle"><input type="checkbox" name="nutrition_auto" ${n.auto ? "checked" : ""}>
          <span></span>Kalorien und Makros automatisch berechnen</label></div>
        <div class="field-row" data-manual ${n.auto ? "hidden" : ""}>
          <label class="field">kcal<input name="kcal_goal" type="number" inputmode="numeric" min="800" max="8000" value="${g.kcal ?? ""}"></label>
          <label class="field">Eiweiß (g)<input name="protein_goal" type="number" inputmode="numeric" min="0" max="500" value="${g.protein ?? ""}"></label>
        </div>
        <div class="field-row" data-manual ${n.auto ? "hidden" : ""}>
          <label class="field">Kohlenh. (g)<input name="carbs_goal" type="number" inputmode="numeric" min="0" max="1500" value="${g.carbs ?? ""}"></label>
          <label class="field">Fett (g)<input name="fat_goal" type="number" inputmode="numeric" min="0" max="500" value="${g.fat ?? ""}"></label>
        </div>
        ${w.auto ? `<p class="hint">${w.from_weight ? "Berechnet aus Gewicht und Aktivität" : "Richtwert, solange kein Körpergewicht eingetragen ist"}: ${liters(w.computed)} L pro Tag.</p>` : ""}
        <div class="toggles"><label class="toggle"><input type="checkbox" name="water_auto" ${w.auto ? "checked" : ""}>
          <span></span>Trinkziel automatisch berechnen</label></div>
        <label class="field" data-water-manual ${w.auto ? "hidden" : ""}>Trinkziel (ml)
          <input name="water_goal_ml" type="number" inputmode="numeric" min="500" max="8000" step="50" value="${w.goal ?? ""}"></label>
        <div class="btn-row"><button type="submit" class="btn">Speichern</button></div>
      </form>
    </details>
  </div>`;
}

const fastLabel = (p) => (p.fasting_kind === "custom" ? `${comma(p.fasting_hours)} h ohne Essen` : p.fasting_kind);

// ------------------------------------------------- Ernährungsziel-Assistent
// Daten -> Aktivität -> Ziel -> Tempo (nur Abnehmen/Aufbau) -> Ergebnis. Gerechnet wird auf dem Server
// (app/nutrition.py computed_goals); der Assistent fragt, zeigt den Rechenweg und speichert erst am Ende.
const PACE_NAMES = { sanft: "Sanft", standard: "Standard", zuegig: "Zügig" };
const PACE_TEXT = {
  abnehmen: { sanft: "Kleines Defizit, leichter durchzuhalten.", standard: "Etwa 0,5 % des Körpergewichts pro Woche – der übliche Richtwert.",
    zuegig: "Größeres Defizit: schneller, aber anstrengender." },
  aufbau: { sanft: "Kleiner Überschuss, wenig Fettzuwachs nebenbei.", standard: "Etwa 10 % über deinem Verbrauch – der übliche Richtwert.",
    zuegig: "Größerer Überschuss: schneller, aber mehr Fett dabei." },
};
const hasPace = (goal) => goal === "abnehmen" || goal === "aufbau";
const stepsFor = (goal) => (hasPace(goal) ? ["data", "activity", "goal", "pace", "result"] : ["data", "activity", "goal", "result"]);

function openGoalAssistant() {
  const p = data.profile;
  const st = { i: 0, busy: false, prev: null,
    activity: p.activity_level || null, goal: p.goal || null, pace: p.nutrition_pace || "standard" };
  const sheet = openSheet({ title: "Ernährungsziel einstellen", body: "", footer: " " });
  const body = $(".sheet-body", sheet);
  const foot = $(".sheet-foot", sheet);
  body.classList.add("goal-assist");   // Bereichs-CSS (fasten.css) nur fuer dieses Sheet
  const steps = () => stepsFor(st.goal);
  const step = () => steps()[st.i];
  const year = new Date().getFullYear();

  const bar = () => `<div class="steps-bar" role="img" aria-label="Schritt ${st.i + 1} von ${steps().length}">${
    steps().map((_, i) => `<i${i <= st.i ? ' class="on"' : ""}></i>`).join("")}</div>
    <h3 class="form-section">Schritt ${st.i + 1} von ${steps().length}</h3>`;
  const back = () => (st.i ? `<button type="button" class="btn ghost" data-back>Zurück</button>` : "");
  const next = (label, ok = true) => `<button type="button" class="btn primary" data-next ${ok && !st.busy ? "" : "disabled"}>${label}</button>`;
  const choice = (attr, key, title, text, on) => `<button type="button" class="choice" ${attr}="${key}" aria-pressed="${on}">
    <b>${esc(title)}</b><small>${esc(text)}</small></button>`;

  function paint() {
    const kind = step();
    if (kind === "data") {
      body.innerHTML = `${bar()}<p>Daraus rechnet die App deinen Grundumsatz – den Kalorienbedarf in Ruhe.</p>
        <form id="goalForm" class="form" novalidate>
          <label class="field">Geschlecht
            <select name="sex">${[["", "keine Angabe"], ["weiblich", "weiblich"], ["männlich", "männlich"], ["divers", "divers"]]
              .map(([v, l]) => `<option value="${v}"${(p.sex || "") === v ? " selected" : ""}>${l}</option>`).join("")}</select></label>
          <div class="field-row">
            <label class="field">Geburtsjahr<input name="birth_year" type="number" inputmode="numeric" min="1920" max="${year - 6}"
              required value="${p.birth_year ?? ""}"></label>
            <label class="field">Größe (cm)<input name="height_cm" type="number" inputmode="decimal" min="100" max="250" step="0.5"
              required value="${p.height_cm ?? ""}"></label>
            <label class="field">Gewicht (kg)<input name="weight" type="number" inputmode="decimal" min="30" max="300" step="0.1"
              required value="${data.weight?.kg ?? ""}"></label>
          </div></form>
        <p class="hint">Ohne Geschlechtsangabe rechnet die App mit dem Mittelwert beider Formeln. Das Gewicht wird als heutiger Eintrag im Tab Körper gespeichert.</p>`;
      foot.innerHTML = `<button type="submit" form="goalForm" class="btn primary" ${st.busy ? "disabled" : ""}>Weiter</button>`;
      const form = $("#goalForm", body);
      submitOnce(form, () => saveData(form));
    } else if (kind === "activity") {
      body.innerHTML = `${bar()}<p>Wie aktiv bist du im Alltag – ohne dein Krafttraining? Das Training rechnet die App getrennt dazu.</p>
        <div class="choice-grid goal-choices">${Object.entries(ACT).map(([k, [t, d]]) => choice("data-activity", k, t, d, st.activity === k)).join("")}</div>`;
      foot.innerHTML = `${back()}${next("Weiter", Boolean(st.activity))}`;
    } else if (kind === "goal") {
      body.innerHTML = `${bar()}<p>Was willst du erreichen?</p>
        <div class="choice-grid goal-choices">${Object.entries(GOALS).map(([k, g]) => choice("data-goal", k, g.label, g.text, st.goal === k)).join("")}</div>
        <p class="hint">Das Ziel gilt auch für deinen Trainingsplan und den Coach – es ist dasselbe wie im Plan-Assistenten.</p>`;
      foot.innerHTML = `${back()}${next("Weiter", Boolean(st.goal))}`;
    } else if (kind === "pace") {
      const paces = st.prev?.paces || {};
      body.innerHTML = `${bar()}<p>${st.goal === "abnehmen" ? "Wie schnell willst du abnehmen?" : "Wie schnell willst du aufbauen?"}</p>
        <div class="choice-grid goal-choices">${Object.keys(PACE_NAMES).map((k) => {
          const o = paces[k];
          const detail = !o ? "" : st.goal === "abnehmen"
            ? `${num(o.kcal)} kcal · etwa ${comma(o.weekly_kg)} kg pro Woche${o.floor_applied ? " (Untergrenze)" : ""}`
            : `${num(o.kcal)} kcal · ${kcalText(o.adjustment)} kcal`;
          return choice("data-pace", k, `${PACE_NAMES[k]}${k === "standard" ? " (empfohlen)" : ""}`, `${detail ? `${detail}. ` : ""}${PACE_TEXT[st.goal][k]}`, st.pace === k);
        }).join("")}</div>
        <p class="hint">Die kg-Angabe ist eine Faustformel (7.700 kcal je kg Körperfett) und kein Versprechen – der Körper reagiert nicht linear.</p>`;
      foot.innerHTML = `${back()}${next("Ergebnis ansehen", Object.keys(paces).length > 0)}`;
    } else paintResult();
    body.scrollTo?.({ top: 0 });
  }

  function paintResult() {
    const c = st.prev?.computed;
    if (!c) {
      body.innerHTML = `${bar()}<p>Für eine Berechnung fehlen noch: ${esc((st.prev?.missing || []).join(", "))}.</p>`;
      foot.innerHTML = back();
      return;
    }
    const b = c.breakdown;
    const wanted = Math.round((c.tdee + b.adjustment) / 10) * 10;
    const basis = b.body_fat ? `fettfreier Masse (${comma(b.protein_basis_kg)} kg)` : `Körpergewicht (${comma(b.protein_basis_kg)} kg)`;
    const manual = !data.nutrition.auto;
    body.innerHTML = `${bar()}
      <div class="goal-kcal"><b>${num(c.kcal)}</b><span>kcal pro Tag im Wochenmittel</span></div>
      <p class="hint">Trainingstag ${num(c.by_day.training.kcal)} kcal · Ruhetag ${num(c.by_day.rest.kcal)} kcal</p>
      <h3 class="form-section">So kommt das zustande</h3>
      <dl class="calc">
        <div><dt>Grundumsatz (${esc(b.formula)}${b.body_fat ? `, Körperfett ${comma(b.body_fat)} %` : ""})</dt><dd>${num(b.bmr)} kcal</dd></div>
        <div><dt>× Alltag</dt><dd>× ${comma(b.pal)} = ${num(b.daily_kcal)} kcal</dd></div>
        <div><dt>+ Krafttraining (Ø pro Tag, ${b.sessions}× pro Woche)</dt><dd>+ ${num(b.training_kcal)} kcal</dd></div>
        <div class="sum"><dt>= Gesamtumsatz</dt><dd>${num(c.tdee)} kcal</dd></div>
        <div><dt>${b.adjustment < 0 ? "− Defizit" : b.adjustment > 0 ? "+ Überschuss" : "Zu-/Abschlag"}</dt><dd>${b.adjustment ? kcalText(b.adjustment) : "±0"} kcal</dd></div>
        ${b.trend_adjust ? `<div class="sub"><dt>davon Anpassung an deinen Gewichtsverlauf (${comma(b.trend_rate)} kg/Woche)</dt><dd>${kcalText(b.trend_adjust)} kcal</dd></div>` : ""}
        <div class="sum"><dt>= Ziel</dt><dd>${num(c.kcal)} kcal</dd></div>
      </dl>
      ${b.floor_applied ? `<div class="callout">${icon("warn")}<span>Unter ${num(b.floor)} kcal pro Tag setzt die App kein Ziel – ohne diese Untergrenze
        wären es ${num(wanted)} kcal. Wenn du schneller abnehmen willst, wähle lieber mehr Bewegung als weniger Essen.</span></div>` : ""}
      <h3 class="form-section">Makros – Eiweiß zuerst</h3>
      ${macroStats(c)}
      <p class="hint">Eiweiß: ${comma(b.protein_per_kg)} g je kg ${basis}${b.protein_capped ? ", begrenzt auf 35 % der Kalorien" : ""}.
        Fett: mindestens 0,8 g je kg und rund ein Viertel der Kalorien. Kohlenhydrate sind der Rest.</p>
      <div class="stats goal-stats"><div class="stat"><b>${liters(st.prev.water_ml)} L</b><span>Trinkziel (Richtwert)</span></div></div>
      ${data.nutrition.water.auto ? "" : `<p class="hint">Dein eigenes Trinkziel (${liters(data.nutrition.water.goal)} L) bleibt unverändert.</p>`}
      ${manual ? `<p class="hint">Du hattest eigene Werte eingestellt. Mit „Übernehmen“ rechnet die App wieder automatisch.</p>` : ""}
      <p class="hint">Eine Schätzung, kein ärztlicher Rat. Prüfe nach 2–3 Wochen am Gewichtsverlauf, ob es passt.</p>`;
    foot.innerHTML = `${back()}<button type="button" class="btn primary" data-apply ${st.busy ? "disabled" : ""}>Übernehmen</button>`;
  }

  /** Vorschau vom Server: fuer "pace" ohne Tempo (liefert alle drei), fuer "result" mit dem gewaehlten. */
  async function fetchPreview(kind) {
    const q = new URLSearchParams({ goal: st.goal, activity_level: st.activity });
    if (hasPace(st.goal) && kind === "result") q.set("pace", st.pace);
    st.prev = await api(`nutrition/goals/preview?${q}`);
  }

  async function saveData(form) {
    // Browser-Pruefung (min/max/required) von Hand: das Formular hat novalidate, damit die Meldung deutsch im Formular steht
    if (!form.checkValidity()) {
      const bad = form.querySelector(":invalid");
      return formError(form, new Error(`Bitte ${bad.closest("label")?.firstChild?.textContent?.trim() || "alle Felder"} prüfen.`));
    }
    const v = formData(form);
    const patch = {};
    const sex = v.sex ?? null;
    if (sex !== (p.sex ?? null)) patch.sex = sex;
    if (v.birth_year !== p.birth_year) patch.birth_year = v.birth_year;
    if (v.height_cm !== p.height_cm) patch.height_cm = v.height_cm;
    try {
      st.busy = true;
      if (Object.keys(patch).length) {
        data.profile = await api(`profiles/${getProfile()}`, { method: "PATCH", body: patch });
        Object.assign(p, data.profile);
        window.dispatchEvent(new CustomEvent("profile-changed", { detail: data.profile }));
      }
      if (v.weight !== data.weight?.kg) {
        await api("bodyweight", { method: "POST", body: { profile_id: getProfile(), kg: v.weight } });
        data.weight = { kg: v.weight, date: ymd(new Date()) };
      }
      st.i = 1;
      st.busy = false;
      paint();
    } catch (e) {
      st.busy = false;
      formError(form, e.offline ? new Error("Dafür ist eine Verbindung nötig.") : e);
    }
  }

  async function guarded(fn) {
    if (st.busy) return;
    st.busy = true;
    paint();
    try { await fn(); } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
    finally { st.busy = false; if (sheet.isConnected) paint(); }
  }

  sheet.addEventListener("click", (ev) => {
    const t = ev.target;
    const set = (attr, key) => { const el = t.closest(`[${attr}]`); if (el) { st[key] = el.getAttribute(attr); paint(); } return Boolean(el); };
    if (set("data-activity", "activity") || set("data-goal", "goal") || set("data-pace", "pace")) return;
    if (t.closest("[data-back]")) { st.i -= 1; return paint(); }
    if (t.closest("[data-next]")) {
      return guarded(async () => {
        const kind = steps()[st.i + 1];
        if (kind === "pace" || kind === "result") await fetchPreview(kind);
        st.i += 1;
      });
    }
    if (t.closest("[data-apply]")) {
      return guarded(async () => {
        const saved = await api(`profiles/${getProfile()}`, { method: "PATCH", body: {
          activity_level: st.activity, goal: st.goal, nutrition_pace: hasPace(st.goal) ? st.pace : null, nutrition_auto: true } });
        window.dispatchEvent(new CustomEvent("profile-changed", { detail: saved }));
        closeSheet();
        toast("Ernährungsziel gespeichert");
        data = null;
        await load();
      });
    }
  });
  paint();
}

const row = (ic, title, meta, attr) => `<div class="row-item" role="button" tabindex="0" ${attr}>
  <span class="row-icon">${icon(ic)}</span>
  <div class="row-main"><div class="row-title">${title}</div><div class="row-meta"><span>${meta}</span></div></div>
  ${icon("right", "chev")}</div>`;

/** Profilseite in drei Teilen: Kopf, Ziele (Training, Ernährung) und eine Liste. Formulare gibt es nur
 *  noch in Sheets – die Seite selbst hat kein Speichern mehr. */
function render() {
  const { profile: p, weight } = data;
  const year = new Date().getFullYear();
  const n = data.nutrition;
  const g = n.goals || {};
  root.innerHTML = `
    <div class="card profile-head">
      <div class="profile-id">
        <span class="avatar" style="--c:${safeColor(p.color)}" aria-hidden="true">${esc((p.name || "?").trim().charAt(0).toUpperCase())}</span>
        <div class="grow"><h2 class="profile-name">${esc(p.name)}</h2>
          <p class="hint">${p.goal ? esc(GOALS[p.goal].label) : "Ziel offen"}${p.experience ? ` · ${esc(EXPERIENCE[p.experience].label)}` : ""}</p></div>
        <button type="button" class="icon-btn" data-edit-data aria-label="Meine Daten bearbeiten">${icon("edit")}</button>
      </div>
      <div class="stats profil-stats">
        ${fact(p.height_cm ? `${String(p.height_cm).replace(".", ",")} cm` : "–", "Größe")}
        ${fact(p.birth_year ? `${year - p.birth_year} J.` : "–", "Alter")}
        <a class="stat" href="#fortschritt/koerper"><b>${weight ? `${String(weight.kg).replace(".", ",")} kg` : "–"}</b>
          <span>${weight ? parseYmd(weight.date).toLocaleDateString("de-AT", { day: "numeric", month: "numeric" }) : "Gewicht eintragen"}</span></a>
      </div>
    </div>

    <h3 class="section-title">Ziele</h3>
    <div class="list profil-list">
      ${data.hasPlan
        ? row("dumbbell", "Training", esc(summary(p)), "data-wizard")
        : row("spark", "Training: Plan erstellen lassen", "Nur die Trainingstage wählen – den Rest stellt die App passend ein.", "data-wizard")}
      ${row("food", "Ernährung", g.kcal ? `${num(g.kcal)} kcal · Eiweiß ${g.protein ?? "–"} g${n.water?.goal ? ` · Trinken ${liters(n.water.goal)} L` : ""}`
        : "Noch kein Ziel – der Assistent rechnet es aus.", "data-nutrition")}
    </div>

    <h3 class="section-title">Mehr</h3>
    <div class="list profil-list">
      ${row("clock", "Intervallfasten", p.fasting_kind ? `${esc(fastLabel(p))} · Timer im Tab Ernährung` : "aus", "data-fasting")}
      ${row("bag", "Shopping-Topf", p.reward_per_workout ? `${String(p.reward_per_workout).replace(".", ",")} € pro Training · Wunschliste`
        : "Jedes Training bringt Geld für deine Wunschliste.", "data-topf")}
      ${row("user", "Profile wechseln und verwalten", "Name, Farbe, weitere Profile", "data-profiles")}
      ${row("download", "Daten exportieren", "CSV für Excel oder Numbers", "data-export")}
    </div>`;
}

/** "Deine Daten" als Sheet statt als Formular am Seitenende. */
function openDataSheet() {
  const p = data.profile;
  const year = new Date().getFullYear();
  const sheet = openSheet({ title: "Meine Daten", body: `<form id="profilForm" class="form">
      <label class="field">Name<input name="name" maxlength="40" required value="${esc(p.name)}"></label>
      <div class="field-row">
        <label class="field">Geburtsjahr<input name="birth_year" type="number" inputmode="numeric"
          min="1920" max="${year - 6}" value="${p.birth_year ?? ""}" placeholder="optional"></label>
        <label class="field">Größe (cm)<input name="height_cm" type="number" inputmode="decimal"
          min="100" max="250" step="0.5" value="${p.height_cm ?? ""}" placeholder="optional"></label>
      </div>
      <label class="field">Geschlecht
        <select name="sex">
          ${option("", "keine Angabe", p.sex || "")}
          ${option("weiblich", "weiblich", p.sex)}
          ${option("männlich", "männlich", p.sex)}
          ${option("divers", "divers", p.sex)}
        </select>
      </label>
      <p class="form-section">Für Coach und Plan</p>
      <label class="field">Was dich motiviert – warum du trainierst
        <textarea name="motivation" rows="3" maxlength="300"
          placeholder="optional, z. B. Ziel-Outfit, mehr Energie im Alltag, Rücken stärken">${esc(p.motivation || "")}</textarea>
        <span class="hint">Das liest nur dein Coach – er bezieht sich darauf statt auf Druck.</span>
      </label>
      <label class="field">Einschränkungen oder Verletzungen
        <textarea name="limitations" rows="2" maxlength="500"
          placeholder="optional, z. B. Knie links, keine Überkopf-Übungen">${esc(p.limitations || "")}</textarea>
        <span class="hint">Stichwörter wie Knie, Rücken, Schulter oder Ellbogen berücksichtigt der
          Assistent direkt bei der Übungswahl; alles andere nur die KI-Verfeinerung.</span>
      </label>
    </form>`,
    footer: `<button type="submit" class="btn primary" form="profilForm">Speichern</button>` });
  const form = $("#profilForm", sheet);
  submitOnce(form, async () => {
    try {
      const saved = await api(`profiles/${getProfile()}`, { method: "PATCH", body: formData(form) });
      data.profile = saved;
      window.dispatchEvent(new CustomEvent("profile-changed", { detail: saved }));
      closeSheet();
      toast("Profil gespeichert");
      render();
    } catch (e) { formError(form, e); }
  });
}

/** Ernährungsziel: Anzeige, Assistent und die "eigenen Werte" – alles in einem Sheet. */
function openNutritionSheet() {
  const sheet = openSheet({ title: "Ernährungsziel", body: nutritionCard() });
  const nf = $("#nutriForm", sheet);
  nf.nutrition_auto.addEventListener("change", () => {
    nf.querySelectorAll("[data-manual]").forEach((el) => { el.hidden = nf.nutrition_auto.checked; });
  });
  nf.water_auto.addEventListener("change", () => {
    nf.querySelectorAll("[data-water-manual]").forEach((el) => { el.hidden = nf.water_auto.checked; });
  });
  sheet.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-goal-wizard]")) openGoalAssistant();
  });
  submitOnce(nf, async () => {
    try {
      const body = formData(nf);
      if (body.nutrition_auto) ["kcal_goal", "protein_goal", "carbs_goal", "fat_goal"].forEach((k) => delete body[k]);
      if (body.water_auto) body.water_goal_ml = null;
      else if (body.water_goal_ml == null) return formError(nf, new Error("Trinkziel: bitte einen Wert eintragen."));
      delete body.water_auto;
      await api(`profiles/${getProfile()}`, { method: "PATCH", body });
      closeSheet();
      toast("Ernährungsziel gespeichert");
      await load();
    } catch (e) { formError(nf, e); }
  });
}

function openFastingMenu() {
  const on = Boolean(data.profile.fasting_kind);
  actionMenu("Intervallfasten", [
    { label: on ? "Fastenart ändern" : "Welche Fastenart passt zu mir?", icon: "clock",
      run: () => openFastAssistant({ onDone: () => { load(); } }) },
    on && { label: "Zur Fasten-Ansicht", icon: "right", run: () => { prefs.set("nutriView", "fasten"); location.hash = "ernaehrung"; } },
    on && { label: "Fasten ausschalten", icon: "x", danger: true, run: turnFastingOff },
  ]);
}

function openExport() {
  openSheet({ title: "Daten exportieren", body: `<p class="hint">Als CSV für Excel oder Numbers – nur die Daten dieses Profils.</p>
    <div class="chips export-chips">${[["saetze", "Sätze"], ["cardio", "Cardio"], ["koerper", "Körper"], ["ernaehrung", "Ernährung"]]
      .map(([k, l]) => `<a class="btn small tonal" href="/api/export/${k}.csv?profile=${getProfile()}" download>${icon("download")} ${l}</a>`).join("")}</div>` });
}

async function turnFastingOff() {
  try {
    const saved = await api(`profiles/${getProfile()}`, { method: "PATCH", body: { fasting_kind: null } });
    window.dispatchEvent(new CustomEvent("profile-changed", { detail: saved }));
    toast("Fasten ausgeschaltet");
    await load();
  } catch (e) { toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true }); }
}

export async function load() {
  if (!data) root.innerHTML = skeletonCards([150, 130, 200]);
  const [profile, bw, nutrition, splits] = await Promise.all([api(`profiles/${getProfile()}`),
    api("bodyweight"), api("nutrition/goals"), api("splits").catch(() => [])]);
  data = { profile, weight: bw[bw.length - 1] || null, nutrition, hasPlan: splits.some((s) => s.active) };
  render();
}

export async function mount(el) {
  root = el;
  data = null;   // frisch (z. B. nach Profilwechsel)
  el.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-wizard]")) openWizard(data?.profile);
    if (ev.target.closest("[data-profiles]")) document.getElementById("profileBtn")?.click();
    if (ev.target.closest("[data-topf]")) openTopf();
    if (ev.target.closest("[data-nutrition]")) openNutritionSheet();
    if (ev.target.closest("[data-edit-data]")) openDataSheet();
    if (ev.target.closest("[data-fasting]")) openFastingMenu();
    if (ev.target.closest("[data-export]")) openExport();
  });
}

// Einmal pro Seite (mount laeuft nach einem Profilwechsel erneut)
// Der Assistent speichert Ziel und Zeit ins Profil – Zusammenfassung dann frisch laden.
window.addEventListener("wizard-changed", () => {
  if (data && root?.isConnected && !root.hidden) load().catch(() => render());
});
