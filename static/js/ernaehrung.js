/** Ernährung: Tagesbilanz, Mahlzeiten, Lebensmittel suchen/scannen/anlegen. */
import { $, api, armDelete, closeSheet, esc, formData, formError, getProfile, icon, openSheet,
  skeleton, submitOnce, toast, busy } from "./util.js";
import * as outbox from "./outbox.js";
import { scanBarcode } from "./scanner.js";

let root;
let data = null;
let day = null;   // YYYY-MM-DD, null = heute
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist

const MEALS = { fruehstueck: "Frühstück", mittag: "Mittagessen", abend: "Abendessen", snack: "Snacks" };
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayIso = () => iso(new Date());
const num = (v, digits = 0) => (v == null ? "–" : Number(v).toLocaleString("de-AT", { maximumFractionDigits: digits }));

// ------------------------------------------------------------- Anzeige
export function ring(value, goal, { size = 132, label = "kcal" } = {}) {
  const pct = goal ? Math.min(value / goal, 1) : 0;
  const over = goal && value > goal;
  const r = 52;
  const c = 2 * Math.PI * r;
  return `<svg class="ring-svg" viewBox="0 0 120 120" width="${size}" height="${size}" role="img"
      aria-label="${num(value)} von ${num(goal)} ${label}">
    <circle cx="60" cy="60" r="${r}" class="ring-track"/>
    ${pct > 0 ? `<circle cx="60" cy="60" r="${r}" class="ring-fill ${over ? "over" : ""}"
      stroke-dasharray="${(pct * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 60 60)"/>` : ""}
    <text x="60" y="56" text-anchor="middle" class="ring-value">${goal ? num(Math.abs(goal - value)) : num(value)}</text>
    <text x="60" y="74" text-anchor="middle" class="ring-label">${goal ? (over ? "zu viel" : "übrig") : label}</text>
  </svg>`;
}

function macro(label, value, goal) {
  const pct = goal ? Math.min(100, (value / goal) * 100) : 0;
  return `<div class="macro"><span>${label}</span>
    <span class="bar"><i style="width:${pct}%"></i></span>
    <span class="macro-val">${num(value)}${goal ? ` / ${num(goal)}` : ""} g</span></div>`;
}

function pendingEntries() {
  return outbox.pending("food-log").filter((e) => (e.date || todayIso()) === data.date);
}

function pendingWater() {
  return outbox.pending("water-log").filter((e) => (e.date || todayIso()) === data.date)
    .reduce((n, e) => n + e.ml, 0);
}

// ------------------------------------------------------------- Wasser
const liters = (ml) => (ml / 1000).toLocaleString("de-AT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function glass(ml, goal) {
  const pct = goal ? Math.min(1, Math.max(0, ml / goal)) : 0;
  const top = 12, bottom = 88, h = (bottom - top) * pct;
  const shape = "M9 10h42l-6.5 78a4 4 0 0 1-4 3.7H19.5a4 4 0 0 1-4-3.7z";
  return `<svg class="glass-svg" viewBox="0 0 60 100" width="60" height="100" role="img"
      aria-label="${num(ml)} von ${goal ? num(goal) : "?"} ml Wasser getrunken">
    <clipPath id="glassClip"><path d="${shape}"/></clipPath>
    <path d="${shape}" class="glass-outline"/>
    ${h > 0 ? `<rect x="3" y="${bottom - h}" width="54" height="${h + 12}" class="glass-fill" clip-path="url(#glassClip)"/>` : ""}
  </svg>`;
}

function waterCard(ml, goal) {
  return `<div class="card water-card">
    <div class="card-title">${icon("water")} Wasser</div>
    <div class="water-row">
      <button type="button" class="icon-btn tonal-icon" data-water="-500" aria-label="Halben Liter abziehen">${icon("minus")}</button>
      ${glass(ml, goal)}
      <button type="button" class="icon-btn tonal-icon" data-water="500" aria-label="Halben Liter hinzufügen">${icon("plus")}</button>
    </div>
    <p class="water-total">${liters(ml)}${goal ? ` von ${liters(goal)}` : ""} L</p>
  </div>`;
}

