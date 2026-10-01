"""Progression: Double Progression mit Ziel-RIR, e1RM-Trend, Plateau-Erkennung und Deload.

Innerhalb der Wiederholungsspanne arbeiten; erst wenn in allen Saetzen das obere Ende bei
passender Anstrengung erreicht ist, kommt Gewicht drauf (Dynamic Double Progression).
Jede Empfehlung hat einen erklaerenden Satz. Reine Rechenlogik (sessions rein, Empfehlung raus),
nur `sessions()` und `for_exercise()` greifen auf die Datenbank zu.
"""
from .. import db

PLATEAU_SESSIONS = 3
DELOAD_FACTOR = 0.9
DROP_FACTOR = 0.93
BODYWEIGHT = "bodyweight"


def epley(weight, reps):
    if not weight or not reps or reps > 12:
        return None
    return weight * (1 + reps / 30)


def sessions(profile_id, exercise_id, limit=6):
    """Letzte Einheiten dieser Uebung, neueste zuerst: [{date, sets: [{reps, weight, rpe}]}]."""
    rows = db.rows(
        "SELECT w.id AS wid, COALESCE(w.started_at, w.finished_at) AS at, s.reps, s.weight, s.rpe"
        " FROM sets s JOIN workouts w ON w.id = s.workout_id"
        " WHERE w.profile_id=? AND s.exercise_id=? AND s.warmup=0"
        " ORDER BY at DESC, w.id DESC, s.set_no, s.id", (profile_id, exercise_id))
    out, index = [], {}
    for r in rows:
        if r["wid"] not in index:
            if len(out) >= limit:
                break
            index[r["wid"]] = {"date": (r["at"] or "")[:10], "sets": []}
            out.append(index[r["wid"]])
        index[r["wid"]]["sets"].append({"reps": r["reps"] or 0, "weight": r["weight"], "rpe": r["rpe"]})
    return out


def step_for(equipment, pattern, top):
    """Kleinster sinnvoller Gewichtssprung je Geraet."""
    compound_leg = pattern in ("squat", "hinge", "lunge")
    if equipment == "barbell":
        return 5.0 if compound_leg else 2.5
    if equipment == "machine":
        return 5.0 if pattern in ("squat", "h_push", "h_pull", "v_pull", "v_push") else 2.5
    if equipment == "dumbbell":
        return 1.0 if (top or 0) < 15 else 2.0
    if equipment in ("cable", "smith"):
        return 2.5
    return 2.5


def _round_to(value, step):
    return round(round(value / step) * step, 2)


def _top_weight(sets):
    ws = [s["weight"] for s in sets if s["weight"] is not None]
    return max(ws) if ws else None


def best_e1rm(sess):
    vals = [epley(s["weight"], s["reps"]) for s in sess["sets"]]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def e1rm_trend(history):
    """Prozent-Aenderung des geschaetzten Einer-Maximums von der aeltesten zur neuesten Einheit."""
    vals = [v for v in (best_e1rm(h) for h in reversed(history)) if v]
    if len(vals) < 2 or not vals[0]:
        return None
    return round((vals[-1] - vals[0]) / vals[0] * 100, 1)


def is_plateau(history):
    """Drei Einheiten am selben Gewicht ohne mehr Wiederholungen (und nicht am oberen Ende)."""
    recent = history[:PLATEAU_SESSIONS]
    if len(recent) < PLATEAU_SESSIONS:
        return False
    tops = [_top_weight(h["sets"]) for h in recent]
    if None in tops or len(set(tops)) != 1:
        return False
    best = [max(s["reps"] for s in h["sets"]) for h in reversed(recent)]    # alt -> neu
    return best[-1] <= best[0]


def rpe_drift(history):
    """Selbes Gewicht, aber die gemeldete Anstrengung steigt ueber die letzten Einheiten
    (Ermuedung hat Fitness ueberholt) -> Zeichen fuer einen frueheren Deload."""
    recent = [h for h in history[:3]]
    if len(recent) < 3:
        return False
    tops = [_top_weight(h["sets"]) for h in recent]
    avg = []
    for h in recent:
        r = [s["rpe"] for s in h["sets"] if s["rpe"] is not None]
        if not r:
            return False
        avg.append(sum(r) / len(r))
    return len(set(tops)) == 1 and tops[0] is not None and avg[0] - avg[-1] >= 1.0   # neu - alt


