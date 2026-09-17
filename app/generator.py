"""Plan-Assistent: baut aus Ziel, Zeit und Erfahrung einen Trainingsplan.

Bewusst regelbasiert und nachvollziehbar – der Plan steht sofort, ohne Netz und ohne
Kosten. Die KI (siehe ai.refine_plan) darf ihn danach nur noch verfeinern.
Gewaehlt wird nach Bewegungsmuster (exercises.pattern), nicht nach Namen.
"""
from datetime import date

from . import db

GOALS = {"aufbau": "Muskelaufbau", "kraft": "Kraft", "abnehmen": "Abnehmen",
         "fitness": "Fitness"}
EXPERIENCE = {"einsteiger": "Einsteiger", "fortgeschritten": "Fortgeschritten",
              "erfahren": "Erfahren"}
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

# Wiederholungs-Bereich je Ziel: (Grunduebung, Isolation)
REPS = {
    "kraft": ((4, 6), (8, 12)),
    "aufbau": ((6, 10), (10, 15)),
    "abnehmen": ((10, 15), (12, 20)),
    "fitness": ((10, 15), (12, 20)),
}
# Saetze je Erfahrung: (Grunduebung, Isolation, zusaetzliche Uebungen)
SETS = {"einsteiger": (3, 2, 0), "fortgeschritten": (4, 3, 0), "erfahren": (4, 3, 1)}
# Uebungen pro Einheit nach Dauer (inkl. Aufwaermen und Pausen)
PER_MINUTES = {45: 4, 60: 5, 75: 6, 90: 7}

# Tages-Vorlagen: Muster in Prioritaet – gekuerzt wird von hinten
TEMPLATES = {
    "Ganzkörper A": ["squat", "h_push", "v_pull", "hinge", "biceps", "core", "lateral", "triceps"],
    "Ganzkörper B": ["hinge", "v_push", "h_pull", "lunge", "triceps", "core", "chest_iso", "calves"],
    "Ganzkörper C": ["squat", "h_push", "h_pull", "leg_iso_back", "biceps", "core", "lateral", "triceps"],
    "Push": ["h_push", "v_push", "h_push", "lateral", "triceps", "chest_iso", "triceps", "lateral"],
    "Pull": ["v_pull", "h_pull", "rear_delt", "biceps", "h_pull", "core", "biceps", "v_pull"],
    "Beine": ["squat", "hinge", "leg_iso_back", "core", "calves", "lunge", "leg_iso_front", "glutes"],
    "Oberkörper": ["h_push", "h_pull", "v_push", "v_pull", "biceps", "triceps", "lateral", "rear_delt"],
    "Unterkörper": ["squat", "hinge", "lunge", "core", "calves", "leg_iso_back", "leg_iso_front", "glutes"],
}

# Split-Optionen je Anzahl Trainingstage: (key, Name, Begruendung, [(Tag, [Wochentage])])
OPTIONS = {
    2: [("gk2", "Ganzkörper A/B", "Zwei Einheiten, jede trainiert den ganzen Körper – "
         "jede Muskelgruppe kommt zweimal pro Woche dran.",
         [("Ganzkörper A", [0]), ("Ganzkörper B", [3])])],
    3: [("gk3", "Ganzkörper A/B/C", "Jede Muskelgruppe dreimal pro Woche, dafür kürzere "
         "Blöcke – ideal zum Lernen der Grundübungen.",
         [("Ganzkörper A", [0]), ("Ganzkörper B", [2]), ("Ganzkörper C", [4])]),
        ("ppl3", "Push / Pull / Beine", "Drücken, Ziehen und Beine getrennt – mehr Volumen "
         "pro Muskel, aber jeder nur einmal pro Woche.",
         [("Push", [0]), ("Pull", [2]), ("Beine", [4])])],
    4: [("okuk4", "Oberkörper / Unterkörper", "Zwei Tage, je zweimal pro Woche – guter "
         "Mix aus Häufigkeit und Volumen.",
         [("Oberkörper", [0, 3]), ("Unterkörper", [1, 4])]),
        ("gk4", "Ganzkörper ×4", "Vier kürzere Ganzkörper-Einheiten, gut wenn einzelne "
         "Tage mal ausfallen.",
         [("Ganzkörper A", [0, 3]), ("Ganzkörper B", [1, 4])])],
    5: [("pplokuk5", "Push / Pull / Beine + Ober-/Unterkörper", "Drei getrennte Tage und "
         "zwei kombinierte – jede Muskelgruppe etwa zweimal pro Woche.",
         [("Push", [0]), ("Pull", [1]), ("Beine", [2]), ("Oberkörper", [3]),
          ("Unterkörper", [4])]),
        ("okukgk5", "Ober-/Unterkörper + Ganzkörper", "Vier Tage Ober-/Unterkörper, dazu ein "
         "lockerer Ganzkörpertag.",
         [("Oberkörper", [0, 3]), ("Unterkörper", [1, 4]), ("Ganzkörper A", [2])])],
    6: [("ppl6", "Push / Pull / Beine ×2", "Jeder Teil zweimal pro Woche – hohes Volumen, "
         "braucht gute Erholung.",
         [("Push", [0, 3]), ("Pull", [1, 4]), ("Beine", [2, 5])]),
        ("okuk6", "Oberkörper / Unterkörper ×3", "Nur zwei Tagestypen, dreimal pro Woche – "
         "einfach zu merken.",
         [("Oberkörper", [0, 2, 4]), ("Unterkörper", [1, 3, 5])])],
}


