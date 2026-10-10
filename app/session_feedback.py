"""Urteil über eine fertige Einheit: wie gut sie war und was zum perfekten Training fehlte.

Die Bewertung wird gerechnet, nicht gespeichert – dieselbe Linie wie in `stats.py`: einzige
Quelle sind die geloggten Sätze, der Trainingstag und die Vorgänger-Einheit. Es gibt also
keine Spalte, die irgendwann neben den Rohdaten herläuft.

Zwei Eigenheiten, die Absicht sind:

* Der Vergleich "hat sich etwas bewegt?" läuft über `_previous_sets()` und nicht über
  `stats.last_sets()`. Letzteres liefert immer die *neueste* Einheit mit dieser Übung – für
  eine ältere Einheit im Verlauf wäre das der falsche Gegner (sie würde an Daten gemessen,
  die es damals noch gar nicht gab).
* Die Plan-Vorgaben (`target_sets`, Wiederholungs-Bereich) werden dagegen im *aktuellen*
  Stand gelesen. Verschiebt man später die Zielwerte, verschiebt sich auch das Urteil über
  alte Einheiten. Das liesse sich nur durch Speichern vermeiden, und genau das soll hier
  nicht passieren.
"""
from datetime import datetime, timezone

from . import db
from .planner import volume_plan as volume

# Gewicht je Kriterium. Summe 100, wenn alle greifen – sonst wird auf die anwendbaren
# normiert, damit freies Training nicht für fehlende Vorgaben bestraft wird.
WEIGHTS = {"plan": 35, "reps": 15, "progression": 20, "intensitaet": 15,
           "aufwaermen": 8, "dauer": 7}

LABELS = [(85, "stark"), (70, "solide"), (50, "ausbaufähig"), (0, "mager")]

# Untergrenze für die Normierung. Ohne sie würden bei freiem Training – wo Plan-Erfüllung und
# Wiederholungs-Bereich mangels Vorgaben herausfallen – die verbliebenen Kriterien plötzlich
# das Dreifache wiegen und ein ordentliches Training auf "mager" drücken. Fehlende Vorgaben
# dürfen die übrigen Kriterien nicht schärfer machen.
BASIS = 60

# Zielfenster für die Anstrengung (RPE): darunter bleibt Reserve liegen, darüber wird
# dauerhaft am Muskelversagen trainiert.
RPE_MIN = 7.0
RPE_MAX = 9.5


def _num(value):
    return value if isinstance(value, (int, float)) else None


def _work_sets(sets):
    """Arbeitssätze – Aufwärmsätze zählen nirgends mit, wie in `stats._sets`."""
    return [s for s in sets if not s.get("warmup")]


def _by_exercise(sets):
    out = {}
    for s in sets:
        out.setdefault(s["exercise_id"], []).append(s)
    return out


def _at(workout):
    return workout.get("started_at") or workout.get("finished_at") or ""


def _plan_items(split_day_id):
    if not split_day_id:
        return []
    return db.rows(
        "SELECT se.*, e.name, e.muscle_group, e.unit, e.kind FROM split_exercises se"
        " JOIN exercises e ON e.id = se.exercise_id"
        " WHERE se.split_day_id=? ORDER BY se.position, se.id", (split_day_id,))


def _kinds(exercise_ids):
    """`db.get_workout()` liefert `kind` nicht mit – ohne das würde das Aufwärm-Kriterium
    stillschweigend nie auslösen. Also hier gezielt nachholen, statt die gemeinsam genutzte
    Abfrage zu verbreitern."""
    if not exercise_ids:
        return {}
    marks = ",".join("?" * len(exercise_ids))
    rows = db.rows(f"SELECT id, name, kind FROM exercises WHERE id IN ({marks})",
                   tuple(exercise_ids))
    return {r["id"]: r for r in rows}


