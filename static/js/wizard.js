/** Plan erstellen: ein Bildschirm, alles vorbelegt, ein Knopf. Gefragt wird nach Erfahrung, Ziel, freien
 *  Tagen (die sind etwas anderes als die Einheiten pro Woche), gewünschten Einheiten und Dauer – die
 *  Empfehlung für die Einheiten kommt vom Server. Fokus und Beschwerden sind eingeklappt. */
import { $, api, closeSheet, esc, icon, openSheet, sheetClosed, toast } from "./util.js";

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
export const FOCUS = {
  brust: "Brust", ruecken: "Rücken", schultern: "Schultern", arme: "Arme",
  beine: "Beine", po: "Po", rumpf: "Bauch & Rumpf",
};
const INJURIES = ["Knie", "Rücken", "Schulter", "Ellbogen", "Handgelenk", "Hüfte"];
const MINUTES = [45, 60, 75, 90];
const MAX_FOCUS = 3;

/** Den alten Mehrschritt-Entwurf gibt es nicht mehr. */
export const hasWizardDraft = () => false;

const WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
// Gleiche Stichwörter wie der Server (app/planner/profile_model.py): so zeigt der Chip, was die App versteht.
const AREA_WORDS = {
  Knie: ["knie", "knee", "meniskus"], Rücken: ["rücken", "ruecken", "bandscheibe", "lws", "kreuz", "ischias"],
  Schulter: ["schulter"], Ellbogen: ["ellbogen", "ellenbogen", "tennisarm", "golfer"],
  Handgelenk: ["handgelenk"], Hüfte: ["hüfte", "huefte", "hüftgelenk"],
};
const areaOf = (entry) => Object.keys(AREA_WORDS).find((a) => AREA_WORDS[a].some((w) => entry.toLowerCase().includes(w)));