function render() {
  const g = data.goals.goals || {};
  const t = data.totals;
  const pend = pendingEntries();
  const pendKcal = pend.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
  const isToday = data.date === todayIso();
  const dateLabel = isToday ? "Heute" : new Date(data.date).toLocaleDateString("de-AT", { weekday: "long", day: "numeric", month: "long" });
  root.innerHTML = `<div class="date-switch">
      <button type="button" class="icon-btn" data-day="-1" aria-label="Vorheriger Tag">${icon("left")}</button>
      <b>${esc(dateLabel)}</b>
      <button type="button" class="icon-btn" data-day="1" aria-label="Nächster Tag" ${isToday ? "disabled" : ""}>${icon("right")}</button>
    </div>
    <div class="card nutri-head">
      ${ring(t.kcal + pendKcal, g.kcal)}
      <div class="nutri-side">
        <p class="eyebrow">${num(t.kcal + pendKcal)} ${g.kcal ? `von ${num(g.kcal)} ` : ""}kcal</p>
        ${macro("Eiweiß", t.protein, g.protein)}
        ${macro("Kohlenhydrate", t.carbs, g.carbs)}
        ${macro("Fett", t.fat, g.fat)}
      </div>
    </div>
    ${!g.kcal ? `<p class="callout">${icon("warn")} Für ein Kalorienziel fehlen noch: ${esc((data.goals.missing || []).join(", "))}.
      <a href="#profil">Im Profil ergänzen</a></p>` : ""}
    ${waterCard(data.water_ml + pendingWater() + waterExtra, data.water_goal)}
    ${Object.entries(data.meals).map(([key, m]) => {
      const extra = pend.filter((e) => e.meal === key);
      return `<div class="card meal">
        <div class="ex-head"><div><div class="card-title">${esc(m.label)}</div>
          <div class="row-meta"><span>${num(m.kcal + extra.reduce((n, e) => n + (e.preview?.kcal || 0), 0))} kcal</span></div></div>
          <button type="button" class="icon-btn tonal-icon" data-add="${key}" aria-label="${esc(m.label)} hinzufügen">${icon("plus")}</button>
        </div>
        ${m.entries.length || extra.length ? `<div class="list flush">
          ${m.entries.map((e) => `<div class="row-item" role="button" tabindex="0" data-entry="${e.id}">
            <div class="row-main"><div class="row-title">${esc(e.name)}</div>
              <div class="row-meta"><span>${num(e.grams)} g</span><span>E ${num(e.protein)} · K ${num(e.carbs)} · F ${num(e.fat)}</span></div></div>
            <span class="row-aside">${num(e.kcal)} kcal</span></div>`).join("")}
          ${extra.map((e) => `<div class="row-item is-pending"><div class="row-main">
            <div class="row-title">${esc(e.preview?.name || "Eintrag")}</div>
            <div class="row-meta"><span>${num(e.grams)} g</span><span class="pill">wartet</span></div></div>
            <span class="row-aside">${num(e.preview?.kcal)} kcal</span></div>`).join("")}
        </div>` : ""}
      </div>`;
    }).join("")}`;
}

export async function load() {
  if (!data) root.innerHTML = skeleton(5);
  data = await api(`nutrition/day?date=${day || todayIso()}`);
  render();
}

// ------------------------------------------------------- Hinzufügen
function foodRow(f, i) {
  return `<div class="row-item" role="button" tabindex="0" data-pick="${i}">
    <div class="row-main"><div class="row-title">${esc(f.name)}</div>
      <div class="row-meta">${f.brand ? `<span>${esc(f.brand)}</span>` : ""}<span>${num(f.kcal)} kcal / 100 g</span></div></div>
    ${icon("right", "chev")}</div>`;
}