def _previous_sets(profile_id, exercise_id, before_at, before_id):
    """Die Arbeitssätze der letzten Einheit mit dieser Übung *vor* der bewerteten.
    Reihenfolge wie in `db.list_workouts`, damit gleiche Zeitstempel stabil aufgelöst werden."""
    prev = db.one(
        "SELECT w.id FROM workouts w JOIN sets s ON s.workout_id = w.id"
        " WHERE w.profile_id=? AND s.exercise_id=? AND s.warmup=0"
        " AND (COALESCE(w.started_at, w.finished_at) < ?"
        "  OR (COALESCE(w.started_at, w.finished_at) = ? AND w.id < ?))"
        " ORDER BY COALESCE(w.started_at, w.finished_at) DESC, w.id DESC LIMIT 1",
        (profile_id, exercise_id, before_at, before_at, before_id))
    if not prev:
        return []
    return db.rows("SELECT set_no, reps, weight, rpe FROM sets"
                   " WHERE workout_id=? AND exercise_id=? AND warmup=0"
                   " ORDER BY set_no, id", (prev["id"], exercise_id))


def _top_set(sets):
    """Bester Satz einer Übung: schwerstes Gewicht, und dort die meisten Wiederholungen.
    Ohne Zusatzgewicht (Klimmzüge) zählen nur die Wiederholungen."""
    weights = [_num(s.get("weight")) for s in sets if _num(s.get("weight")) is not None]
    reps = [_num(s.get("reps")) or 0 for s in sets]
    if not weights:
        return None, max(reps, default=0)
    top = max(weights)
    at_top = [(_num(s.get("reps")) or 0) for s in sets if _num(s.get("weight")) == top]
    return top, max(at_top, default=0)


# ------------------------------------------------------------------ Kriterien
def _check_plan(items, done_by_ex):
    """Wurde abgearbeitet, was für den Tag vorgesehen war?"""
    total = sum((i.get("target_sets") or 0) for i in items)
    if not total:
        return None
    fehlt, offen = [], 0
    for item in items:
        target = item.get("target_sets") or 0
        done = len(done_by_ex.get(item["exercise_id"], []))
        if done >= target:
            continue
        offen += target - done
        fehlt.append({"key": "plan", "text": f"{item['name']}: ganz ausgelassen" if not done
                      else f"{item['name']}: {done} von {target} Sätzen"})
    if not fehlt:
        return {"lost": 0.0, "missing": [], "good": [f"alle {total} geplanten Sätze gemacht"]}
    return {"lost": min(1.0, offen / total), "missing": fehlt, "good": []}


def _check_reps(items, done_by_ex):
    """Wiederholungen im vorgegebenen Bereich – darunter ist das Gewicht zu hoch,
    durchgehend darüber heisst: das Gewicht ist fällig."""
    geprueft, fehlt = 0, []
    for item in items:
        sets = done_by_ex.get(item["exercise_id"], [])
        if not sets:
            continue
        lo, hi = item.get("target_reps_min"), item.get("target_reps_max")
        if not lo or not hi:
            continue
        geprueft += 1
        reps = [(_num(s.get("reps")) or 0) for s in sets]
        if any(r < lo for r in reps):
            fehlt.append({"key": "reps",
                          "text": f"{item['name']}: unter {lo} Wiederholungen – Gewicht zu hoch"})
        elif all(r > hi for r in reps):
            fehlt.append({"key": "reps",
                          "text": f"{item['name']}: überall über {hi} – Gewicht erhöhen"})
    if not geprueft:
        return None
    if not fehlt:
        return {"lost": 0.0, "missing": [], "good": ["Wiederholungen im Zielbereich"]}
    return {"lost": min(1.0, len(fehlt) / geprueft), "missing": fehlt, "good": []}