export function openWizard(profile) {
  if (!profile) {
    // Ohne Profil würde der Assistent mit Standardwerten arbeiten und echte Angaben überschreiben.
    toast("Das Profil konnte nicht geladen werden – bitte noch einmal versuchen.", { error: true });
    return;
  }
  const st = {
    // Profile von vor der Trennung haben noch keine freien Tage: dann die bisher geplanten vorwählen
    available: [...(profile.available_weekdays?.length ? profile.available_weekdays : profile.train_weekdays || [])],
    days: profile.days_per_week || 3,
    touched: false,            // Einheiten pro Woche von Hand gewählt? Dann ändert die Empfehlung sie nicht mehr
    experience: profile.experience || "einsteiger",
    goal: profile.goal || "aufbau",
    minutes: profile.session_minutes ?? 60,
    focus: [...(profile.focus || [])],
    limits: (profile.limitations || "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean),
    split: null,               // gewählte Aufteilung; null = die empfohlene
    sug: null, moreOpen: false, creating: false, seq: 0,
    clientId: crypto.randomUUID?.() || String(Date.now()),
  };
  const sheet = openSheet({ title: "Plan erstellen", body: "", wide: true });
  const body = $(".sheet-body", sheet);

  const chip = (attr, value, label, active, extra = "") => `<button type="button" class="chip${active ? " active" : ""}"
    ${attr}="${value}" aria-pressed="${active}" ${extra}>${label}</button>`;
  const maxDays = () => st.available.length || 7;
  const currentSplit = () => {
    const list = st.sug?.splits || [];
    return list.find((o) => o.key === st.split) || list.find((o) => o.recommended) || list[0] || null;
  };

  async function suggest() {
    const my = ++st.seq;                       // nur die neueste Antwort zählt
    const q = new URLSearchParams({ available: st.available.join(","), minutes: st.minutes ?? 0,
      goal: st.goal, experience: st.experience, focus: st.focus.join(",") });
    if (st.touched) q.set("days", st.days);
    try {
      const res = await api(`planner/suggest?${q}`);
      if (my !== st.seq) return;
      st.sug = res;
      if (!st.touched) st.days = res.recommended;
      if (st.days > maxDays()) st.days = maxDays();
      if (st.split && !res.splits.some((o) => o.key === st.split)) st.split = null;
    } catch { if (my !== st.seq) return; st.sug = null; }
    paint();
  }

  /** Aufteilungen für die gewählte Einheitenzahl: die empfohlene ist vorgewählt, die anderen sind Alternativen. */
  function splitChoice() {
    const list = st.sug?.splits || [];
    if (list.length < 2) return "";
    const current = currentSplit();
    return `<h3 class="form-section">Aufteilung</h3>
      <div class="chips">${list.map((o) => chip("data-split", o.key, `${esc(o.name)}${o.recommended ? " ✓" : ""}`, o.key === current.key)).join("")}</div>
      <p class="hint">${esc(current.text)} ${current.pro.map((x) => esc(x)).join(" ")}${current.contra.length ? ` Nachteil: ${esc(current.contra[0])}` : ""}</p>`;
  }

  function paint() {
    const free = st.available.length;
    const picked = (st.sug?.weekdays || []).map((d) => WD[d]).join(" · ");
    const others = st.limits.filter((x) => !areaOf(x));
    body.innerHTML = `<h3 class="form-section">Wie viel Erfahrung hast du?</h3>
      <div class="choices">${Object.entries(EXPERIENCE).map(([k, e]) => `<button type="button"
        class="choice${st.experience === k ? " active" : ""}" data-experience="${k}" aria-pressed="${st.experience === k}">
        <span class="choice-title">${esc(e.label)}</span><span class="choice-text">${esc(e.text)}</span></button>`).join("")}</div>
      <h3 class="form-section">Ziel</h3>
      <div class="chips">${Object.entries(GOALS).map(([k, g]) => chip("data-goal", k, g.label, st.goal === k)).join("")}</div>
      <h3 class="form-section">An welchen Tagen hast du Zeit?</h3>
      <div class="chips days">${WD.map((l, i) => chip("data-avail", i, l, st.available.includes(i))).join("")}</div>
      <p class="hint">${free === 1 ? "Bitte mindestens zwei Tage wählen – oder keinen, dann ist jeder Tag möglich."
        : free ? `${free} freie Tage – das heißt nicht, dass du an allen trainierst.`
        : "Keine Auswahl = jeder Tag ist möglich."}</p>
      <h3 class="form-section">Wie oft pro Woche willst du trainieren?</h3>
      <div class="chips big" role="group" aria-label="Einheiten pro Woche">${[2, 3, 4, 5, 6]
        .map((n) => chip("data-days", n, `${n}×`, st.days === n, n > maxDays() ? "disabled" : "")).join("")}</div>
      <p class="hint">${st.sug ? `Empfohlen: ${st.sug.recommended}× pro Woche. ` : ""}${picked ? `Trainingstage: ${picked}.` : ""}
        ${free > st.days ? "Die übrigen freien Tage bleiben als Reserve zum Nachholen." : ""}</p>
      ${splitChoice()}
      <h3 class="form-section">Dauer pro Einheit</h3>
      <div class="chips">${[...MINUTES, 0].map((m) => chip("data-minutes", m, m ? `${m} min` : "egal", st.minutes === m)).join("")}</div>
      <details class="field-more more" ${st.moreOpen ? "open" : ""}><summary>Mehr Aufmerksamkeit für Muskeln, Beschwerden</summary>
        <h3 class="form-section">Mehr Aufmerksamkeit für</h3>
        <div class="chips">${Object.entries(FOCUS).map(([k, l]) => chip("data-focus", k, l, st.focus.includes(k))).join("")}</div>
        <h3 class="form-section">Beschwerden</h3>
        <div class="chips">${INJURIES.map((l) => chip("data-injury", l, l, st.limits.some((x) => areaOf(x) === l))).join("")}</div>
        ${others.length ? `<div class="chips">${others.map((x, i) => `<button type="button" class="chip active" data-drop-limit="${i}"
          aria-label="${esc(x)} entfernen">${esc(x)} ✕</button>`).join("")}</div>
          <p class="hint">Diese Angaben kann die App keinem Bereich zuordnen – bitte die Übungen selbst prüfen.</p>` : ""}
        <p class="hint">Belastende Übungen werden weggelassen und durch passende ersetzt.</p>
      </details>
      <div class="btn-row wide"><button type="button" class="btn primary" data-create ${free === 1 || st.creating ? "disabled" : ""}>${icon("spark")} Plan erstellen</button></div>
      <p class="hint">Gewichte findest du in der ersten Einheit selbst – die App merkt sich, was passt und passt den Plan danach laufend an.</p>`;
  }

  async function create(btn) {
    if (st.creating) return;
    st.creating = true;
    paint();
    try {
      const shown = currentSplit();
      const res = await api("planner/create", { method: "POST", body: {
        days: st.days, available: st.available, goal: st.goal, experience: st.experience,
        minutes: st.minutes, focus: st.focus, limitations: st.limits.join(", "),
        split: shown?.key ?? null, client_id: st.clientId } });
      window.dispatchEvent(new CustomEvent("plan-created", { detail: { splitId: res.split.id } }));
      window.dispatchEvent(new CustomEvent("wizard-changed"));
      closeSheet();
      await sheetClosed();
      toast("Plan erstellt – er passt sich ab jetzt an");
      window.dispatchEvent(new CustomEvent("data-changed"));
      location.hash = "training/plan";
    } catch (e) {
      st.creating = false;
      paint();
      toast(e.offline ? "Zum Erstellen ist eine Verbindung nötig." : e.message, { error: true });
    }
  }

  sheet.addEventListener("toggle", (ev) => {
    if (ev.target.matches?.("details.more")) st.moreOpen = ev.target.open;
  }, true);
  sheet.addEventListener("click", (ev) => {
    if (st.creating) return;
    const t = (s) => ev.target.closest(s);
    const exp = t("[data-experience]");
    if (exp) { st.experience = exp.dataset.experience; paint(); return suggest(); }
    const goal = t("[data-goal]");
    if (goal) { st.goal = goal.dataset.goal; paint(); return suggest(); }
    const av = t("[data-avail]");
    if (av) {
      const d = Number(av.dataset.avail);
      st.available = st.available.includes(d) ? st.available.filter((x) => x !== d) : [...st.available, d].sort();
      paint();
      return suggest();
    }
    const days = t("[data-days]");
    if (days && !days.disabled) { st.days = Number(days.dataset.days); st.touched = true; st.split = null; paint(); return suggest(); }
    const split = t("[data-split]");
    if (split) { st.split = split.dataset.split; return paint(); }
    const min = t("[data-minutes]");
    if (min) { st.minutes = Number(min.dataset.minutes); paint(); return suggest(); }
    const focus = t("[data-focus]");
    if (focus) {
      const k = focus.dataset.focus;
      st.focus = st.focus.includes(k) ? st.focus.filter((x) => x !== k)
        : st.focus.length < MAX_FOCUS ? [...st.focus, k] : st.focus;
      paint();
      return suggest();           // der Fokus ändert die empfohlene Aufteilung
    }
    const inj = t("[data-injury]");
    if (inj) {
      const label = inj.dataset.injury;
      st.limits = st.limits.some((x) => areaOf(x) === label)
        ? st.limits.filter((x) => areaOf(x) !== label) : [...st.limits, label];
      return paint();
    }
    const drop = t("[data-drop-limit]");
    if (drop) {
      const others = st.limits.filter((x) => !areaOf(x));
      st.limits = st.limits.filter((x) => x !== others[Number(drop.dataset.dropLimit)]);
      return paint();
    }
    const go = t("[data-create]");
    if (go && !go.disabled) return create(go);
  });
  paint();
  suggest();
}
