/** Übungsbibliothek: Suche, Muskelgruppen-Filter, Anleitung mit Muskel-Grafik. */
import { $, api, esc, fmtWeight, GROUPS, icon, openSheet, prefs, skeleton, toast } from "./util.js";
import { muscleMap } from "./muscles.js";
import { editExercise } from "./plan.js";
import { sparkline } from "./fortschritt.js";

let root;
let all = null;
let muscles = {};
let query = "";
let group = prefs.get("library.group") || "";

function listHtml() {
  const q = query.trim().toLowerCase();
  const items = all.filter((e) => !e.archived && (!group || e.muscle_group === group)
    && (!q || e.name.toLowerCase().includes(q) || (e.equipment || "").toLowerCase().includes(q)));
  if (!items.length) return `<p class="hint pad">Keine Übung passt.</p>`;
  const byGroup = {};
  for (const e of items) (byGroup[e.muscle_group || "sonstige"] ||= []).push(e);
  // Ohne Suche und Filter nur die Gruppenkoepfe – 49 Zeilen am Stueck sind keine Bibliothek,
  // sondern eine Wand. Suche oder Chip klappt die Treffer direkt auf.
  const open = Boolean(q || group);
  return Object.entries(byGroup).map(([g, list]) => `
    <details class="group-details lib-group" ${open ? "open" : ""}>
    <summary class="group-title">${esc(GROUPS[g] || "Sonstige")}<span class="count-muted">${list.length}</span>${icon("down", "chev")}</summary>
    <div class="list">${list.map((e) => `
      <div class="row-item" role="button" tabindex="0" data-ex="${e.id}">
        <div class="row-main">
          <div class="row-title">${esc(e.name)}</div>
          <div class="row-meta">${e.equipment ? `<span>${esc(e.equipment)}</span>` : ""}
            ${e.instructions ? "" : `<span class="faint">ohne Anleitung</span>`}</div>
        </div>${icon("right", "chev")}
      </div>`).join("")}</div></details>`).join("");
}

function render() {
  root.innerHTML = `<div class="toolbar">
      <label class="search">${icon("search")}<input type="search" data-q value="${esc(query)}"
        placeholder="Übung oder Gerät suchen…" aria-label="Übung suchen"></label>
      <button type="button" class="btn small ghost" data-new>${icon("plus")} Übung</button>
    </div>
    <div class="chips scroll" role="group" aria-label="Muskelgruppe">
      <button type="button" class="chip ${group ? "" : "active"}" data-group="">Alle</button>
      ${Object.entries(GROUPS).map(([k, l]) => `<button type="button" class="chip ${group === k ? "active" : ""}"
        data-group="${k}">${l}</button>`).join("")}
    </div>
    <div data-list>${listHtml()}</div>`;
  const input = $("[data-q]", root);
  input.addEventListener("input", () => {
    query = input.value;
    $("[data-list]", root).innerHTML = listHtml();
  });
}

export async function load() {
  if (root.contains(document.activeElement) && document.activeElement.matches("input")) return;
  if (!all) root.innerHTML = skeleton(6);
  [all, muscles] = await Promise.all([api("exercises"), Object.keys(muscles).length ? muscles : api("muscles")]);
  render();
}

async function openExercise(id) {
  let ex;
  try {
    ex = await api(`exercises/${id}`);
  } catch (e) {
    return toast(e.offline ? "Die Anleitung ist offline noch nicht verfügbar." : e.message, { error: true });
  }
  const names = (list) => list.map((m) => muscles[m] || m).join(", ");
  const hist = ex.history || [];
  const sheet = openSheet({
    title: esc(ex.name),
    wide: true,
    body: `${ex.muscles_primary.length ? `${muscleMap(ex.muscles_primary, ex.muscles_secondary)}
      <p class="hint center"><span class="legend primary"></span> ${esc(names(ex.muscles_primary))}
        ${ex.muscles_secondary.length ? ` · <span class="legend secondary"></span> ${esc(names(ex.muscles_secondary))}` : ""}</p>` : ""}
      <div class="row-meta">${ex.equipment ? `<span>${icon("dumbbell")} ${esc(ex.equipment)}</span>` : ""}
        <span>${esc(GROUPS[ex.muscle_group] || "")}</span></div>
      ${ex.instructions.length ? `<h3 class="form-section">Ausführung</h3>
        <ol class="steps-list">${ex.instructions.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
      ${ex.mistakes.length ? `<h3 class="form-section">Typische Fehler</h3>
        <ul class="plain warn-list">${ex.mistakes.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
      ${!ex.instructions.length && !ex.mistakes.length ? `<p class="hint">Für diese Übung gibt es noch keine Anleitung – über „Bearbeiten“ kannst du eine eintragen.</p>` : ""}
      <div class="btn-row"><a class="btn tonal" href="${esc(ex.video_url)}" target="_blank" rel="noopener">
        ${icon("play")} Video ansehen</a></div>
      ${ex.best ? `<h3 class="form-section">Deine Bestleistung</h3>
        <p><b>${esc(ex.best.reps)} × ${esc(fmtWeight(ex.best.weight, ex.unit))}</b>
        ${ex.best.one_rm ? `<span class="hint"> · 1RM ≈ ${String(ex.best.one_rm).replace(".", ",")} kg</span>` : ""}</p>
        ${hist.length > 1 ? sparkline(hist.map((p) => ({ value: p.value, date: p.date }))) : ""}` : ""}
      ${ex.used_in.length ? `<p class="hint">Im Plan: ${ex.used_in.map((u) => esc(`${u.split} · ${u.day}`)).join(", ")}</p>` : ""}`,
    footer: `<span class="grow"></span>
      <button type="button" class="btn ghost" data-edit>${icon("edit")} Bearbeiten</button>
      <button type="button" class="btn ghost" data-close>Schließen</button>`,
  });
  $("[data-edit]", sheet).addEventListener("click", () => editExercise(all.find((e) => e.id === id)));
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const g = ev.target.closest("[data-group]");
    if (g) {
      group = g.dataset.group;
      prefs.set("library.group", group);
      return render();
    }
    if (ev.target.closest("[data-new]")) return editExercise(null);
    const row = ev.target.closest("[data-ex]");
    if (row) openExercise(Number(row.dataset.ex));
  });
}
