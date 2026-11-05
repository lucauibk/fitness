/** Shopping-Topf: Jede Einheit legt Geld in den Topf, davon wird die Wunschliste gekauft.
 *  Belohnt wird das Training, nicht die Waage (Logik in app/rewards.py). */
import { $, actionMenu, api, busy, closeSheet, esc, formData, formError, icon, openSheet, submitOnce,
  toast } from "./util.js";

const euro = (v) => `${Number(v || 0).toLocaleString("de-AT", { minimumFractionDigits: 0, maximumFractionDigits: 2 })} €`;
const trainings = (n) => `${n} ${n === 1 ? "Training" : "Trainings"}`;
const changed = () => window.dispatchEvent(new CustomEvent("data-changed"));

function thumb(item, cls = "") {
  return item?.image ? `<img class="topf-thumb ${cls}" src="${esc(item.image)}" alt="">`
    : `<span class="row-icon ${cls}">${icon("bag")}</span>`;
}

function progress(item, balance) {
  const pct = Math.min(100, Math.round((balance / item.price) * 100));
  return `<div class="progress sm" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"
    aria-label="${esc(item.name)}: ${pct} %"><i style="width:${pct}%"></i></div>`;
}

const goalLine = (g) => (g.affordable ? "Genug im Topf – gönn es dir!"
  : `Noch ${trainings(g.workouts_left)} (${euro(g.missing)})`);

/** Karte fuer "Heute". r = rewards.card() vom Server oder null (Topf aus). */
export function topfCard(r) {
  if (!r) return "";
  const g = r.goal;
  return `<div class="card mini-card topf-card" role="button" tabindex="0" data-topf>
    ${thumb(g, "lg")}
    <div class="grow"><p class="eyebrow">Shopping-Topf · ${euro(r.balance)}${r.earned_today ? ` · heute +${euro(r.earned_today)}` : ""}</p>
      ${g ? `<div class="card-title">${esc(g.name)} · ${euro(g.price)}</div>
        <p class="hint">${goalLine(g)}</p>${progress(g, r.balance)}`
        : `<div class="card-title">Wunschliste ist leer</div>
        <p class="hint">Trag dein nächstes Lieblingsteil ein – jedes Training bringt ${euro(r.rate)}.</p>`}
    </div>
    ${g?.affordable ? `<span class="pill ok">kaufbar</span>` : icon("right", "chev")}
  </div>`;
}

// ------------------------------------------------------------ Sheet
let state = null;

function itemRow(it) {
  return `<div class="row-item">
    ${thumb(it)}
    <div class="row-main"><div class="row-title">${esc(it.name)}</div>
      <div class="row-meta"><span>${euro(it.price)}</span><span>${it.affordable ? "kaufbar" : `noch ${trainings(it.workouts_left)}`}</span></div>
      ${progress(it, state.balance)}</div>
    ${it.affordable ? `<button type="button" class="btn small primary" data-buy="${it.id}">Kaufen</button>` : ""}
    <button type="button" class="icon-btn" data-more="${it.id}" aria-label="Mehr zu ${esc(it.name)}">⋯</button>
  </div>`;
}

function body() {
  const s = state;
  if (!s.enabled) {
    const paused = s.balance || s.open.length
      ? `<p class="hint">Pausiert · im Topf liegen ${euro(s.balance)}, ${s.open.length} ${s.open.length === 1 ? "Wunsch wartet" : "Wünsche warten"}.</p>` : "";
    return `${paused}<p>Für jedes Training kommt ein fester Betrag in deinen Topf. Davon „kaufst“ du dir die Teile
      auf deiner Wunschliste – so zählt jedes Training, auch wenn die Waage mal stehen bleibt.</p>
      ${rateForm(5)}`;
  }
  return `<div class="kpis compact">
      <div><span class="big-number">${euro(s.balance)}</span><span>im Topf</span></div>
      <div><span class="big-number">${euro(s.rate)}</span><span>pro Training</span></div>
    </div>
    ${s.open.length ? `<h3 class="group-title">Wunschliste</h3><div class="list flush">${s.open.map(itemRow).join("")}</div>
      <p class="hint">Das oberste Teil ist dein Ziel auf „Heute“.</p>`
      : `<p class="hint pad">Noch nichts auf der Liste.</p>`}
    <details class="field-more" ${s.open.length ? "" : "open"}><summary>${icon("plus")} Wunsch hinzufügen</summary>
      <form id="topfItem" class="form">
        <label class="field">Was?<input name="name" maxlength="80" required placeholder="z. B. Jeans von Zara"></label>
        <div class="field-row">
          <label class="field narrow">Preis (€)<input name="price" type="number" inputmode="decimal" min="1" max="10000" step="0.01" required></label>
          <label class="field">Link<input name="link" type="url" maxlength="500" placeholder="optional, https://…"></label>
        </div>
        <label class="btn small ghost">${icon("camera")} Foto wählen
          <input type="file" accept="image/*" data-photo hidden></label>
        <img class="topf-thumb lg" data-preview alt="" hidden>
        <div class="btn-row"><button type="submit" class="btn primary">${icon("plus")} Auf die Liste</button></div>
      </form>
    </details>
    ${s.bought.length ? `<h3 class="group-title">Schon gegönnt<span class="count-muted">${s.bought.length}</span></h3>
      <div class="list flush">${s.bought.map((it) => `<div class="row-item is-done">${thumb(it)}
        <div class="row-main"><div class="row-title">${esc(it.name)}</div>
          <div class="row-meta"><span>${euro(it.price)}</span></div></div>
        <button type="button" class="btn small ghost" data-unbuy="${it.id}">Rückgängig</button></div>`).join("")}</div>` : ""}
    <details class="field-more"><summary>Betrag pro Training ändern</summary>${rateForm(s.rate, true)}</details>`;
}

