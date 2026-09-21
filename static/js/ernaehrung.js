/** Ernährung: Tagesbilanz, Mahlzeiten, Lebensmittel suchen/scannen/anlegen. */
import { $, api, armDelete, closeSheet, deleteLater, esc, formData, formError, getProfile, icon,
  isPendingDelete, openSheet, skeleton, submitOnce, toast, busy } from "./util.js";
import * as outbox from "./outbox.js";
import { scanBarcode } from "./scanner.js";

let root;
let data = null;
let day = null;   // YYYY-MM-DD, null = heute
let waterExtra = 0;   // optimistisch, bis der Klick bestaetigt oder in den Ausgang gelegt ist

// Der Pi wartet bei Open Food Facts bis zu 12 s je Abruf (app/nutrition.py) – das
// Standard-Zeitlimit von api() wuerde vorher aufgeben und "offline" melden.
const OFF_TIMEOUT = 30000;
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
      const visible = m.entries.filter((e) => !isPendingDelete(`food-log/${e.id}`));
      const kcal = m.kcal - m.entries.filter((e) => isPendingDelete(`food-log/${e.id}`))
        .reduce((n, e) => n + (e.kcal || 0), 0) + extra.reduce((n, e) => n + (e.preview?.kcal || 0), 0);
      return `<div class="card meal">
        <div class="ex-head"><div><div class="card-title">${esc(m.label)}</div>
          <div class="row-meta"><span>${num(kcal)} kcal</span></div></div>
          <button type="button" class="icon-btn tonal-icon" data-add="${key}" aria-label="${esc(m.label)} hinzufügen">${icon("plus")}</button>
        </div>
        ${visible.length || extra.length ? `<div class="list flush">
          ${visible.map((e) => `<div class="row-item" role="button" tabindex="0" data-entry="${e.id}">
            <div class="row-main"><div class="row-title">${esc(e.name)}</div>
              <div class="row-meta"><span>${portionsText(e)}${num(e.grams)} g</span><span>E ${num(e.protein)} · K ${num(e.carbs)} · F ${num(e.fat)}</span></div></div>
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
/** Vergleichsform wie nutrition.norm im Backend: "Müller" = "Mueller" = "muller". */
const norm = (s) => (s || "").toLowerCase().replace(/ß/g, "ss").replace(/(?<=[aou])e/g, "")
  .normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/['’`´]/g, "")
  .replace(/[^a-z0-9]+/g, " ").trim();
const matches = (f, q) => { const hay = norm(`${f.name} ${f.brand || ""}`); return norm(q).split(" ").every((w) => hay.includes(w)); };
const kcalFor = (f, grams) => Math.round(((f.kcal || 0) * grams) / 100);
const count = (n) => n.toLocaleString("de-AT", { maximumFractionDigits: 2 });

/** Portion eines Lebensmittels: angegebene Portion, sonst eine kleine Packung (Burger, Riegel, Becher) –
 *  grosse Packungen sind keine Portion. */
function servingOf(f) {
  if (f.serving_g > 0) {
    const label = f.serving_label || "Portion";
    return { g: f.serving_g, text: /\d\s*(g|ml)\b/i.test(label) ? label : `${label} (${num(f.serving_g)} g)` };
  }
  if (f.pack_g > 0 && f.pack_g <= 250) return { g: f.pack_g, text: `Packung (${num(f.pack_g)} g)` };
  return null;
}

/** "2 Portionen · " vor der Grammzahl eines Eintrags, wenn die Menge glatt aufgeht. */
function portionsText(e) {
  const s = servingOf(e);
  const n = s && e.grams / s.g;
  if (!n || Math.abs(n * 2 - Math.round(n * 2)) > 0.02) return "";
  const unit = s.text.startsWith("Packung") ? (n === 1 ? "Packung" : "Packungen") : (n === 1 ? "Portion" : "Portionen");
  return `${count(Math.round(n * 2) / 2)} ${unit} · `;
}

