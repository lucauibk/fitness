/** Satzpause je Übung. Reine Logik ohne DOM (training.js nutzt sie, Tests laufen unter Node).
 *
 *  Grundlage ist die Pause aus dem Plan (rest_seconds), ohne Planwert ein Standard nach
 *  Übungsart. Wer in der Pausenleiste ±15 s tippt, will diese Pause bei dieser Übung meist
 *  immer so – die App merkt sich die Länge pro Übung auf dem Gerät (je Profil) und nimmt
 *  sie ab dem nächsten Satz. Der Plan selbst bleibt unverändert. */

export const REST = { compound: 120, isolation: 75 };
// Gleiche Grenzen wie rest_seconds im Plan (app/main.py)
export const REST_MIN = 30;
export const REST_MAX = 300;

const clamp = (s) => Math.min(REST_MAX, Math.max(REST_MIN, Math.round(s)));

/** Pause laut Plan, ohne Planwert der Standard nach Übungsart. */
export function planRest(ex) {
  return ex?.rest || (ex?.kind === "compound" ? REST.compound : REST.isolation);
}

/** Pause, die nach einem Satz dieser Übung läuft: gemerkte Länge vor Planwert. */
export function restFor(ex, saved) {
  const own = saved?.[ex?.exercise_id];
  return Number.isFinite(own) ? clamp(own) : planRest(ex);
}

/** Die gemerkte Länge um `delta` Sekunden verschieben. Liefert die neue Liste (die alte
 *  bleibt unverändert) und die neue Länge. Landet man wieder beim Planwert, fällt der
 *  Eintrag weg – dann gilt der Plan wieder, auch wenn er sich später ändert. */
export function rememberRest(saved, ex, delta) {
  const id = ex?.exercise_id;
  if (id == null || !Number.isFinite(delta)) return { saved: saved || {}, seconds: null };
  const seconds = clamp(restFor(ex, saved) + delta);
  const out = { ...(saved || {}) };
  if (seconds === planRest(ex)) delete out[id];
  else out[id] = seconds;
  return { saved: out, seconds };
}

/** 135 → "2:15" */
export const formatRest = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** Rückmeldung nach ±15 s: was ab jetzt für diese Übung gilt. */
export function restNote(ex, seconds) {
  if (!ex || seconds == null) return "";
  const name = ex.name || "dieser Übung";
  return seconds === planRest(ex) ? `Pause bei ${name} wieder wie im Plan (${formatRest(seconds)}).`
    : `Pause bei ${name} ab jetzt ${formatRest(seconds)}.`;
}
