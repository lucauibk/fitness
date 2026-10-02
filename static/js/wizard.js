/** Plan erstellen: ein Bildschirm, ein Tipp. Gefragt wird nur nach den Trainingstagen pro Woche,
 *  alles andere sind Voreinstellungen (Ziel, Dauer, Erfahrung, Split, Wochentage) und lässt sich
 *  unter „Anpassen“ ändern. Gewichte und Erfahrung lernt die App aus den ersten Einheiten. */
import { $, api, busy, closeSheet, esc, getProfile, icon, openSheet, toast } from "./util.js";

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

export function openWizard(profile) {
  const have = (profile?.train_weekdays || []).length;
  const st = {
    days: profile?.days_per_week || (have >= 2 ? have : 3),
    goal: profile?.goal || "aufbau",
    minutes: profile?.session_minutes ?? 60,
    focus: [...(profile?.focus || [])],
    limits: (profile?.limitations || "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean),
  };
  const sheet = openSheet({ title: "Plan erstellen", body: "", wide: true });
  const body = $(".sheet-body", sheet);

  const chip = (attr, value, label, active) => `<button type="button" class="chip${active ? " active" : ""}"
    ${attr}="${value}" aria-pressed="${active}">${label}</button>`;

  function paint() {
    body.innerHTML = `<p class="hint">Wie oft pro Woche willst du trainieren? Den Rest stellt die App passend ein
        und passt ihn nach jeder Einheit an.</p>
      <div class="chips big" role="group" aria-label="Trainingstage pro Woche">${[2, 3, 4, 5, 6]
        .map((n) => chip("data-days", n, `${n}×`, st.days === n)).join("")}</div>
      <div class="btn-row wide"><button type="button" class="btn primary" data-create>${icon("spark")} Plan erstellen</button></div>
      <details class="field-more"><summary>Anpassen (optional)</summary>
        <h3 class="form-section">Ziel</h3>
        <div class="chips">${Object.entries(GOALS).map(([k, g]) => chip("data-goal", k, g.label, st.goal === k)).join("")}</div>
        <h3 class="form-section">Dauer pro Einheit</h3>
        <div class="chips">${[...MINUTES, 0].map((m) => chip("data-minutes", m, m ? `${m} min` : "egal", st.minutes === m)).join("")}</div>
        <h3 class="form-section">Mehr Aufmerksamkeit für</h3>
        <div class="chips">${Object.entries(FOCUS).map(([k, l]) => chip("data-focus", k, l, st.focus.includes(k))).join("")}</div>
        <h3 class="form-section">Beschwerden</h3>
        <div class="chips">${INJURIES.map((l) => chip("data-injury", l, l, st.limits.some((x) => x.toLowerCase() === l.toLowerCase()))).join("")}</div>
        <p class="hint">Belastende Übungen werden weggelassen und durch passende ersetzt.</p>
      </details>
      <p class="hint">Startgewichte findest du in der ersten Einheit selbst – die App merkt sich, was passt.</p>`;
  }

  async function create(btn) {
    await busy(btn, async () => {
      try {
        const limitations = st.limits.join(", ") || null;
        await api(`profiles/${getProfile()}`, { method: "PATCH", body: {
          goal: st.goal, session_minutes: st.minutes || null, focus: st.focus, limitations } });
        await api("planner/create", { method: "POST", body: {
          days: st.days, goal: st.goal, minutes: st.minutes, focus: st.focus } });
        window.dispatchEvent(new CustomEvent("wizard-changed"));
        closeSheet();
        toast("Plan erstellt – er passt sich ab jetzt an");
        window.dispatchEvent(new CustomEvent("data-changed"));
        location.hash = "training/plan";
      } catch (e) {
        toast(e.offline ? "Zum Erstellen ist eine Verbindung nötig." : e.message, { error: true });
      }
    });
  }

  sheet.addEventListener("click", (ev) => {
    const t = (s) => ev.target.closest(s);
    const days = t("[data-days]");
    if (days) { st.days = Number(days.dataset.days); return paint(); }
    const goal = t("[data-goal]");
    if (goal) { st.goal = goal.dataset.goal; return paint(); }
    const min = t("[data-minutes]");
    if (min) { st.minutes = Number(min.dataset.minutes); return paint(); }
    const focus = t("[data-focus]");
    if (focus) {
      const k = focus.dataset.focus;
      st.focus = st.focus.includes(k) ? st.focus.filter((x) => x !== k)
        : st.focus.length < MAX_FOCUS ? [...st.focus, k] : st.focus;
      return paint();
    }
    const inj = t("[data-injury]");
    if (inj) {
      const label = inj.dataset.injury;
      st.limits = st.limits.some((x) => x.toLowerCase() === label.toLowerCase())
        ? st.limits.filter((x) => x.toLowerCase() !== label.toLowerCase()) : [...st.limits, label];
      return paint();
    }
    const go = t("[data-create]");
    if (go) return create(go);
  });
  paint();
}