/** Suchwort im Namen hervorheben (erster Treffer des laengsten Worts). */
function highlight(text, q) {
  const word = (q || "").trim().split(/\s+/).sort((a, b) => b.length - a.length)[0];
  const at = word && word.length >= 2 ? text.toLowerCase().indexOf(word.toLowerCase()) : -1;
  if (at < 0) return esc(text);
  return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + word.length))}</mark>${esc(text.slice(at + word.length))}`;
}

function foodRow(f, i, q) {
  const s = servingOf(f);
  const meta = [f.brand && esc(f.brand), s && esc(s.text), f.last_grams && `zuletzt ${num(f.last_grams)} g`].filter(Boolean);
  return `<div class="row-item food-row" role="button" tabindex="0" data-pick="${i}">
    <div class="row-main"><div class="row-title">${highlight(f.name, q)}</div>
      ${meta.length ? `<div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>` : ""}</div>
    <span class="row-aside food-kcal">${s ? `<b>${num(kcalFor(f, s.g))} kcal</b><small>pro ${s.text.startsWith("Packung") ? "Packung" : "Portion"}</small>`
      : `<b>${num(f.kcal)} kcal</b><small>pro 100 g</small>`}</span></div>`;
}

/** Menge in Portionen oder Gramm. Gespeichert werden immer Gramm. */
function amountBox(box, f, grams, onChange) {
  const s = servingOf(f);
  const n0 = s && grams ? grams / s.g : 1;
  const halfStep = Math.abs(n0 * 2 - Math.round(n0 * 2)) < 0.02;
  let mode = s && (!grams || halfStep) ? "portion" : "gram";
  let value = mode === "portion" ? Math.max(0.5, Math.round(n0 * 2) / 2) : Math.round(grams || 100);
  const current = () => (mode === "portion" ? Math.round(value * s.g * 10) / 10 : value);
  const pack = f.pack_g > 0 && f.pack_g <= 1000 ? Math.round(f.pack_g) : null;

  function chipLabel(g) {
    if (s && g === Math.round(s.g)) return `${g} g (${s.text.startsWith("Packung") ? "Packung" : "Portion"})`;
    if (g === pack) return `${g} g (Packung)`;
    if (f.last_grams && g === Math.round(f.last_grams)) return `${g} g (zuletzt)`;
    return `${g} g`;
  }

  function paint() {
    const gramChips = [...new Set([s && s.g, f.last_grams, pack, 50, 100, 150, 200].filter(Boolean).map(Math.round))].slice(0, 6);
    box.innerHTML = `${s ? `<div class="segmented small amount-mode" role="radiogroup" aria-label="Menge angeben in">
        <button type="button" role="radio" aria-checked="${mode === "portion"}" data-mode="portion">Portionen</button>
        <button type="button" role="radio" aria-checked="${mode === "gram"}" data-mode="gram">Gramm</button></div>` : ""}
      ${mode === "portion" ? `<div class="field"><span>Anzahl · ${esc(s.text)}</span>
          <div class="stepper">
            <button type="button" class="icon-btn tonal-icon" data-step="-0.5" aria-label="Halbe Portion weniger">${icon("minus")}</button>
            <input data-count type="number" inputmode="decimal" min="0.25" max="50" step="0.25" value="${value}" aria-label="Anzahl Portionen">
            <button type="button" class="icon-btn tonal-icon" data-step="0.5" aria-label="Halbe Portion mehr">${icon("plus")}</button>
          </div></div>
        <div class="chips">${[0.5, 1, 1.5, 2, 3].map((n) => `<button type="button" class="chip${n === value ? " active" : ""}" data-set="${n}">${count(n)}</button>`).join("")}</div>`
      : `<label class="field">Menge (g)<input data-grams type="number" inputmode="decimal" min="1" max="5000" value="${value}"></label>
        <div class="chips">${gramChips.map((g) => `<button type="button" class="chip${g === value ? " active" : ""}" data-set="${g}">${chipLabel(g)}</button>`).join("")}</div>`}`;
  }

  const changed = () => {
    box.querySelectorAll("[data-set]").forEach((c) => c.classList.toggle("active", Number(c.dataset.set) === value));
    onChange(current());
  };
  box.addEventListener("click", (ev) => {
    const m = ev.target.closest("[data-mode]");
    if (m && m.dataset.mode !== mode) {
      const g = current();
      mode = m.dataset.mode;
      value = mode === "portion" ? Math.max(0.5, Math.round((g / s.g) * 2) / 2) : Math.round(g);
      paint();
      return changed();
    }
    const step = ev.target.closest("[data-step]");
    if (step) value = Math.min(50, Math.max(0.5, Math.round((value + Number(step.dataset.step)) * 2) / 2));
    const set = ev.target.closest("[data-set]");
    if (set) value = Number(set.dataset.set);
    if (step || set) {
      const input = box.querySelector("[data-count], [data-grams]");
      input.value = value;
      changed();
    }
  });
  box.addEventListener("input", (ev) => {
    if (!ev.target.matches("[data-count], [data-grams]")) return;
    value = Number(ev.target.value) || 0;
    changed();
  });
  paint();
  onChange(current());
  return { grams: current };
}

function openAdd(meal) {
  const st = { meal, local: [], online: [], recent: [], items: [], query: "", food: null, error: null,
    searchingLocal: false, searchingOnline: false };
  const sheet = openSheet({ title: `${MEALS[meal]} · hinzufügen`, body: "", wide: true });
  const body = $(".sheet-body", sheet);
  let localTimer = null;
  let onlineTimer = null;
  let amount = null;

  function section(title, items, empty) {
    const from = st.items.length;
    st.items.push(...items);
    return `<h3 class="form-section">${title}</h3>
      ${items.length ? `<div class="list">${items.map((f, i) => foodRow(f, from + i, st.query)).join("")}</div>` : empty}`;
  }

  function paintList() {
    const box = $("[data-results]", sheet);
    if (!box) return;
    st.items = [];
    if (st.query.length < 2) {
      box.innerHTML = section("Zuletzt verwendet", st.recent, `<p class="hint pad">Noch keine zuletzt verwendeten Lebensmittel.</p>`);
    } else {
      const known = new Set(st.local.map((f) => f.barcode).filter(Boolean));
      const online = st.online.filter((f) => !f.barcode || !known.has(f.barcode));
      box.innerHTML = (st.local.length || st.searchingLocal
        ? section("Gespeicherte Lebensmittel", st.local, `<p class="hint pad">Suche …</p>`) : "")
        + section("Produkte (Open Food Facts)", online,
          `<p class="hint pad">${st.searchingOnline ? "Weitere Produkte werden gesucht …" : st.error ? "" : "Nichts gefunden."}</p>`)
        + (online.length && st.searchingOnline ? `<p class="hint pad">Weitere Produkte werden gesucht …</p>` : "");
    }
    $("[data-error]", sheet).textContent = st.error || "";
  }

  function paintSearch() {
    body.innerHTML = `<label class="search">${icon("search")}<input type="search" data-q value="${esc(st.query)}"
        placeholder="Lebensmittel oder Marke suchen…" aria-label="Lebensmittel suchen" autocomplete="off"></label>
      <div class="btn-row">
        <button type="button" class="btn small tonal" data-scan>${icon("barcode")} Barcode scannen</button>
        <button type="button" class="btn small ghost" data-own>${icon("plus")} Eigenes Lebensmittel</button>
      </div>
      <p class="hint" data-error role="status"></p>
      <div data-results></div>`;
    paintList();
    const input = $("[data-q]", sheet);
    input.addEventListener("input", () => {
      st.query = input.value.trim();
      clearTimeout(localTimer);
      clearTimeout(onlineTimer);
      st.error = null;
      if (st.query.length < 2) { st.local = []; st.online = []; st.searchingOnline = false; return paintList(); }
      // Eigene Lebensmittel sofort, Open Food Facts erst, wenn nicht mehr getippt wird
      st.searchingLocal = st.searchingOnline = true;
      localTimer = setTimeout(searchLocal, 200);
      onlineTimer = setTimeout(searchOnline, 600);
    });
  }

  async function searchLocal() {
    const q = st.query;
    try {
      const res = await api(`foods/search/local?q=${encodeURIComponent(q)}`);
      if (q !== st.query) return;
      st.local = res.local;
    } catch {
      if (q !== st.query) return;
      st.local = st.recent.filter((f) => matches(f, q));
    }
    st.searchingLocal = false;
    paintList();
  }

  async function searchOnline() {
    const q = st.query;
    try {
      const res = await api(`foods/search?q=${encodeURIComponent(q)}`, { timeout: OFF_TIMEOUT });
      if (q !== st.query) return;
      st.online = res.online;
      st.error = res.error;
    } catch (e) {
      if (q !== st.query) return;
      st.online = [];
      st.error = e.offline ? "Offline – es werden nur gespeicherte Lebensmittel durchsucht." : e.message;
    }
    st.searchingOnline = false;
    paintList();
  }

  function paintAmount() {
    const f = st.food;
    const s = servingOf(f);
    body.innerHTML = `<button type="button" class="link-btn" data-back>${icon("left")} Zurück</button>
      <h3 class="card-title">${esc(f.name)}</h3>
      <p class="hint">${f.brand ? `${esc(f.brand)} · ` : ""}je 100 g: ${num(f.kcal)} kcal · E ${num(f.protein, 1)} · K ${num(f.carbs, 1)} · F ${num(f.fat, 1)}
        ${s ? `<br>${esc(s.text)}: ${num(kcalFor(f, s.g))} kcal` : ""}</p>
      <div data-amount></div>
      <label class="field">Mahlzeit<select data-meal>${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === st.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <div class="card quiet" data-calc></div>
      <div class="btn-row"><button type="button" class="btn primary" data-save>${icon("check")} Hinzufügen</button></div>`;
    amount = amountBox($("[data-amount]", sheet), f, f.last_grams, (g) => {
      const k = (v) => num(((v || 0) * g) / 100, 1);
      $("[data-calc]", sheet).innerHTML = `<b>${k(f.kcal)} kcal</b><span>${num(g, 1)} g · E ${k(f.protein)} g · K ${k(f.carbs)} g · F ${k(f.fat)} g</span>`;
    });
  }

  async function pick(food) {
    st.food = { ...food };
    paintAmount();
    // Die Suche kennt die Portion nur fuer die ersten Treffer – bei Bedarf jetzt nachladen
    if (food.barcode && !food.serving_g && (food.source === "off" || !food.id)) {
      try {
        const p = await api(`foods/portion/${food.barcode}`, { timeout: OFF_TIMEOUT });
        if (st.food?.barcode !== food.barcode || !(p.serving_g || p.pack_g)) return;
        const before = servingOf(st.food);
        Object.entries(p).forEach(([k, v]) => { if (v && !st.food[k]) st.food[k] = v; });
        if (!before && servingOf(st.food)) paintAmount();
      } catch { /* ohne Portion geht es auch */ }
    }
  }

  async function save(btn) {
    const f = st.food;
    const grams = amount.grams();
    if (!(grams > 0)) return toast("Bitte eine Menge eintragen.", { error: true });
    const meal = $("[data-meal]", sheet).value;
    await busy(btn, async () => {
      try {
        let food = f;
        if (!food.id) {   // online gefundenes Produkt erst übernehmen
          food = await api("foods", { method: "POST", body: {
            name: f.name, brand: f.brand, barcode: f.barcode, kcal: f.kcal, protein: f.protein,
            carbs: f.carbs, fat: f.fat, serving_g: f.serving_g, serving_label: f.serving_label,
            pack_g: f.pack_g, source: "off" } });
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
      const res = await api(`foods/barcode/${code}`, { timeout: OFF_TIMEOUT });
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
        <p class="hint">Portion – dann trägst du später einfach „1 Scheibe“ statt Gramm ein:</p>
        <div class="field-row">
          <label class="field">Portion heißt<input name="serving_label" maxlength="60" placeholder="z. B. 1 Scheibe"></label>
          <label class="field">Portion (g)<input name="serving_g" type="number" inputmode="decimal" min="1" max="2000" placeholder="optional"></label>
        </div>
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
    if (p) return pick(st.items[Number(p.dataset.pick)]);
    if (t("[data-back]")) { st.food = null; return paintSearch(); }
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
  const per100 = (k) => (e.grams ? ((e[k] || 0) * 100) / e.grams : 0);
  const food = { serving_g: e.serving_g, serving_label: e.serving_label, pack_g: e.pack_g };
  const sheet = openSheet({
    title: esc(e.name),
    body: `<form id="entryForm" class="form">
      <div data-amount></div>
      <label class="field">Mahlzeit<select name="meal">${Object.entries(MEALS).map(([k, l]) => `<option value="${k}"${k === e.meal ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <p class="hint" data-calc-hint></p>
    </form>`,
    footer: `<button type="button" class="btn danger ghost" data-delete>${icon("trash")} Löschen</button>
      <span class="grow"></span>
      <button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="submit" class="btn primary" form="entryForm">Speichern</button>`,
  });
  const form = $("#entryForm", sheet);
  const amt = amountBox($("[data-amount]", sheet), food, e.grams, (g) => {
    const k = (key) => num((per100(key) * g) / 100, 1);
    $("[data-calc-hint]", sheet).textContent = `${num(g, 1)} g · ${k("kcal")} kcal · E ${k("protein")} g · K ${k("carbs")} g · F ${k("fat")} g`;
  });
  submitOnce(form, async () => {
    const grams = amt.grams();
    if (!(grams > 0)) return toast("Bitte eine Menge eintragen.", { error: true });
    try {
      await api(`food-log/${id}`, { method: "PATCH", body: { grams, meal: form.elements.meal.value } });
      closeSheet();
      toast("Gespeichert");
      window.dispatchEvent(new CustomEvent("data-changed"));
    } catch (err) { formError(form, err); }
  });
  armDelete($("[data-delete]", sheet), () => {
    closeSheet();
    deleteLater(`food-log/${id}`, "Eintrag gelöscht", { onDone: () => { data = null; } });
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
