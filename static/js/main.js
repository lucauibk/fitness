/** Einstieg: Tab-Verwaltung, Profil-Umschalter und die Offline-Hinweise. */
import { $, $$, ago, api, closeSheet, empty, esc, getProfile, icon, openSheet, prefs,
  setProfile, submitOnce, toast, formData, formError, safeColor } from "./util.js";
import * as outbox from "./outbox.js";
import * as heute from "./heute.js";
import * as training from "./training.js";
import * as plan from "./plan.js";
import * as fortschritt from "./fortschritt.js";

const VIEWS = {
  heute: ["Heute", heute],
  training: ["Training", training],
  plan: ["Plan", plan],
  fortschritt: ["Fortschritt", fortschritt],
};
const mounted = new Set();
let current = null;
let loading = null;
let again = false;
let profiles = [];

const parseHash = () => {
  const tab = location.hash.replace(/^#/, "").split("/")[0];
  return VIEWS[tab] ? tab : "heute";
};

async function show() {
  const tab = parseHash();
  const switched = tab !== current;
  current = tab;
  closeSheet(false);

  $$(".view").forEach((el) => { el.hidden = el.id !== `view-${tab}`; });
  $$("#nav a[data-tab]").forEach((a) => a.setAttribute("aria-current", a.dataset.tab === tab ? "page" : "false"));
  $("#pageTitle").textContent = VIEWS[tab][0];
  document.title = `${VIEWS[tab][0]} · Fitness`;

  const el = $(`#view-${tab}`);
  try {
    if (!mounted.has(tab)) {
      await VIEWS[tab][1].mount(el);
      mounted.add(tab);
    }
    await reload();
  } catch (e) {
    console.error(e);
    if (!mounted.has(tab)) el.innerHTML = errorState(e);
  }
  if (switched) window.scrollTo(0, 0);
}

const errorState = (e) => empty("Konnte nicht laden", esc(e.message),
  { label: "Erneut versuchen", attrs: "data-retry" });

/** Aktuelle Ansicht neu laden; parallele Aufrufe werden zusammengefasst. */
async function reload() {
  // Ohne Profil ist keine Anfrage sinnvoll – der Server lehnt sie zu Recht ab.
  // Das kann in den ersten Millisekunden nach dem Start vorkommen.
  if (!getProfile()) return;
  if (loading) { again = true; return loading; }
  loading = (async () => {
    do {
      again = false;
      try {
        await VIEWS[current][1].load();
        updateBadge();
      } catch (e) {
        const el = $(`#view-${current}`);
        if (el.querySelector(".skeleton")) el.innerHTML = errorState(e);
        else if (!e.offline) toast(e.message, { error: true });
      }
    } while (again);
    loading = null;
  })();
  return loading;
}

function updateBadge() {
  const badge = $('#nav [data-tab="training"] .count');
  const running = Boolean(prefs.get("draft"));
  badge.hidden = !running;
}

// ------------------------------------------------------------ Profile
async function loadProfiles() {
  // Zuerst das zuletzt gewaehlte Profil aus dem Geraetespeicher setzen: ohne das wuerde
  // ein Start ohne Netz gar keine Anfrage stellen duerfen (jede braucht ein Profil).
  const remembered = prefs.get("profile");
  if (remembered) {
    setProfile(remembered);
    $("#profileName").textContent = prefs.get("profileName") || "Profil";
    $("#profileDot").style.setProperty("--c", safeColor(prefs.get("profileColor")));
  }
  profiles = await api("profiles");
  if (!profiles.length) return;
  const chosen = profiles.some((p) => p.id === remembered) ? remembered : profiles[0].id;
  applyProfile(chosen);
}

function applyProfile(id) {
  const p = profiles.find((x) => x.id === id) || profiles[0];
  if (!p) return;
  setProfile(p.id);
  prefs.set("profile", p.id);
  prefs.set("profileName", p.name);
  prefs.set("profileColor", p.color);
  $("#profileName").textContent = p.name;
  $("#profileDot").style.setProperty("--c", safeColor(p.color));
}

function openProfiles() {
  const active = getProfile();
  const sheet = openSheet({
    title: "Profil",
    body: `<div class="list">${profiles.map((p) => `
        <div class="row-item" role="button" tabindex="0" data-profile="${p.id}">
          <span class="dot" style="--c:${safeColor(p.color)}"></span>
          <div class="row-main"><div class="row-title">${esc(p.name)}</div></div>
          ${p.id === active ? `<span class="pill ok">aktiv</span>` : ""}
        </div>`).join("")}</div>
      <details class="field-more">
        <summary>Neues Profil</summary>
        <form id="profileForm" class="form">
          <label class="field">Name<input name="name" maxlength="40" required placeholder="z. B. Mia"></label>
          <label class="field">Farbe<input name="color" type="color" value="#2c8a55"></label>
          <div class="btn-row"><button type="submit" class="btn primary">Profil anlegen</button></div>
        </form>
      </details>
      <p class="hint pad">Jedes Profil hat eigene Splits, Einheiten und Auswertungen.
        Der Übungs-Katalog ist für alle gleich.</p>`,
  });
  sheet.addEventListener("click", (ev) => {
    const row = ev.target.closest("[data-profile]");
    if (!row) return;
    applyProfile(Number(row.dataset.profile));
    closeSheet();
    // Profilwechsel heisst: alles neu laden, nicht nur die Anzeige umschreiben.
    mounted.clear();
    $$(".view").forEach((el) => { el.innerHTML = ""; });
    show();
  });
  const form = $("#profileForm", sheet);
  submitOnce(form, async () => {
    try {
      const p = await api("profiles", { method: "POST", body: formData(form) });
      profiles.push(p);
      applyProfile(p.id);
      closeSheet();
      toast(`Profil „${p.name}“ angelegt`);
      mounted.clear();
      $$(".view").forEach((el) => { el.innerHTML = ""; });
      show();
    } catch (e) { formError(form, e); }
  });
}

// ------------------------------------------------------------ Offline
const banner = $("#banner");
let staleRetry = null;
// Zeitpunkt des zuletzt aus dem Vorrat gelieferten Stands. Solange es einen gibt, ist
// "Offline · Stand …" die ehrlichere und ruhigere Aussage als "Server nicht erreichbar" –
// eine einzelne, noch nie geladene Anfrage soll den Hinweis nicht in einen Fehler kippen.
let lastStale = null;

function setBanner({ text, quiet = true, action = null, onAction = null }) {
  if (banner.dataset.text === text && !banner.hidden) return;
  banner.dataset.text = text;
  banner.classList.toggle("quiet", quiet);
  banner.setAttribute("role", quiet ? "status" : "alert");
  $("#bannerText").textContent = text;
  const btn = $("#bannerAction");
  btn.hidden = !action;
  btn.textContent = action || "";
  btn.onclick = onAction;
  banner.hidden = false;
}

function hideBanner() {
  banner.hidden = true;
  delete banner.dataset.text;
}

/** Der Ausgang hat Vorrang vor dem Offline-Hinweis: „wartet auf Verbindung" sagt mehr. */
function updateBanner(detail) {
  const waiting = outbox.count();
  if (waiting) {
    setBanner({
      text: waiting === 1 ? "1 Einheit wartet auf Verbindung"
        : `${waiting} Einträge warten auf Verbindung`,
      action: "Jetzt senden",
      onAction: () => outbox.flush({ silent: false }).then(() => { updateBanner(); reload(); }),
    });
    return;
  }
  if (detail === "online") { lastStale = null; return hideBanner(); }
  if (detail && detail !== "offline") lastStale = detail;
  if (lastStale) {
    setBanner({ text: `Offline · Stand ${ago(lastStale)}`, action: "Erneut versuchen", onAction: retry });
    return;
  }
  if (detail === "offline") {
    setBanner({ text: "Server nicht erreichbar.", quiet: false, action: "Erneut versuchen", onAction: retry });
  }
}

window.addEventListener("api-offline", () => updateBanner("offline"));
window.addEventListener("api-stale", (e) => {
  // Solange offline: alle 30 s still neu versuchen, damit der Hinweis von selbst verschwindet
  if (!staleRetry) {
    staleRetry = setTimeout(() => {
      staleRetry = null;
      if (!document.hidden && !document.getElementById("sheet")) reload();
    }, 30000);
  }
  updateBanner(e.detail || "offline");
});
window.addEventListener("api-online", () => updateBanner("online"));
window.addEventListener("outbox-changed", () => { updateBanner(); updateBadge(); });
window.addEventListener("online", () => reload());

function retry() {
  const el = $(`#view-${current}`);
  if (el?.querySelector("[data-retry]")) {
    const fresh = el.cloneNode(false);
    el.replaceWith(fresh);
    mounted.delete(current);
  }
  show();
}

// ------------------------------------------------------------ Ereignisse
window.addEventListener("hashchange", show);
window.addEventListener("data-changed", () => reload());
$("#profileBtn").addEventListener("click", openProfiles);
document.addEventListener("click", (e) => { if (e.target.closest("[data-retry]")) retry(); });
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest?.("input, textarea, select, [contenteditable]");
  if (typing || e.metaKey || e.ctrlKey || e.altKey || document.getElementById("sheet")) return;
  const keys = { 1: "heute", 2: "training", 3: "plan", 4: "fortschritt" };
  if (keys[e.key]) location.hash = keys[e.key];
});

