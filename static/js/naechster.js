/** Was kommt nach der Pause? Reine Logik ohne DOM, damit die Pausenleiste (training.js)
 *  den naechsten Satz zeigen kann und sich das unter Node testen laesst. */

const kg = (v) => String(v).replace(".", ",");

/** Der naechste offene Satz. Zuerst in der Uebung `from` (die mit dem gerade abgehakten Satz),
 *  sonst in der naechsten Uebung mit offenen Saetzen – in derselben Reihenfolge wie
 *  advanceFocus in training.js, damit Anzeige und aufgeklappte Karte zusammenpassen. */
export function nextSet(draft, from) {
  const list = draft?.exercises || [];
  const n = list.length;
  if (!n) return null;
  const start = Number.isInteger(from) && from >= 0 && from < n ? from : 0;
  for (let k = 0; k < n; k += 1) {
    const exIndex = (start + k) % n;
    const ex = list[exIndex];
    const setIndex = ex.sets.findIndex((s) => !s.done);
    if (setIndex < 0) continue;
    const s = ex.sets[setIndex];
    // Aufwaermsaetze zaehlen nicht mit – wie die Nummer in der Satz-Zeile
    const no = ex.sets.slice(0, setIndex + 1).filter((x) => !x.warmup).length;
    return { exIndex, setIndex, name: ex.name, unit: ex.unit, warmup: Boolean(s.warmup), no,
             reps: s.reps ?? null, weight: s.weight ?? null, sameExercise: exIndex === start };
  }
  return null;
}

/** Kurztext fuer die Pausenleiste, z. B. "Bankdrücken · Satz 3 · 8 × 62,5 kg". */
export function nextLabel(next) {
  if (!next) return "";
  const body = next.unit === "koerper";
  const weight = next.weight == null || (body && !next.weight) ? null
    : `${body ? "+" : ""}${kg(next.weight)} kg`;
  const load = next.reps != null && weight ? `${next.reps} × ${weight}`
    : next.reps != null ? `${next.reps} Whd.` : weight;
  return [next.name, next.warmup ? "Aufwärmsatz" : `Satz ${next.no}`, load].filter(Boolean).join(" · ");
}
