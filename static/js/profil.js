/** Profil: persönliche Angaben und der Einstieg in den Plan-Assistenten. */
import { $, api, esc, formData, formError, getProfile, hasDirtyForm, icon, skeleton, submitOnce, toast,
  WEEKDAYS, parseYmd } from "./util.js";
import { openWizard, GOALS, EXPERIENCE, FOCUS, hasWizardDraft } from "./wizard.js";

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

const ACT = { niedrig: "wenig (Bürojob)", mittel: "mittel (viel auf den Beinen)", hoch: "hoch (körperliche Arbeit)" };

function nutritionCard() {
  const n = data.nutrition;
  const g = n.goals || {};
  const c = n.computed;
  const w = n.water;
  const liters = (ml) => String(Math.round(ml / 50) * 50 / 1000).replace(".", ",");
  return `<div class="card">
    <p class="eyebrow">Ernährungsziel</p>
    ${g.kcal ? `<p><b>${g.kcal} kcal</b> · Eiweiß ${g.protein} g · Kohlenhydrate ${g.carbs} g · Fett ${g.fat} g</p>`
      : `<p class="hint">Für eine Berechnung fehlen: ${esc((n.missing || []).join(", "))}.</p>`}
    ${c && n.auto ? `<p class="hint">Berechnet aus Gewicht ${String(c.weight).replace(".", ",")} kg, Größe, Alter, Aktivität und Ziel
      (Gesamtumsatz ≈ ${c.tdee} kcal).</p>` : ""}
    <form id="nutriForm" class="form">
      <label class="field">Alltags-Aktivität
        <select name="activity_level">
          <option value="">aus den Trainingstagen schätzen</option>
          ${Object.entries(ACT).map(([k, l]) => `<option value="${k}"${n.activity_level === k ? " selected" : ""}>${l}</option>`).join("")}
        </select>
      </label>
      <div class="toggles"><label class="toggle"><input type="checkbox" name="nutrition_auto" ${n.auto ? "checked" : ""}>
        <span></span>Ziele automatisch berechnen</label></div>
      <div class="field-row" data-manual ${n.auto ? "hidden" : ""}>
        <label class="field">kcal<input name="kcal_goal" type="number" inputmode="numeric" min="800" max="8000" value="${g.kcal ?? ""}"></label>
        <label class="field">Eiweiß (g)<input name="protein_goal" type="number" inputmode="numeric" min="0" max="500" value="${g.protein ?? ""}"></label>
      </div>
      <div class="field-row" data-manual ${n.auto ? "hidden" : ""}>
        <label class="field">Kohlenh. (g)<input name="carbs_goal" type="number" inputmode="numeric" min="0" max="1500" value="${g.carbs ?? ""}"></label>
        <label class="field">Fett (g)<input name="fat_goal" type="number" inputmode="numeric" min="0" max="500" value="${g.fat ?? ""}"></label>
      </div>
      <p class="eyebrow">Trinkziel</p>
      ${w.auto ? `<p class="hint">${w.from_weight ? "Berechnet aus Gewicht und Aktivität" : "Richtwert, solange kein Körpergewicht eingetragen ist"}: ${liters(w.computed)} L pro Tag.</p>` : ""}
      <div class="toggles"><label class="toggle"><input type="checkbox" name="water_auto" ${w.auto ? "checked" : ""}>
        <span></span>Trinkziel automatisch berechnen</label></div>
      <label class="field" data-water-manual ${w.auto ? "hidden" : ""}>Trinkziel (ml)
        <input name="water_goal_ml" type="number" inputmode="numeric" min="500" max="8000" step="50" value="${w.goal ?? ""}"></label>
      <div class="btn-row"><button type="submit" class="btn">Ziel speichern</button></div>
    </form>
  </div>`;
}

