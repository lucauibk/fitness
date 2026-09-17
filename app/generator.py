"""Plan-Assistent: baut aus den Profildaten einen Trainingsplan.

Bewusst regelbasiert und nachvollziehbar – der Plan steht sofort, ohne Netz und ohne
Kosten. Jede Entscheidung wird als kurzer Satz in `basis` festgehalten, damit sichtbar
ist, worauf der Plan beruht. Die KI (ai.refine_plan) darf ihn danach verfeinern.
Gewaehlt wird nach Bewegungsmuster (exercises.pattern), nicht nach Namen.
"""
from datetime import date

from . import db, splits

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

TEMPLATES = splits.TEMPLATES

# Fokus -> Bewegungsmuster, die mehr Saetze und ggf. eine Zusatzuebung bekommen
FOCUS_PATTERNS = {
    "brust": ["h_push", "chest_iso"],
    "ruecken": ["v_pull", "h_pull"],
    "schultern": ["lateral", "rear_delt", "v_push"],
    "arme": ["biceps", "triceps"],
    "beine": ["squat", "leg_iso_front", "leg_iso_back", "lunge"],
    "po": ["glutes", "hinge"],
    "rumpf": ["core"],
}
FOCUS_LABEL = splits.GROUP_LABEL

# Bevorzugte Uebungen je Erfahrung (werden bei der Auswahl nach vorn sortiert)
PREFER = {
    "einsteiger": ["Brustpresse", "Beinpresse", "Latzug", "Rudern Maschine",
                   "Kurzhantel-Schulterdrücken", "Beinbeuger sitzend", "Hip Thrust",
                   "Schrägbankdrücken Kurzhantel", "Rumänisches Kreuzheben"],
    "fortgeschritten": ["Bankdrücken", "Kniebeuge", "Klimmzüge", "Langhantelrudern",
                        "Rumänisches Kreuzheben", "Schulterdrücken"],
    "erfahren": ["Bankdrücken", "Kniebeuge", "Kreuzheben", "Klimmzüge", "Langhantelrudern",
                 "Schulterdrücken", "Bulgarian Split Squat"],
}

# Einschraenkungen: (Stichworte, Bezeichnung, ausgeschlossen, bevorzugt, Satz fuer basis)
LIMITS = [
    (("knie",), "Knie",
     ["Ausfallschritte", "Bulgarian Split Squat", "Kniebeuge", "Beinstrecker"],
     ["Beinpresse", "Hip Thrust", "Beinbeuger sitzend"],
     "keine Ausfallschritte und tiefen Kniebeugen, Beinpresse statt Kniebeuge"),
    (("rücken", "ruecken", "bandscheibe", "lws", "kreuz"), "Rücken",
     ["Kreuzheben", "Langhantelrudern", "Kniebeuge", "Hyperextension"],
     ["Beinpresse", "Rudern Maschine", "Rudern Kabel", "Hip Thrust"],
     "kein Kreuzheben und Langhantelrudern, geführte Varianten statt freier Langhantel"),
    (("schulter",), "Schulter",
     ["Schulterdrücken", "Dips", "Frontheben"],
     ["Kurzhantel-Schulterdrücken", "Seitheben Kabel", "Face Pulls"],
     "kein Langhantel-Schulterdrücken und keine Dips, Kurzhantel- und Kabelvarianten"),
    (("ellbogen", "handgelenk", "tennisarm"), "Ellbogen/Handgelenk",
     ["SZ-Curls", "Enges Bankdrücken", "Trizeps über Kopf"],
     ["Hammercurls", "Trizepsdrücken Kabel"],
     "keine SZ-Curls und kein enges Bankdrücken, neutrale Griffe und Kabel"),
]


def catalog():
    """Alle Uebungen mit Muster, nach Id sortiert (die mitgelieferten zuerst)."""
    return db.rows("SELECT id, name, muscle_group, unit, pattern, kind FROM exercises"
                   " WHERE archived=0 AND pattern IS NOT NULL ORDER BY id")