function rateForm(value, canDisable = false) {
  return `<form id="topfRate" class="form">
    <label class="field">Betrag pro Training (€)
      <input name="rate" type="number" inputmode="decimal" min="0.5" max="100" step="0.5" required value="${value}"></label>
    <p class="hint">Gilt ab dem nächsten Training – was schon im Topf ist, bleibt drin.</p>
    <div class="btn-row"><button type="submit" class="btn ${canDisable ? "" : "primary"}">${canDisable ? "Speichern" : "Topf starten"}</button>
      ${canDisable ? `<button type="button" class="btn ghost danger" data-rate-off>Topf pausieren</button>` : ""}</div>
  </form>`;
}

/** Foto auf 480 px verkleinern – reist mit jeder "Heute"-Antwort mit, muss also klein bleiben. */
function shrink(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, 480 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.8));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Bild konnte nicht gelesen werden.")); };
    img.src = url;
  });
}

function paint(sheet) {
  $(".sheet-body", sheet).innerHTML = body();
  let photo = null;
  const rate = $("#topfRate", sheet);
  submitOnce(rate, async () => {
    try {
      state = await api("rewards/rate", { method: "PUT", body: formData(rate) });
      toast("Gespeichert");
      paint(sheet);
      changed();
    } catch (e) { formError(rate, e); }
  });
  const form = $("#topfItem", sheet);
  if (!form) return;
  $("[data-photo]", form).addEventListener("change", async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try {
      photo = await shrink(file);
      const prev = $("[data-preview]", form);
      prev.src = photo;
      prev.hidden = false;
    } catch (e) { formError(form, e); }
  });
  submitOnce(form, async () => {
    try {
      state = await api("wishlist", { method: "POST", body: { ...formData(form), image: photo } });
      toast("Auf der Wunschliste");
      paint(sheet);
      changed();
    } catch (e) { formError(form, e); }
  });
}

async function act(sheet, button, path, method, message) {
  await busy(button, async () => {
    try {
      state = await api(path, { method });
      if (message) toast(message);
      paint(sheet);
      changed();
    } catch (e) { toast(e.message, { error: true }); }
  });
}

/** Ein Teil nach oben holen = neues Ziel. Die Reihenfolge wird dafuer neu durchnummeriert. */
async function makeGoal(id) {
  const order = [id, ...state.open.map((i) => i.id).filter((x) => x !== id)];
  for (const [pos, itemId] of order.entries()) {
    state = await api(`wishlist/${itemId}`, { method: "PATCH", body: { position: pos } });
  }
}

function more(id) {
  const it = state.open.find((i) => i.id === id);
  actionMenu(it.name, [
    state.open[0]?.id !== id && { label: "Als Ziel festlegen", icon: "up",
      run: async () => { try { await makeGoal(id); changed(); } catch (e) { toast(e.message, { error: true }); } openTopf(); } },
    it.link && { label: "Im Shop ansehen", icon: "right", run: () => { window.open(it.link, "_blank", "noopener"); openTopf(); } },
    { label: "Von der Liste nehmen", icon: "trash", danger: true,
      run: async () => {
        try { state = await api(`wishlist/${id}`, { method: "DELETE" }); changed(); } catch (e) { toast(e.message, { error: true }); }
        openTopf();
      } },
  ]);
}

export async function openTopf() {
  const sheet = openSheet({ title: "Shopping-Topf", body: `<p class="hint pad">Lädt …</p>` });
  try { state = await api("rewards"); } catch (e) { closeSheet(); toast(e.message, { error: true }); return; }
  paint(sheet);
  sheet.addEventListener("click", (ev) => {
    const t = (s) => ev.target.closest(s);
    const buy = t("[data-buy]");
    if (buy) {
      const it = state.open.find((i) => i.id === Number(buy.dataset.buy));
      return act(sheet, buy, `wishlist/${it.id}/buy`, "POST", `Gegönnt: ${it.name}! Selbst ertrainiert.`);
    }
    const unbuy = t("[data-unbuy]");
    if (unbuy) return act(sheet, unbuy, `wishlist/${unbuy.dataset.unbuy}/unbuy`, "POST", "Zurück in den Topf");
    const off = t("[data-rate-off]");
    if (off) {
      return busy(off, async () => {
        try { state = await api("rewards/rate", { method: "PUT", body: { rate: null } }); paint(sheet); changed(); }
        catch (e) { toast(e.message, { error: true }); }
      });
    }
    const m = t("[data-more]");
    if (m) return more(Number(m.dataset.more));
  });
}