function openAdd(meal) {
  const st = { meal, results: [], recent: [], query: "", food: null, error: null, searching: false };
  const sheet = openSheet({ title: `${MEALS[meal]} · hinzufügen`, body: "", wide: true });
  const body = $(".sheet-body", sheet);
  let timer = null;

  const list = () => (st.query.length >= 2 ? st.results : st.recent);

  function paintList() {
    const box = $("[data-results]", sheet);
    if (!box) return;
    const items = list();
    box.innerHTML = st.searching ? `<p class="hint pad">Suche …</p>`
      : items.length ? `<div class="list">${items.map(foodRow).join("")}</div>`
        : `<p class="hint pad">${st.query.length >= 2 ? "Nichts gefunden." : "Noch keine zuletzt verwendeten Lebensmittel."}</p>`;
    $("[data-error]", sheet).textContent = st.error || "";
  }

  function paintSearch() {
    body.innerHTML = `<label class="search">${icon("search")}<input type="search" data-q value="${esc(st.query)}"
        placeholder="Lebensmittel suchen…" aria-label="Lebensmittel suchen" autocomplete="off"></label>
      <div class="btn-row">
        <button type="button" class="btn small tonal" data-scan>${icon("barcode")} Barcode scannen</button>
        <button type="button" class="btn small ghost" data-own>${icon("plus")} Eigenes Lebensmittel</button>
      </div>
      <p class="hint" data-error role="status"></p>
      <h3 class="form-section">${st.query.length >= 2 ? "Treffer" : "Zuletzt verwendet"}</h3>
      <div data-results></div>`;
    paintList();
    const input = $("[data-q]", sheet);
    input.addEventListener("input", () => {
      st.query = input.value.trim();
      sheet.querySelector(".form-section").textContent = st.query.length >= 2 ? "Treffer" : "Zuletzt verwendet";
      clearTimeout(timer);
      if (st.query.length < 2) { st.error = null; return paintList(); }
      timer = setTimeout(search, 450);
    });
  }

  async function search() {
    const q = st.query;
    st.searching = true;
    paintList();
    try {
      const res = await api(`foods/search?q=${encodeURIComponent(q)}`);
      if (q !== st.query) return;
      st.results = [...res.local, ...res.online];
      st.error = res.error;
    } catch (e) {
      st.results = st.recent.filter((f) => f.name.toLowerCase().includes(q.toLowerCase()));
      st.error = e.offline ? "Offline – es werden nur zuletzt verwendete Lebensmittel durchsucht." : e.message;
    }
    st.searching = false;
    paintList();
  }

  function paintAmount() {
    const f = st.food;
    const presets = [...new Set([f.serving_g, f.last_grams, 50, 100, 150, 200].filter(Boolean).map(Math.round))].slice(0, 5);
    const start = Math.round(f.last_grams || f.serving_g || 100);
    body.innerHTML = `<button type="button" class="link-btn" data-back>${icon("left")} Zurück</button>
      <h3 class="card-title">${esc(f.name)}</h3>
      <p class="hint">${f.brand ? `${esc(f.brand)} · ` : ""}je 100 g: ${num(f.kcal)} kcal · E ${num(f.protein, 1)} · K ${num(f.carbs, 1)} · F ${num(f.fat, 1)}</p>
      <label class="field">Menge (g)<input data-grams type="number" inputmode="decimal" min="1" max="5000" value="${start}" autofocus></label>
      <div class="chips">${presets.map((g) => `<button type="button" class="chip" data-preset="${g}">${g} g${g === Math.round(f.serving_g) ? " (Portion)" : ""}</button>`).join("")}</div>
      <label class="field">Mahlzeit<select data-meal>${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === st.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <div class="card quiet" data-calc></div>
      <div class="btn-row"><button type="button" class="btn primary" data-save>${icon("check")} Hinzufügen</button></div>`;
    const calc = () => {
      const g = Number($("[data-grams]", sheet).value) || 0;
      const k = (v) => num(((v || 0) * g) / 100, 1);
      $("[data-calc]", sheet).innerHTML = `<b>${k(f.kcal)} kcal</b><span>E ${k(f.protein)} g · K ${k(f.carbs)} g · F ${k(f.fat)} g</span>`;
    };
    $("[data-grams]", sheet).addEventListener("input", calc);
    calc();
  }

  async function pick(food) {
    st.food = food;
    paintAmount();
  }

  async function save(btn) {
    const f = st.food;
    const grams = Number($("[data-grams]", sheet).value);
    if (!(grams > 0)) return toast("Bitte eine Menge eintragen.", { error: true });
    const meal = $("[data-meal]", sheet).value;
    await busy(btn, async () => {
      try {
        let food = f;
        if (!food.id) {   // online gefundenes Produkt erst übernehmen
          food = await api("foods", { method: "POST", body: {
            name: f.name, brand: f.brand, barcode: f.barcode, kcal: f.kcal, protein: f.protein,
            carbs: f.carbs, fat: f.fat, serving_g: f.serving_g, source: "off" } });
        }
        const body = { profile_id: getProfile(), food_id: food.id, grams, meal, date: data.date,
          client_id: outbox.uuid(),
          preview: { name: food.name, kcal: Math.round((food.kcal * grams) / 100) } };
        const res = await outbox.send({ path: "food-log", body });
        closeSheet();
        toast(res.queued ? "Gespeichert – wird gesendet, sobald wieder Verbindung da ist"
          : `${food.name} eingetragen`);
        window.dispatchEvent(new CustomEvent("data-changed"));
      } catch (e) {
        toast(e.offline ? "Offline lassen sich nur schon verwendete Lebensmittel eintragen." : e.message, { error: true });
      }
    });
  }

  async function byBarcode(code) {
    try {
      const res = await api(`foods/barcode/${code}`);
      pick(res.food);
    } catch (e) {
      if (/nicht gefunden/.test(e.message)) {
        toast("Produkt nicht in der Datenbank – bitte die Werte von der Packung eintragen.");
        return openOwn({ barcode: code });
      }
      st.error = e.offline ? "Offline – der Barcode kann gerade nicht nachgeschlagen werden." : e.message;
      paintSearch();
    }
  }

  function openOwn(preset = {}) {
    body.innerHTML = `<button type="button" class="link-btn" data-back>${icon("left")} Zurück</button>
      <form id="foodForm" class="form">
        <label class="field">Name<input name="name" maxlength="120" required autofocus value="${esc(preset.name || st.query || "")}"></label>
        <label class="field">Marke<input name="brand" maxlength="60" placeholder="optional"></label>
        <p class="hint">Nährwerte je 100 g (stehen auf der Packung):</p>
        <div class="field-row">
          <label class="field">kcal<input name="kcal" type="number" inputmode="decimal" min="0" max="950" step="0.1" required></label>
          <label class="field">Eiweiß<input name="protein" type="number" inputmode="decimal" min="0" max="100" step="0.1"></label>
        </div>
        <div class="field-row">
          <label class="field">Kohlenhydrate<input name="carbs" type="number" inputmode="decimal" min="0" max="100" step="0.1"></label>
          <label class="field">Fett<input name="fat" type="number" inputmode="decimal" min="0" max="100" step="0.1"></label>
        </div>
        <label class="field">Portion (g)<input name="serving_g" type="number" inputmode="decimal" min="1" max="2000" placeholder="optional"></label>
        <input type="hidden" name="barcode" value="${esc(preset.barcode || "")}">
        <div class="btn-row"><button type="submit" class="btn primary">Weiter</button></div>
      </form>`;
    const form = $("#foodForm", sheet);
    submitOnce(form, async () => {
      try {
        const food = await api("foods", { method: "POST", body: formData(form) });
        pick(food);
      } catch (e) { formError(form, e); }
    });
  }

  sheet.addEventListener("click", async (ev) => {
    const t = (s) => ev.target.closest(s);
    const p = t("[data-pick]");
    if (p) return pick(list()[Number(p.dataset.pick)]);
    if (t("[data-back]")) { st.food = null; return paintSearch(); }
    const preset = t("[data-preset]");
    if (preset) {
      const input = $("[data-grams]", sheet);
      input.value = preset.dataset.preset;
      input.dispatchEvent(new Event("input"));
      return;
    }
    const s = t("[data-save]");
    if (s) return save(s);
    if (t("[data-own]")) return openOwn();
    if (t("[data-scan]")) {
      const code = await scanBarcode();
      if (code) byBarcode(code);
    }
  });

  paintSearch();
  api("foods/recent").then((r) => { st.recent = r; if (!st.food) paintList(); }).catch(() => {});
}