def _recommended(days, experience):
    if days == 3:
        return "gk3" if experience == "einsteiger" else "ppl3"
    if days == 6:
        return "okuk6" if experience == "einsteiger" else "ppl6"
    return OPTIONS[days][0][0]


def split_options(days, experience):
    days = min(max(int(days or 3), 2), 6)
    rec = _recommended(days, experience)
    out = []
    for key, name, reason, layout in OPTIONS[days]:
        out.append({"key": key, "name": name, "reason": reason, "recommended": key == rec,
                    "days": [{"label": label, "weekdays": wd} for label, wd in layout]})
    out.sort(key=lambda o: not o["recommended"])
    return out


def catalog():
    """Alle Uebungen mit Muster, nach Id sortiert (die mitgelieferten zuerst)."""
    return db.rows("SELECT id, name, muscle_group, unit, pattern, kind FROM exercises"
                   " WHERE archived=0 AND pattern IS NOT NULL ORDER BY id")


def _minutes_key(minutes):
    minutes = int(minutes or 60)
    return min(PER_MINUTES, key=lambda m: abs(m - minutes))


def _find_option(option_key):
    for layouts in OPTIONS.values():
        for key, name, reason, layout in layouts:
            if key == option_key:
                return name, reason, layout
    return None


def settings_from(profile, overrides=None):
    s = {"goal": profile.get("goal") or "aufbau",
         "experience": profile.get("experience") or "einsteiger",
         "days": profile.get("days_per_week") or 3,
         "minutes": profile.get("session_minutes") or 60}
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    return s


def generate(profile, option_key, overrides=None):
    s = settings_from(profile, overrides)
    found = _find_option(option_key)
    if not found:
        raise ValueError("Unbekannte Split-Option.")
    name, reason, layout = found
    goal = s["goal"] if s["goal"] in REPS else "aufbau"
    exp = s["experience"] if s["experience"] in SETS else "einsteiger"
    (c_lo, c_hi), (i_lo, i_hi) = REPS[goal]
    c_sets, i_sets, extra = SETS[exp]
    count = PER_MINUTES[_minutes_key(s["minutes"])] + extra

    by_pattern = {}
    for ex in catalog():
        by_pattern.setdefault(ex["pattern"], []).append(ex)
    used = {}   # Muster -> wie oft schon vergeben (fuer Abwechslung zwischen A/B-Tagen)

    days = []
    for label, weekdays in layout:
        chosen, taken = [], set()
        for pattern in TEMPLATES[label]:
            if len(chosen) >= count:
                break
            candidates = [e for e in by_pattern.get(pattern, []) if e["id"] not in taken]
            if not candidates:
                continue
            ex = candidates[used.get(pattern, 0) % len(candidates)]
            used[pattern] = used.get(pattern, 0) + 1
            taken.add(ex["id"])
            compound = ex["kind"] == "compound"
            chosen.append({
                "exercise_id": ex["id"], "name": ex["name"], "pattern": pattern,
                "muscle_group": ex["muscle_group"], "unit": ex["unit"],
                "target_sets": c_sets if compound else i_sets,
                "target_reps_min": c_lo if compound else i_lo,
                "target_reps_max": c_hi if compound else i_hi,
            })
        days.append({"label": label, "weekdays": list(weekdays), "exercises": chosen})

    today = date.today()
    return {
        "option": option_key,
        "name": f"{GOALS.get(goal, goal)} · {name} · {MONTHS[today.month - 1]} {today.year}",
        "goal": goal, "experience": exp, "minutes": _minutes_key(s["minutes"]),
        "reason": reason,
        "days": days,
        "notes": [],
    }


def alternatives(pattern, exclude=()):
    exclude = set(exclude or ())
    return [e for e in catalog() if e["pattern"] == pattern and e["id"] not in exclude]
