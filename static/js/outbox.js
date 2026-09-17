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
  save(items);
  changed();
  flush();
  return entry;
}

/**
 * Speichern mit Netz, sonst in den Ausgang. Gibt zurueck, ob es direkt geklappt hat.
 * Fehler, die der Server bewusst meldet (400/409), landen NICHT im Ausgang – die
 * wuerden dort ewig liegen bleiben.
 */
export async function send(entry) {
  try {
    const data = await api(entry.path, { method: "POST", body: entry.body });
    celebrate(data);
    return { ok: true, queued: false, data };
  } catch (e) {
    if (!e.offline) throw e;
    queue(entry);
    return { ok: true, queued: true };
  }
}

/** Den Ausgang der Reihe nach leeren. Bricht beim ersten Netzproblem ab. */
export async function flush({ silent = true } = {}) {
  if (sending) return { sent: 0, left: count() };
  const items = list();
  if (!items.length) return { sent: 0, left: 0 };
  sending = true;
  let sent = 0;
  try {
    while (true) {
      const rest = list();
      if (!rest.length) break;
      const entry = rest[0];
      try {
        const data = await api(entry.path, { method: "POST", body: entry.body });
        celebrate(data);
        save(list().slice(1));
        sent += 1;
        changed();
      } catch (e) {
        if (e.offline) break; // weiter geht es beim naechsten Versuch
        // Der Server lehnt den Eintrag inhaltlich ab – liegen lassen hilft nicht.
        console.warn("Eintrag abgelehnt, wird verworfen:", entry, e.message);
        save(list().slice(1));
        changed();
        if (!silent) toast(`Nicht gespeichert: ${e.message}`, { error: true });
      }
    }
  } finally {
    sending = false;
  }
  if (sent) {
    window.dispatchEvent(new CustomEvent("data-changed"));
    if (!silent || sent > 0) {
      toast(sent === 1 ? "Einheit nachträglich gespeichert"
        : `${sent} Einträge nachträglich gespeichert`);
    }
  }
  return { sent, left: count() };
}

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

/** Neu erreichte Meilensteine feiern (die API liefert sie beim Speichern mit). */
export function celebrate(data) {
  for (const m of data?.milestones || []) {
    toast(`🏅 Erreicht: ${m.title}`, { duration: 5000 });
  }
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