def _check_progression(profile_id, workout, done_by_ex, names):
    """Gegenüber der letzten Einheit mit derselben Übung: mehr Gewicht oder mehr
    Wiederholungen? Übungen, die zum ersten Mal vorkommen, zählen nicht mit."""
    vergleichbar, fehlt, besser = 0, [], []
    at, wid = _at(workout), workout.get("id") or 0
    for exercise_id, sets in done_by_ex.items():
        prev = _previous_sets(profile_id, exercise_id, at, wid)
        if not prev:
            continue
        vergleichbar += 1
        cur_w, cur_r = _top_set(sets)
        old_w, old_r = _top_set(prev)
        if cur_w is not None and old_w is not None:
            vorwaerts = cur_w > old_w or (cur_w == old_w and cur_r > old_r)
        elif cur_w is not None:
            vorwaerts = True
        elif old_w is not None:
            vorwaerts = False
        else:
            vorwaerts = cur_r > old_r
        name = names.get(exercise_id, {}).get("name") or sets[0].get("exercise") or "Übung"
        (besser if vorwaerts else fehlt).append(name)
    if not vergleichbar:
        return None
    if not fehlt:
        return {"lost": 0.0, "missing": [],
                "good": [f"gesteigert bei {', '.join(besser)}"] if besser else []}
    return {"lost": min(1.0, len(fehlt) / vergleichbar),
            # Gleichartige Hinweise in einer Zeile – drei fast gleiche Saetze lesen sich wie ein Tadel.
            "missing": [{"key": "progression",
                         "text": f"{', '.join(fehlt)}: kein Fortschritt gegenüber dem letzten Mal"}],
            "good": [f"gesteigert bei {', '.join(besser)}"] if besser else []}


def _check_intensity(work_sets):
    """Anstrengung (RPE): ohne Notiz lässt sich nicht beurteilen, ob hart genug trainiert
    wurde – das ist ein Mangel, aber kein Totalausfall. Die App fragt RPE einmal pro Übung
    ab (nach dem letzten Satz), deshalb zählt der Anteil der Übungen, nicht der Sätze."""
    rpes = [_num(s.get("rpe")) for s in work_sets if _num(s.get("rpe")) is not None]
    if not rpes:
        return {"lost": 0.3, "missing": [{"key": "rpe",
                "text": "Anstrengung (RPE) nicht notiert – ein Tipp nach dem letzten Satz reicht"}],
                "good": []}
    schnitt = sum(rpes) / len(rpes)
    zahl = f"{schnitt:.1f}".replace(".", ",")   # deutsche Schreibweise wie in fmtWeight()
    fehlt = []
    lost = 0.0
    if schnitt < RPE_MIN:
        lost = min(1.0, (RPE_MIN - schnitt) / 3)
        fehlt.append({"key": "rpe",
                      "text": f"⌀ RPE {zahl} – da lag noch Reserve auf der Hantel"})
    elif schnitt >= RPE_MAX:
        lost = 0.5
        fehlt.append({"key": "rpe",
                      "text": f"⌀ RPE {zahl} – dauerhaft am Limit, das rächt sich"})
    uebungen = {s["exercise_id"] for s in work_sets}
    mit_rpe = {s["exercise_id"] for s in work_sets if _num(s.get("rpe")) is not None}
    if len(mit_rpe) < len(uebungen) / 2:
        lost = max(lost, 0.2)
        fehlt.append({"key": "rpe",
                      "text": f"Anstrengung nur bei {len(mit_rpe)} von {len(uebungen)} Übungen notiert"})
    good = [] if fehlt else [f"Intensität passt (⌀ RPE {zahl})"]
    return {"lost": lost, "missing": fehlt, "good": good}


def _check_warmup(sets, done_by_ex, names):
    """Vor schweren Grundübungen gehört ein Aufwärmsatz – Isolationsübungen brauchen keinen."""
    compounds = [ex for ex in done_by_ex if names.get(ex, {}).get("kind") == "compound"]
    if not compounds:
        return None
    aufgewaermt = {s["exercise_id"] for s in sets if s.get("warmup")}
    fehlt = [ex for ex in compounds if ex not in aufgewaermt]
    if not fehlt:
        return {"lost": 0.0, "missing": [], "good": ["Grundübungen aufgewärmt"]}
    return {"lost": len(fehlt) / len(compounds),
            "missing": [{"key": "warmup",
                         "text": f"{', '.join(names[ex]['name'] for ex in fehlt)}: "
                                 "kein Aufwärmsatz vor der Grundübung"}], "good": []}


def _minutes(workout):
    start, end = workout.get("started_at"), workout.get("finished_at")
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Eine Seite ohne Zeitzone (z. B. per API nachgetragen, Ende vom Server gesetzt):
    # als UTC lesen statt beim Abziehen abzustuerzen.
    a, b = (x if x.tzinfo else x.replace(tzinfo=timezone.utc) for x in (a, b))
    dauer = (b - a).total_seconds() / 60
    return dauer if dauer > 0 else None


