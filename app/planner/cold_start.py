"""Kaltstart ohne Fragen: Startgewichte aus Standards, Einstufung aus den ersten Einheiten.

Prior = Vielfaches des Koerpergewichts je Bewegungsmuster fuer Neueinsteiger (Strength-Level-/
Kilgore-Tabellen, grob), nach Geschlecht, Alter und Geraet angepasst. Startlast = 50 % (Anfaenger) bzw.
60 % dieses e1RM – bewusst vorsichtig, die Schaetzung ist ±25 % genau. Die erste Einheit kalibriert
(leicht / passt / zu schwer), nach zwei Einheiten dominieren die echten Daten.
"""
from datetime import date

from .. import db
from . import progression

# e1RM-Vielfache des Koerpergewichts (Mann, Neueinsteiger) mit der typischen Studio-Uebung des Musters
BW_RATIO = {
    "squat": 0.75, "hinge": 0.9, "lunge": 0.3, "leg_iso_front": 0.5, "leg_iso_back": 0.35, "glutes": 0.8,
    "calves": 0.8, "h_push": 0.5, "v_push": 0.35, "chest_iso": 0.2, "h_pull": 0.45, "v_pull": 0.4,
    "rear_delt": 0.1, "lateral": 0.08, "biceps": 0.2, "triceps": 0.25, "core": 0.15,
}
# Geraet relativ zur Langhantel-Uebung: Maschinen tragen mehr, Kabel weniger, Kurzhantel pro Hand viel weniger
EQUIPMENT_FACTOR = {"barbell": 1.0, "smith": 0.9, "machine": 1.1, "cable": 0.9, "dumbbell": 0.4}
DEFAULT_BODYWEIGHT = {"männlich": 78.0, "weiblich": 63.0}
FEMALE_FACTOR = 0.6
UNCERTAINTY = 0.25


def bodyweight_of(profile_id, profile):
    row = db.one("SELECT kg FROM bodyweight WHERE profile_id=? ORDER BY date DESC LIMIT 1", (profile_id,))
    if row:
        return row["kg"]
    return DEFAULT_BODYWEIGHT.get((profile or {}).get("sex"), 70.0)


def prior_e1rm(profile, bodyweight, pattern, equipment):
    ratio = BW_RATIO.get(pattern)
    if not ratio or equipment == "bodyweight":
        return None
    sex = FEMALE_FACTOR if (profile or {}).get("sex") == "weiblich" else 1.0
    age = (date.today().year - int(profile["birth_year"])) if (profile or {}).get("birth_year") else None
    age_factor = 0.85 if age and age >= 50 else 0.95 if age and age >= 40 else 1.0
    return bodyweight * ratio * sex * age_factor * EQUIPMENT_FACTOR.get(equipment, 1.0)


def starting_weight(profile_id, exercise_id):
    """Vorsichtiges Startgewicht fuer eine Uebung ohne Verlauf. -> (kg | None, Begruendung)"""
    ex = db.one("SELECT pattern, equipment_type FROM exercises WHERE id=?", (exercise_id,))
    profile = db.get_row("profiles", profile_id) or {}
    if not ex or ex["equipment_type"] == "bodyweight":
        return None, None
    # Hat die Person eine andere Uebung desselben Musters schon trainiert, ist das die bessere Basis.
    e1rm = _from_same_pattern(profile_id, ex["pattern"], ex["equipment_type"], exercise_id)
    source = "aus deiner ähnlichen Übung"
    if not e1rm:
        e1rm = prior_e1rm(profile, bodyweight_of(profile_id, profile), ex["pattern"], ex["equipment_type"])
        source = "Schätzung aus Körpergewicht"
    if not e1rm:
        return None, None
    novice = (profile.get("experience") or "einsteiger") == "einsteiger"
    raw = e1rm * (0.5 if novice else 0.6)
    step = progression.step_for(ex["equipment_type"], ex["pattern"], raw)
    kg = max(progression.floor_for(ex["equipment_type"], step, ex["pattern"]),
             progression._round_to(raw, step if step <= 2.5 else 2.5))
    return kg, f"{source} – wird nach zwei Einheiten genauer (±25 %)"


def _from_same_pattern(profile_id, pattern, equipment, exercise_id):
    others = db.rows("SELECT id, equipment_type FROM exercises WHERE pattern=? AND id<>? AND archived=0",
                     (pattern, exercise_id))
    estimates = []
    for o in others:
        est = progression.estimate(progression.sessions(profile_id, o["id"], limit=12))
        best = est[0] if est else None
        if best:
            ratio = EQUIPMENT_FACTOR.get(equipment, 1.0) / EQUIPMENT_FACTOR.get(o["equipment_type"], 1.0)
            estimates.append(best * ratio)
    return max(estimates) if estimates else None


def update_estimate(m, sigma, observed, reps, novice=False):
    """Kalman-Schritt fuer den e1RM (siehe progression.kalman_step)."""
    return progression.kalman_step(m, sigma, observed, reps, novice)


MIN_EXERCISES_FOR_LEVEL = 3
MIN_WEEKS_FOR_LEVEL = 3
# Maschinen, Kabel und Multipresse: Gewichte schwanken von Geraet zu Geraet stark (Hebel, Umlenkung) –
# sie muessen die Schwelle deutlicher ueberschreiten als freie Gewichte
GUIDED_MARGIN = 1.3


def infer_experience(profile_id):
    """Einstufung aus den Daten (hoechstens nach oben): -> ('fortgeschritten'|'erfahren'|None, Begruendung).
    Verlangt mindestens drei Grunduebungen ueber der Schwelle und drei Wochen Daten – eine einzelne
    (Maschinen-)Uebung mit grosszuegig geschaetztem Startwert reicht nicht."""
    profile = db.get_row("profiles", profile_id) or {}
    span = db.one("SELECT COUNT(*) AS n, MIN(COALESCE(started_at, finished_at)) AS first,"
                  " MAX(COALESCE(started_at, finished_at)) AS last FROM workouts WHERE profile_id=?", (profile_id,))
    if span["n"] < 10:
        return None, None
    try:
        weeks = (date.fromisoformat(span["last"][:10]) - date.fromisoformat(span["first"][:10])).days / 7
    except (TypeError, ValueError):
        return None, None
    if weeks < MIN_WEEKS_FOR_LEVEL:
        return None, None
    bw = bodyweight_of(profile_id, profile)
    ratios = []
    for ex in db.rows("SELECT id, name, pattern, equipment_type FROM exercises WHERE archived=0"
                      " AND pattern IN ('squat','hinge','h_push','v_pull','h_pull','v_push')"):
        est = progression.estimate(progression.sessions(profile_id, ex["id"], limit=12))
        e1 = est[0] if est else None
        base = prior_e1rm(profile, bw, ex["pattern"], ex["equipment_type"])
        if e1 and base:
            margin = GUIDED_MARGIN if ex["equipment_type"] in ("machine", "cable", "smith") else 1.0
            ratios.append((e1 / base / margin, ex["name"]))
    ratios.sort(reverse=True)
    for level, threshold, text in (("erfahren", 2.0, "weit"), ("fortgeschritten", 1.4, "klar")):
        above = [name for r, name in ratios if r >= threshold]
        if len(above) >= MIN_EXERCISES_FOR_LEVEL:
            return level, f"{', '.join(above[:3])} liegen {text} über dem Einsteiger-Niveau"
    return None, None