function render() {
  const { profile: p, weight } = data;
  const year = new Date().getFullYear();
  const draft = hasWizardDraft();
  // Grosser Einstieg nur, solange es noch keinen Plan gibt – danach reicht eine Zeile
  // unter dem Trainingsziel (der Assistent steckt auch unter Training → Plan).
  root.innerHTML = `
    ${data.hasPlan ? "" : `<div class="card hero">
      <p class="eyebrow">Plan-Assistent</p>
      <h3 class="hero-title">Plan erstellen lassen</h3>
      <p class="hint">Nur die Trainingstage pro Woche wählen – den Rest stellt die App passend ein
        und passt ihn nach jeder Einheit an.</p>
      <div class="btn-row">
        <button type="button" class="btn primary" data-wizard>${icon("spark")} Neuen Plan erstellen</button>
      </div>
    </div>`}

    <div class="card">
      <p class="eyebrow">Trainingsziel</p>
      <p>${esc(summary(p))}</p>
      ${data.hasPlan ? `<div class="btn-row"><button type="button" class="btn small tonal" data-wizard>${icon("spark")}
        Neuen Plan erstellen</button></div>` : ""}
    </div>

    ${nutritionCard()}

    <div class="card">
      <p class="eyebrow">Deine Daten</p>
      <form id="profilForm" class="form">
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
        <div class="field">
          <span>Körpergewicht</span>
          <p class="hint">${weight ? `${String(weight.kg).replace(".", ",")} kg · ${parseYmd(weight.date).toLocaleDateString("de-AT")}`
            : "noch nicht eingetragen"}</p>
          <a class="btn small ghost" href="#fortschritt/koerper">Gewicht &amp; Maße eintragen</a>
        </div>
        <label class="field">Einschränkungen oder Verletzungen
          <textarea name="limitations" rows="2" maxlength="500"
            placeholder="optional, z. B. Knie links, keine Überkopf-Übungen">${esc(p.limitations || "")}</textarea>
        </label>
        <p class="hint">Stichwörter wie Knie, Rücken, Schulter oder Ellbogen berücksichtigt der
          Assistent direkt bei der Übungswahl; alles andere nur die KI-Verfeinerung.</p>
        <div class="btn-row"><button type="submit" class="btn">Speichern</button></div>
      </form>
    </div>
    <div class="card">
      <p class="eyebrow">Daten exportieren</p>
      <p class="hint">Als CSV für Excel oder Numbers – nur die Daten dieses Profils.</p>
      <div class="btn-row">${[["saetze", "Sätze"], ["cardio", "Cardio"], ["koerper", "Körper"], ["ernaehrung", "Ernährung"]]
        .map(([k, l]) => `<a class="btn small ghost" href="/api/export/${k}.csv?profile=${getProfile()}" download>${icon("download")} ${l}</a>`).join("")}</div>
    </div>
    <div class="btn-row"><button type="button" class="btn ghost" data-profiles>${icon("user")} Profile verwalten</button></div>
    <p class="hint pad">Name, Farbe und weitere Profile – auch oben rechts über den Namen.</p>`;

  const nf = $("#nutriForm", root);
  nf.nutrition_auto.addEventListener("change", () => {
    nf.querySelectorAll("[data-manual]").forEach((el) => { el.hidden = nf.nutrition_auto.checked; });
  });
  nf.water_auto.addEventListener("change", () => {
    nf.querySelectorAll("[data-water-manual]").forEach((el) => { el.hidden = nf.water_auto.checked; });
  });
  submitOnce(nf, async () => {
    try {
      const body = formData(nf);
      if (body.nutrition_auto) ["kcal_goal", "protein_goal", "carbs_goal", "fat_goal"].forEach((k) => delete body[k]);
      if (body.water_auto) body.water_goal_ml = null;
      else if (body.water_goal_ml == null) return formError(nf, new Error("Trinkziel: bitte einen Wert eintragen."));
      delete body.water_auto;
      await api(`profiles/${getProfile()}`, { method: "PATCH", body });
      toast("Ernährungsziel gespeichert");
      data = null;
      await load();
    } catch (e) { formError(nf, e); }
  });

  const form = $("#profilForm", root);
  submitOnce(form, async () => {
    try {
      const body = formData(form);
      const saved = await api(`profiles/${getProfile()}`, { method: "PATCH", body });
      data.profile = saved;
      window.dispatchEvent(new CustomEvent("profile-changed", { detail: saved }));
      toast("Profil gespeichert");
      render();
    } catch (e) { formError(form, e); }
  });
}

export async function load() {
  // Nicht neu zeichnen, während jemand im Formular tippt
  if (data && root.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) return;
  if (data && hasDirtyForm(root)) return;
  if (!data) root.innerHTML = skeleton(4);
  const [profile, bw, nutrition, splits] = await Promise.all([api(`profiles/${getProfile()}`),
    api("bodyweight"), api("nutrition/goals"), api("splits").catch(() => [])]);
  data = { profile, weight: bw[bw.length - 1] || null, nutrition, hasPlan: splits.some((s) => s.active) };
  render();
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-wizard]")) openWizard(data?.profile);
    if (ev.target.closest("[data-profiles]")) document.getElementById("profileBtn")?.click();
  });
}

// Einmal pro Seite (mount laeuft nach einem Profilwechsel erneut)
// Der Assistent speichert Ziel und Zeit ins Profil – Zusammenfassung dann frisch laden.
window.addEventListener("wizard-changed", () => {
  if (data && root?.isConnected && !root.hidden) load().catch(() => render());
});