def recommend(history, lo, hi, target_sets=None, equipment=None, pattern=None, rir=2, deload=False):
    """Naechster Vorschlag fuer eine Uebung. history: neueste Einheit zuerst."""
    hi = max(hi, lo)
    base = {"range": [lo, hi], "raise_weight": False, "status": "progress", "trend": e1rm_trend(history)}
    if not history:
        return {**base, "reps": lo, "weight": None, "status": "start", "last": [],
                "reason": f"erstes Mal – wähle ein Gewicht, mit dem du ≈ {rir} Wiederholungen übrig hast"}
    last = history[0]["sets"]
    top = _top_weight(last)
    step = step_for(equipment, pattern, top)
    base["last"] = last

    if deload:
        if top:
            return {**base, "reps": hi, "weight": max(step, _round_to(top * DELOAD_FACTOR, step)),
                    "status": "deload", "reason": "Entlastungswoche: ca. 10 % leichter, nichts bis ans Limit"}
        return {**base, "reps": max(lo, hi - 2), "weight": None, "status": "deload",
                "reason": "Entlastungswoche: weniger Wiederholungen, nichts bis ans Limit"}

    if len(history) >= 2 and top:
        prev_top = _top_weight(history[1]["sets"])
        weakest_now = min(s["reps"] for s in last)
        weakest_prev = min(s["reps"] for s in history[1]["sets"])
        if prev_top == top and weakest_now < lo and weakest_prev < lo:
            return {**base, "reps": lo, "weight": max(step, _round_to(top * DROP_FACTOR, step)),
                    "status": "drop", "reason": f"zweimal unter {lo} Wiederholungen – etwas leichter und sauber aufbauen"}

    if top and is_plateau(history):
        return {**base, "reps": lo, "weight": max(step, _round_to(top * DELOAD_FACTOR, step)),
                "status": "plateau",
                "reason": f"{PLATEAU_SESSIONS} Einheiten ohne Fortschritt – 10 % leichter neu aufbauen "
                          "(oder die Übung im nächsten Block wechseln)"}

    at_top = all(s["reps"] >= hi for s in last) and len(last) >= (target_sets or 1)
    rpes = [s["rpe"] for s in last if s["rpe"] is not None]
    avg_rpe = sum(rpes) / len(rpes) if rpes else None
    if at_top:
        if avg_rpe is not None and avg_rpe >= 9.5 and rir >= 2:
            return {**base, "reps": hi, "weight": top, "status": "hold",
                    "reason": f"{hi} Wiederholungen geschafft, aber nah am Muskelversagen (⌀ RPE {avg_rpe:g}) "
                              "– noch einmal gleich, dann steigern"}
        if top:
            easy = avg_rpe is not None and avg_rpe <= 6.5
            raise_by = step * (2 if easy else 1)
            return {**base, "reps": lo, "weight": round(top + raise_by, 2), "raise_weight": True,
                    "reason": f"{hi} Wiederholungen in allen Sätzen"
                              + (" und leicht" if easy else "") + " – Gewicht erhöhen"}
        return {**base, "reps": hi + 1, "weight": None,
                "reason": "Bereich ausgereizt – eine Wiederholung mehr"}
    weakest = min(s["reps"] for s in last)
    if weakest < lo:
        return {**base, "reps": lo, "weight": top, "status": "hold",
                "reason": f"unteres Ende ({lo}) erst noch in allen Sätzen schaffen"}
    best_reps = max(s["reps"] for s in last)
    return {**base, "reps": min(best_reps + 1, hi), "weight": top,
            "reason": "gleiches Gewicht, eine Wiederholung mehr"}


def for_exercise(profile_id, exercise_id, lo, hi, target_sets=None, rir=2, deload=False):
    ex = db.one("SELECT equipment_type, pattern FROM exercises WHERE id=?", (exercise_id,)) or {}
    return recommend(sessions(profile_id, exercise_id), lo, hi, target_sets,
                     ex.get("equipment_type"), ex.get("pattern"), rir, deload)