function openEntry(id) {
  const e = Object.values(data.meals).flatMap((m) => m.entries).find((x) => x.id === id);
  if (!e) return;
  const sheet = openSheet({
    title: esc(e.name),
    body: `<form id="entryForm" class="form">
      <label class="field">Menge (g)<input name="grams" type="number" inputmode="decimal" min="1" max="5000" required value="${e.grams}"></label>
      <label class="field">Mahlzeit<select name="meal">${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === e.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <p class="hint">${num(e.kcal)} kcal · E ${num(e.protein, 1)} g · K ${num(e.carbs, 1)} g · F ${num(e.fat, 1)} g</p>
    </form>`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="submit" class="btn primary" form="entryForm">Speichern</button>`,
  });
  const form = $("#entryForm", sheet);
  submitOnce(form, async () => {
    try {
      await api(`food-log/${id}`, { method: "PATCH", body: formData(form) });
      closeSheet();
      toast("Gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (err) { formError(form, err); }
  });
  armDelete($("[data-delete]", sheet), async () => {
    closeSheet();
    try {
      await api(`food-log/${id}`, { method: "DELETE" });
      toast("Eintrag gelöscht");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (err) { toast(err.message, { error: true }); }
  }, "Löschen", "Eintrag löschen?");
}

export async function mount(el) {
  root = el;
  el.addEventListener("click", (ev) => {
    const d = ev.target.closest("[data-day]");
    if (d) {
      const cur = new Date(data.date);
      cur.setDate(cur.getDate() + Number(d.dataset.day));
      day = iso(cur) >= todayIso() ? null : iso(cur);
      data = null;
      return load();
    }
    const add = ev.target.closest("[data-add]");
    if (add) return openAdd(add.dataset.add);
    const entry = ev.target.closest("[data-entry]");
    if (entry) openEntry(Number(entry.dataset.entry));
    const water = ev.target.closest("[data-water]");
    if (water) return addWater(Number(water.dataset.water));
  });
}

async function addWater(ml) {
  const body = { profile_id: getProfile(), ml, date: data.date, client_id: outbox.uuid() };
  waterExtra += ml;   // sofort sichtbar, nicht erst nach der Antwort vom Pi
  render();
  try {
    const res = await outbox.send({ path: "water-log", body });
    waterExtra -= ml;
    // Erfolgreich gespeichert: gleich in die Bilanz einrechnen statt neu vom Server zu laden.
    // Im Ausgang gelandet (offline): pendingWater() zaehlt den Eintrag ab jetzt mit.
    if (!res.queued) data.water_ml = Math.max(0, data.water_ml + ml);
    render();
  } catch (e) {
    waterExtra -= ml;
    render();
    toast(e.message, { error: true });
  }
}