def _check_duration(workout, profile, work_sets):
    """Zu kurz heisst meist abgebrochen, zu lang heisst: die Pausen sind ausgeufert."""
    dauer = _minutes(workout)
    if dauer is None or not work_sets:
        return None
    # Die Dauer wird nicht abgefragt: Massstab ist die Hochrechnung aus der Satzzahl. Sie taugt erst ab einer
    # richtigen Einheit – bei drei geloggten Saetzen wuerde sonst jede normale Trainingsdauer als "zu lang" gelten.
    if len(work_sets) < 6:
        return None
    pro_satz = volume.MINUTES_PER_SET.get((profile or {}).get("goal") or "aufbau", 3.2)
    geplant = volume.WARMUP_MINUTES + pro_satz * len(work_sets)
    if dauer < geplant * 0.6:
        # Unter einer Minute liest sich "nur 0 Minuten" wie ein Fehler – es ist aber schlicht
        # eine im Nachhinein eingetippte Einheit.
        wie_lang = ("keine ganze Minute" if round(dauer) < 1
                    else f"nur {round(dauer)} statt rund {round(geplant)} Minuten")
        return {"lost": 0.5, "missing": [{"key": "dauer", "text": wie_lang}], "good": []}
    if dauer > geplant * 1.8:
        return {"lost": 0.5, "missing": [{"key": "dauer",
                "text": f"{round(dauer)} Minuten – deutlich länger als die geplanten "
                        f"rund {round(geplant)}, die Pausen sind ausgeufert"}], "good": []}
    return {"lost": 0.0, "missing": [], "good": [f"{round(dauer)} Minuten – gute Länge"]}


def label_for(score):
    for grenze, wort in LABELS:
        if score >= grenze:
            return wort
    return LABELS[-1][1]


def evaluate(profile_id, workout):
    """Urteil über eine gespeicherte Einheit. Kriterien, die mangels Vorgaben oder Vorgeschichte
    nicht greifen (freies Training, Übung zum ersten Mal), fallen heraus statt zu bestrafen –
    der Score wird auf die tatsächlich anwendbaren Gewichte normiert."""
    sets = workout.get("sets") or []
    work = _work_sets(sets)
    if not work:
        return {"score": 0, "label": "mager", "good": [],
                "missing": [{"key": "leer", "text": "kein einziger Arbeitssatz"}]}

    done_by_ex = _by_exercise(work)
    names = _kinds({s["exercise_id"] for s in sets})
    items = _plan_items(workout.get("split_day_id"))
    profile = db.get_row("profiles", profile_id) or {}

    ergebnisse = {
        "plan": _check_plan(items, done_by_ex) if items else None,
        "reps": _check_reps(items, done_by_ex) if items else None,
        "progression": _check_progression(profile_id, workout, done_by_ex, names),
        "intensitaet": _check_intensity(work),
        "aufwaermen": _check_warmup(sets, done_by_ex, names),
        "dauer": _check_duration(workout, profile, work),
    }
    moeglich = max(sum(WEIGHTS[k] for k, v in ergebnisse.items() if v), BASIS)
    verloren = sum(WEIGHTS[k] * v["lost"] for k, v in ergebnisse.items() if v)
    score = round(100 * (moeglich - verloren) / moeglich)
    score = max(0, min(100, score))

    # Das Schwerste zuerst – wer nur die erste Zeile liest, liest die wichtigste.
    missing = [m for k in sorted(ergebnisse, key=lambda k: -WEIGHTS[k])
               if ergebnisse[k] for m in ergebnisse[k]["missing"]]
    good = [g for k in sorted(ergebnisse, key=lambda k: -WEIGHTS[k])
            if ergebnisse[k] for g in ergebnisse[k]["good"]]
    return {"score": score, "label": label_for(score), "good": good, "missing": missing}


def for_workout(profile_id, workout):
    """Fürs Anhängen an eine API-Antwort: gibt die Einheit mit `feedback` zurück."""
    if not workout:
        return workout
    workout["feedback"] = evaluate(profile_id, workout)
    return workout