def _minutes_key(minutes):
    minutes = int(minutes or 60)
    return min(PER_MINUTES, key=lambda m: abs(m - minutes))


def settings_from(profile, overrides=None):
    focus = profile.get("focus")
    s = {"goal": profile.get("goal") or "aufbau",
         "experience": profile.get("experience") or "einsteiger",
         "days": profile.get("days_per_week") or 3,
         "minutes": profile.get("session_minutes") or 60,
         "weekdays": db.parse_weekdays(profile.get("train_weekdays")),
         "focus": db.parse_focus(focus) if isinstance(focus, str) else list(focus or []),
         "limitations": profile.get("limitations") or "",
         "age": (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None}
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    s["weekdays"] = db.parse_weekdays(s["weekdays"])
    if s["weekdays"]:
        s["days"] = len(s["weekdays"])
    return s


def exercise_count(s):
    exp = s["experience"] if s["experience"] in SETS else "einsteiger"
    return PER_MINUTES[_minutes_key(s["minutes"])] + SETS[exp][2]


def split_options(profile, overrides=None):
    """Bewertete Split-Vorschlaege fuer die Profildaten."""
    s = settings_from(profile, overrides)
    return splits.rank(s["days"], s["weekdays"], {
        "goal": s["goal"], "experience": s["experience"], "minutes": s["minutes"],
        "focus": s["focus"], "age": s["age"]}, exercise_count(s))


def limitation_rules(text):
    text = (text or "").lower()
    return [r for r in LIMITS if any(k in text for k in r[0])]


def generate(profile, option_key, overrides=None):
    s = settings_from(profile, overrides)
    opt = splits.find(option_key)
    if not opt:
        raise ValueError("Unbekannte Split-Option.")
    goal = s["goal"] if s["goal"] in REPS else "aufbau"
    exp = s["experience"] if s["experience"] in SETS else "einsteiger"
    (c_lo, c_hi), (i_lo, i_hi) = REPS[goal]
    c_sets, i_sets, _ = SETS[exp]
    count = exercise_count(s)
    age = s.get("age")
    basis = [f"{GOALS[goal]}: Grundübungen {c_lo}–{c_hi}, Isolation {i_lo}–{i_hi} Wiederholungen.",
             f"{EXPERIENCE[exp]}: {c_sets} Sätze je Grundübung, {i_sets} je Isolationsübung.",
             f"{_minutes_key(s['minutes'])} Minuten pro Einheit: {count} Übungen."]

    if age and age >= 50:
        c_sets = max(2, c_sets - 1)
        c_lo = max(c_lo, 6)
        c_hi = max(c_hi, c_lo + 2)
        i_lo, i_hi = max(i_lo, 10), max(i_hi, 15)
        basis.append(f"Mit {age} Jahren: ein Satz weniger bei Grundübungen und mindestens "
                     f"{c_lo} Wiederholungen – schont die Gelenke.")
    elif age and age >= 40:
        i_lo, i_hi = max(i_lo, 10), max(i_hi, 15)
        basis.append(f"Mit {age} Jahren: Isolationsübungen mit mindestens 10 Wiederholungen.")

    rules = limitation_rules(s.get("limitations"))
    excluded = {n for r in rules for n in r[2]}
    preferred = [n for r in rules for n in r[3]] + PREFER.get(exp, [])
    for r in rules:
        basis.append(f"Wegen „{r[1]}“: {r[4]}.")
    if s.get("limitations") and not rules:
        basis.append("Deine Angabe zu Einschränkungen berücksichtigt nur die KI-Verfeinerung – "
                     "bitte die Übungen selbst prüfen.")

    focus = [f for f in (s.get("focus") or []) if f in FOCUS_PATTERNS][:3]
    focus_patterns = {pat for f in focus for pat in FOCUS_PATTERNS[f]}
    if focus:
        basis.append(f"Fokus {', '.join(FOCUS_LABEL[f] for f in focus)}: ein Satz mehr "
                     "und bei Bedarf eine Zusatzübung.")
    if exp == "einsteiger":
        basis.append("Als Einsteiger bekommst du eher geführte Übungen an Maschinen.")

    rank_of = {name: i for i, name in enumerate(preferred)}
    by_pattern = {}
    for ex in catalog():
        if ex["name"] in excluded:
            continue
        by_pattern.setdefault(ex["pattern"], []).append(ex)
    for lst in by_pattern.values():
        lst.sort(key=lambda e: (rank_of.get(e["name"], 99), e["id"]))
    used = {}   # Muster -> wie oft schon vergeben (Abwechslung zwischen A/B-Tagen)

    days = []
    for label, weekdays in splits.layout(opt, s.get("weekdays")):
        template = list(TEMPLATES[label])
        groups = splits.day_groups(label, len(template))
        day_focus = [pat for f in focus for pat in FOCUS_PATTERNS[f]
                     if any(g in groups for g in splits.PATTERN_GROUPS.get(pat, []))]
        slots = template[:count]
        if day_focus:
            # Grunduebungen bleiben, Fokus kommt direkt dahinter, Isolation zuletzt.
            missing = [pat for pat in dict.fromkeys(day_focus) if pat not in slots]
            base = [pat for pat in slots if pat in db.COMPOUND_PATTERNS]
            rest = [pat for pat in slots if pat not in db.COMPOUND_PATTERNS]
            slots = (base + missing[:2] + rest)[:max(count + 1, len(base))]
        chosen, taken = [], set()
        target = len(slots)
        # Faellt ein Muster weg (z. B. alle Varianten wegen einer Einschraenkung),
        # rueckt das naechste Muster der Vorlage nach – der Tag bleibt gleich lang.
        queue = slots + [pat for pat in template if pat not in slots]
        # Zweiter Durchgang: reicht das nicht, bekommt ein Muster eine weitere Uebung
        queue += list(dict.fromkeys(template))
        for pattern in queue:
            if len(chosen) >= target:
                break
            candidates = [e for e in by_pattern.get(pattern, []) if e["id"] not in taken]
            if not candidates:
                continue
            # Die bevorzugte Uebung zuerst; beim naechsten Tag mit demselben Muster die zweite
            ex = candidates[used.get(pattern, 0) % min(len(candidates), 2)]
            used[pattern] = used.get(pattern, 0) + 1
            taken.add(ex["id"])
            compound = ex["kind"] == "compound"
            sets = c_sets if compound else i_sets
            if pattern in focus_patterns:
                sets = min(sets + 1, 5)
            chosen.append({
                "exercise_id": ex["id"], "name": ex["name"], "pattern": pattern,
                "muscle_group": ex["muscle_group"], "unit": ex["unit"],
                "target_sets": sets,
                "target_reps_min": c_lo if compound else i_lo,
                "target_reps_max": c_hi if compound else i_hi,
                "focus": pattern in focus_patterns,
                "_compound": compound,
            })
        chosen.sort(key=lambda e: not e.pop("_compound"))
        days.append({"label": label, "weekdays": list(weekdays), "exercises": chosen})

    info = next((o for o in split_options(profile, overrides) if o["key"] == option_key), {})
    if opt.get("rotate") and s.get("weekdays"):
        basis.insert(0, "Die Tage wechseln automatisch der Reihe nach – an deinen Trainingstagen "
                     + " · ".join(splits.WD[d] for d in s["weekdays"]) + ".")
    today = date.today()
    return {
        "option": option_key,
        "name": f"{GOALS.get(goal, goal)} · {opt['name']} · {MONTHS[today.month - 1]} {today.year}",
        "goal": goal, "experience": exp, "minutes": _minutes_key(s["minutes"]),
        "reason": opt["text"],
        "reasons": info.get("reasons", []),
        "frequency": info.get("frequency", {}),
        "focus": focus,
        "rotate": bool(opt.get("rotate")),
        "weekdays_chosen": s.get("weekdays") or [],
        "basis": info.get("pro", [])[:2] + basis,
        "days": days,
        "notes": [],
    }


def alternatives(pattern, exclude=()):
    exclude = set(exclude or ())
    return [e for e in catalog() if e["pattern"] == pattern and e["id"] not in exclude]
