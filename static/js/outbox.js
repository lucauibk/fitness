/* Ausgang: alles, was geschrieben werden soll, geht hier durch.
 *
 * Im Fitnessstudio gibt es oft kein Netz. Statt beim Speichern zu scheitern, legt die App
 * den Eintrag in eine Liste im Browser-Speicher und schickt ihn später – beim naechsten
 * Netz, beim App-Start oder wenn die App wieder in den Vordergrund kommt.
 *
 * Doppelt gespeichert werden kann nichts: jede Einheit traegt eine client_id, und der
 * Server legt zu derselben client_id nur einen Datensatz an. Ein Eintrag verlaesst den
 * Ausgang erst nach einer bestaetigten Antwort.
 */
import { api, prefs, toast } from "./util.js";

const KEY = "outbox";
let sending = false;

export const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`);

export const list = () => prefs.get(KEY, []) || [];
const save = (items) => prefs.set(KEY, items);

export const count = () => list().length;

function changed() {
  window.dispatchEvent(new CustomEvent("outbox-changed"));
}

/** Eintrag in den Ausgang legen und gleich einen Sendeversuch starten. */
export function queue(entry) {
  const items = list();
  items.push({ ...entry, queued_at: new Date().toISOString(), tries: 0 });
  // Ohne Platz im Geraetespeicher waere "wird gesendet" gelogen – lieber laut scheitern.
  if (!save(items)) throw new Error("Gerätespeicher voll – der Eintrag konnte nicht zwischengespeichert werden.");
  changed();
  flush();
  return entry;
}

/** 5xx (Pi startet gerade neu, tailscale serve liefert 502) und 429 sind voruebergehend –
 *  so ein Eintrag bleibt liegen wie bei fehlendem Netz. */
const temporary = (e) => e.offline || e.status === 429 || (e.status >= 500);

/**
 * Speichern mit Netz, sonst in den Ausgang. Gibt zurueck, ob es direkt geklappt hat.
 * Fehler, die der Server bewusst meldet (400/409), landen NICHT im Ausgang – die
 * wuerden dort ewig liegen bleiben. Voruebergehende (5xx, 429) schon.
 */
export async function send(entry) {
  try {
    const data = await api(entry.path, { method: "POST", body: entry.body });
    celebrate(data);
    return { ok: true, queued: false, data };
  } catch (e) {
    if (!temporary(e)) throw e;
    queue(entry);
    return { ok: true, queued: true };
  }
}

/** Wie ein Eintrag in Meldungen heisst – "1 Einheit wartet" passt nicht zu einem Glas Wasser. */
const LABELS = {
  workouts: ["Einheit", "Einheiten"], cardio: ["Cardio-Einheit", "Cardio-Einheiten"],
  checkin: ["Check-in", "Check-ins"], "mobility/log": ["Mobility", "Mobility-Einheiten"],
  "water-log": ["Wasser-Eintrag", "Wasser-Einträge"], "food-log": ["Essens-Eintrag", "Essens-Einträge"],
  bodyweight: ["Gewicht", "Gewichtseinträge"], measurements: ["Messung", "Messungen"],
};
export function label(path, n = 1) {
  const [one, many] = LABELS[path] || ["Eintrag", "Einträge"];
  return n === 1 ? one : many;
}

/** Vom Server abgelehnte Eintraege: nicht wegwerfen, sondern beiseitelegen. Sie tauchen in
 *  keiner Liste mehr auf, sind aber im Geraetespeicher noch da, falls man sie braucht. */
const REJECTED = "outbox-rejected";
const MAX_TRIES = 10;
function reject(entry, message) {
  const kept = prefs.get(REJECTED, []) || [];
  kept.push({ ...entry, rejected_at: new Date().toISOString(), reason: message });
  prefs.set(REJECTED, kept.slice(-50));
}

/** Den Ausgang der Reihe nach leeren. Bricht beim ersten voruebergehenden Problem ab. */
export async function flush() {
  if (sending) return { sent: 0, left: count() };
  const items = list();
  if (!items.length) return { sent: 0, left: 0 };
  sending = true;
  const sent = [];
  try {
    while (true) {
      const rest = list();
      if (!rest.length) break;
      const entry = rest[0];
      try {
        const data = await api(entry.path, { method: "POST", body: entry.body });
        celebrate(data);
        save(list().slice(1));
        sent.push(entry.path);
        changed();
      } catch (e) {
        // Ein 500, der bei jedem Versuch kommt, ist ein Fehler im Server und kein Neustart –
        // nach zehn Anlaeufen beiseitelegen, sonst blockiert er alles dahinter.
        const stuck = !e.offline && (entry.tries || 0) >= MAX_TRIES;
        if (temporary(e) && !stuck) {
          // weiter geht es beim naechsten Versuch
          // Nur Server-Fehler zaehlen – ein Tag ohne Netz soll keinen Eintrag kosten.
          if (!e.offline) save([{ ...entry, tries: (entry.tries || 0) + 1 }, ...list().slice(1)]);
          break;
        }
        // Der Server lehnt den Eintrag inhaltlich ab – liegen lassen hilft nicht.
        console.warn("Eintrag abgelehnt, beiseitegelegt:", entry, e.message);
        reject(entry, e.message);
        save(list().slice(1));
        changed();
        toast(`${label(entry.path)} vom ${relDay(entry.queued_at)} nicht gespeichert: ${e.message}`,
          { error: true, sticky: true });
      }
    }
  } finally {
    sending = false;
  }
  if (sent.length) {
    window.dispatchEvent(new CustomEvent("data-changed"));
    const same = sent.every((p) => p === sent[0]);
    const what = same ? label(sent[0], sent.length) : "Einträge";
    toast(sent.length === 1 ? `${what} nachträglich gespeichert`
      : `${sent.length} ${what} nachträglich gespeichert`);
  }
  return { sent: sent.length, left: count() };
}

const relDay = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "vorhin" : d.toLocaleDateString("de-AT", { day: "numeric", month: "numeric" });
};

/** Eintraege eines Typs, die noch im Ausgang liegen – die Oberflaeche zeigt sie
 *  trotzdem an, damit nichts verloren wirkt. */
export function pending(path) {
  return list().filter((e) => e.path === path).map((e) => ({
    ...e.body,
    id: `pending-${e.body.client_id}`,
    pending: true,
  }));
}

export const pendingWorkouts = () => pending("workouts")
  .map((w) => ({ ...w, sets: (w.sets || []).map((s) => ({ ...s })) }));

/** Neu erreichte Meilensteine feiern (die API liefert sie beim Speichern mit). Die
 *  eigentliche Feier hoert auf das "milestones"-Event (main.js) – hier nur melden. */
export function celebrate(data) {
  if (data?.milestones?.length) window.dispatchEvent(new CustomEvent("milestones", { detail: data.milestones }));
}

export function start() {
  window.addEventListener("online", () => flush());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") flush();
  });
  window.addEventListener("api-online", () => { if (count()) flush(); });
  flush();
}
