"""Progression: Double Progression mit Ziel-RIR, e1RM-Trend, Plateau-Erkennung und Deload.

Innerhalb der Wiederholungsspanne arbeiten; erst wenn in allen Saetzen das obere Ende bei
passender Anstrengung erreicht ist, kommt Gewicht drauf (Dynamic Double Progression).
Jede Empfehlung hat einen erklaerenden Satz. Reine Rechenlogik (sessions rein, Empfehlung raus),
nur `sessions()` und `for_exercise()` greifen auf die Datenbank zu.
"""
from datetime import date

from .. import db, stats

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
            local = stats._local_date(r["at"])
            index[r["wid"]] = {"date": local.isoformat() if local else (r["at"] or "")[:10], "sets": []}
            out.append(index[r["wid"]])
        index[r["wid"]]["sets"].append({"reps": r["reps"] or 0, "weight": r["weight"], "rpe": r["rpe"]})
    return out


LAYOFF_DAYS = 14


def _days_since(iso, today=None):
    try:
        return ((today or stats.today()) - date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return None


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


EMPTY_BAR = 20.0
# Grunduebungen an der olympischen Stange; SZ-Curls oder Skull Crusher laufen mit leichteren Stangen
BAR_PATTERNS = {"squat", "hinge", "lunge", "h_push", "v_push", "h_pull", "glutes"}


def floor_for(equipment, step, pattern=None, logged_min=None):
    """Leichtestes sinnvolles Gewicht: bei Langhantel-Grunduebungen die leere Stange – ausser die Person hat
    die Uebung schon leichter geloggt (leichte Stange, nur Scheiben): dann ihr eigenes Minimum."""
    if equipment == "barbell" and pattern in BAR_PATTERNS:
        bar = EMPTY_BAR if logged_min is None else min(EMPTY_BAR, logged_min)
        return max(step, bar)
    return step


def lighter(top, factor, step, equipment=None, pattern=None, logged_min=None):
    """Leichtere Last fuer Entlastung, Neuaufbau und Wiedereinstieg: mindestens einen Schritt unter top
    (4 kg x 0,9 runden sonst wieder auf 4 kg), nie unter der leeren Stange bzw. dem eigenen Minimum."""
    return max(floor_for(equipment, step, pattern, logged_min), min(_round_to(top * factor, step), round(top - step, 2)))


def _at_floor(rec, top):
    """Ist leichter nicht moeglich (schon an der leeren Stange), sagt der Text das statt „10 % leichter“."""
    if rec.get("weight") is not None and top and rec["weight"] >= top:
        rec["reason"] = "leichter geht hier nicht – gleiches Gewicht, dafür nicht bis ans Limit"
    return rec


def _top_weight(sets):
    ws = [s["weight"] for s in sets if s["weight"] is not None]
    return max(ws) if ws else None


def best_e1rm(sess):
    vals = [epley(s["weight"], s["reps"]) for s in sess["sets"]]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


def kalman_step(m, sigma, observed, reps, novice=False):
    """Ein Kalman-Schritt fuer den e1RM: -> (m, sigma). Messrauschen 5 % bei <= 5 Wdh., sonst 8 %,
    Anfaenger x1,5 (sie schaetzen ihre Reserve ungenau)."""
    noise = (0.05 if reps <= 5 else 0.08) * observed * (1.5 if novice else 1.0)
    gain = sigma ** 2 / (sigma ** 2 + noise ** 2)
    return m + gain * (observed - m), ((1 - gain) * sigma ** 2) ** 0.5


START_SPREAD = 0.10       # erste Einheit: ±10 %
DRIFT_PER_WEEK = 0.015    # so viel darf sich die wahre Kraft je Woche unbemerkt veraendern


def _session_e1rm(sess):
    """Bester e1RM einer Einheit; mit gemeldeter Anstrengung zaehlen die Wiederholungen in Reserve mit."""
    best = None
    for s in sess["sets"]:
        if not s["weight"] or not s["reps"] or s["reps"] > 12:
            continue
        reserve = max(0, 10 - s["rpe"]) if s.get("rpe") is not None else 0
        value = s["weight"] * (1 + (s["reps"] + reserve) / 30)
        if best is None or value > best[0]:
            best = (value, s["reps"])
    return best


def estimate(history, novice=False):
    """Kalman-Schaetzung des aktuellen Einer-Maximums aus dem Verlauf (neueste Einheit zuerst).
    Glaettet Ausreisser-Tage; die Unsicherheit waechst mit Pausen. -> (e1RM, sigma) | None"""
    m = sigma = last = None
    for sess in reversed(history):
        obs = _session_e1rm(sess)
        if not obs:
            continue
        try:
            day = date.fromisoformat(sess.get("date") or "")
        except ValueError:
            day = None
        if m is None:
            m, sigma = obs[0], obs[0] * START_SPREAD
        else:
            weeks = max(1.0, ((day - last).days / 7) if day and last else 1.0)
            sigma = (sigma ** 2 + (DRIFT_PER_WEEK * m) ** 2 * weeks) ** 0.5
            m, sigma = kalman_step(m, sigma, obs[0], obs[1], novice)
        last = day or last
    return (m, sigma) if m else None


def e1rm_trend(history):
    """Prozent-Aenderung des geschaetzten Einer-Maximums von der aeltesten zur neuesten Einheit."""
    vals = [v for v in (best_e1rm(h) for h in reversed(history)) if v]
    if len(vals) < 2 or not vals[0]:
        return None
    return round((vals[-1] - vals[0]) / vals[0] * 100, 1)


def is_plateau(history, sessions=PLATEAU_SESSIONS):
    """Drei Einheiten (Anfaenger: zwei) am selben Gewicht ohne mehr Wiederholungen."""
    recent = history[:sessions]
    if len(recent) < sessions:
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


def recommend(history, lo, hi, target_sets=None, equipment=None, pattern=None, rir=2, deload=False,
              experience=None, start_weight=None, today=None, last_workout=None):
    """Naechster Vorschlag fuer eine Uebung. history: neueste Einheit zuerst.
    Anfaenger: reine Wiederholungs-Regeln (lineare Steigerung), keine RPE-Logik – sie schaetzen ihre
    Reserve um 4-5 Wiederholungen daneben; Plateau schon nach zwei Stillstaenden."""
    novice = experience == "einsteiger"
    hi = max(hi, lo)
    base = {"range": [lo, hi], "raise_weight": False, "status": "progress", "trend": e1rm_trend(history)}
    if not history:
        if start_weight:
            return {**base, "reps": lo, "weight": start_weight[0], "status": "start", "last": [], "estimate": True,
                    "reason": f"erstes Mal – Startgewicht {start_weight[1]}; mit ≈ {rir} Wiederholungen übrig?"}
        return {**base, "reps": lo, "weight": None, "status": "start", "last": [],
                "reason": f"erstes Mal – wähle ein Gewicht, mit dem du ≈ {rir} Wiederholungen übrig hast"}
    last = history[0]["sets"]
    top = _top_weight(last)
    step = step_for(equipment, pattern, top)
    base["last"] = last
    logged = [s["weight"] for h in history for s in h["sets"] if s["weight"]]
    low = min(logged) if logged else None

    def down(factor):
        return lighter(top, factor, step, equipment, pattern, low)

    gap = _days_since(last_workout or history[0].get("date"), today)
    if not deload and top and gap is not None and gap > LAYOFF_DAYS:
        factor = 0.87 if gap > 28 else 0.93
        return _at_floor({**base, "reps": lo, "weight": down(factor), "status": "return",
                          "reason": f"{gap} Tage Pause – vorsichtig wieder einsteigen ({round(factor * 100)} % der letzten Last)"},
                         top)

    if deload:
        if top:
            return _at_floor({**base, "reps": hi, "weight": down(DELOAD_FACTOR),
                              "status": "deload", "reason": "Entlastungswoche: ca. 10 % leichter, nichts bis ans Limit"}, top)
        return {**base, "reps": max(lo, hi - 2), "weight": None, "status": "deload",
                "reason": "Entlastungswoche: weniger Wiederholungen, nichts bis ans Limit"}

    if len(history) >= 2 and top:
        prev_top = _top_weight(history[1]["sets"])
        weakest_now = min(s["reps"] for s in last)
        weakest_prev = min(s["reps"] for s in history[1]["sets"])
        if prev_top == top and weakest_now < lo and weakest_prev < lo:
            return _at_floor({**base, "reps": lo, "weight": down(DROP_FACTOR), "status": "drop",
                              "reason": f"zweimal unter {lo} Wiederholungen – etwas leichter und sauber aufbauen"}, top)

    # Oberes Ende in allen Saetzen geschafft geht vor der Plateau-Pruefung: wer nach einem „noch einmal gleich“
    # wieder oben ankommt, steckt nicht fest, sondern ist bereit fuer mehr Gewicht.
    at_top = all(s["reps"] >= hi for s in last) and len(last) >= (target_sets or 1)
    rpes = [] if novice else [s["rpe"] for s in last if s["rpe"] is not None]
    avg_rpe = sum(rpes) / len(rpes) if rpes else None
    held_before = len(history) >= 2 and _top_weight(history[1]["sets"]) == top \
        and all(s["reps"] >= hi for s in history[1]["sets"])

    if top and not at_top and is_plateau(history, 2 if novice else PLATEAU_SESSIONS):
        return _at_floor({**base, "reps": lo, "weight": down(DELOAD_FACTOR), "status": "plateau",
                          "reason": f"{2 if novice else PLATEAU_SESSIONS} Einheiten ohne Fortschritt – 10 % leichter neu "
                                    "aufbauen (oder die Übung im nächsten Block wechseln)"}, top)

    if at_top:
        if avg_rpe is not None and avg_rpe >= 9.5 and rir >= 2 and not held_before:
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
    experience = (db.one("SELECT experience FROM profiles WHERE id=?", (profile_id,)) or {}).get("experience")
    history = sessions(profile_id, exercise_id)
    start = None
    if not history:
        from . import cold_start
        kg, why = cold_start.starting_weight(profile_id, exercise_id)
        start = (kg, why) if kg else None
    last = db.one("SELECT COALESCE(started_at, finished_at) AS at FROM workouts WHERE profile_id=?"
                  " ORDER BY COALESCE(started_at, finished_at) DESC LIMIT 1", (profile_id,))
    last_day = stats._local_date((last or {}).get("at"))
    return recommend(history, lo, hi, target_sets, ex.get("equipment_type"), ex.get("pattern"), rir,
                     deload, experience, start, last_workout=last_day.isoformat() if last_day else None)