// Offene Seite auffrischen – aber nicht, waehrend jemand tippt oder trainiert.
setInterval(() => {
  if (document.hidden || document.getElementById("sheet")
      || document.activeElement?.matches("input, textarea")
      || prefs.get("draft")) return;
  reload();
}, 120000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && current && !prefs.get("draft")) reload();
});

// ------------------------------------------------------- Offline-Vorrat
/** Die Daten im Hintergrund holen, damit im Gym auch Tabs funktionieren, die zuletzt
 *  nicht offen waren. Hoechstens alle 10 Minuten. */
function warmOfflineCache() {
  if (!navigator.serviceWorker?.controller || !navigator.onLine) return;
  const profile = getProfile();
  if (!profile) return;
  const last = Number(prefs.get("offline.warmed") || 0);
  if (Date.now() - last < 10 * 60 * 1000) return;
  prefs.set("offline.warmed", Date.now());
  const paths = ["profiles", "exercises", `today?profile=${profile}`, `splits?profile=${profile}`,
    `progress?profile=${profile}`, `workouts?profile=${profile}&limit=30`,
    `bodyweight?profile=${profile}`];
  paths.forEach((p, i) => setTimeout(() => fetch(`/api/${p}`).catch(() => {}), 300 * i));
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("Service Worker:", e));
  navigator.serviceWorker.addEventListener("controllerchange", () => setTimeout(warmOfflineCache, 1000));
  setTimeout(warmOfflineCache, 4000);
}

(async () => {
  try {
    await loadProfiles();
  } catch (e) {
    console.warn("Profile konnten nicht geladen werden:", e.message);
  }
  outbox.start();
  updateBanner();
  show();
})();
