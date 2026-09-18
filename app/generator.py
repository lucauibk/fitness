"""Plan-Assistent: baut aus den Profildaten einen Trainingsplan.

Bewusst regelbasiert und nachvollziehbar – der Plan steht sofort, ohne Netz und ohne
Kosten. Jede Entscheidung wird als kurzer Satz in `basis` festgehalten, damit sichtbar
ist, worauf der Plan beruht. Die KI (ai.refine_plan) darf ihn danach verfeinern.
Gewaehlt wird nach Bewegungsmuster (exercises.pattern), nicht nach Namen.
"""
import math
from datetime import date

from . import db, splits, volume

GOALS = {"aufbau": "Muskelaufbau", "kraft": "Kraft", "abnehmen": "Abnehmen",
         "fitness": "Fitness"}
EXPERIENCE = {"einsteiger": "Einsteiger", "fortgeschritten": "Fortgeschritten",
              "erfahren": "Erfahren"}
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

GOAL_ORDER = ("kraft", "aufbau", "abnehmen", "fitness")

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


def settings_from(profile, overrides=None):
    focus = profile.get("focus")
    s = {"goal": profile.get("goal") or "aufbau",
         "experience": profile.get("experience") or "einsteiger",
         "days": profile.get("days_per_week") or 3,
         "minutes": profile.get("session_minutes"),
         "weekdays": db.parse_weekdays(profile.get("train_weekdays")),
         "focus": db.parse_focus(focus) if isinstance(focus, str) else list(focus or []),
         "limitations": profile.get("limitations") or "",
         "age": (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None}
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    if s.get("minutes") == 0:
        s["minutes"] = None     # 0 = "egal", keine Zeitgrenze
    s["weekdays"] = db.parse_weekdays(s["weekdays"])
    if s["weekdays"]:
        s["days"] = len(s["weekdays"])
    return s


def exercise_count(s):
    """Grober Richtwert fuer die Split-Bewertung: wie viele Uebungen ein Tag umfasst."""
    _, per_session = volume.capacity(max(2, s.get("days") or 3), s.get("minutes"))
    return max(4, min(9, round(per_session / 3)))


def split_options(profile, overrides=None):
    """Bewertete Split-Vorschlaege fuer die Profildaten."""
    s = settings_from(profile, overrides)
    return splits.rank(s["days"], s["weekdays"], {
        "goal": s["goal"], "experience": s["experience"], "minutes": s["minutes"] or 90,
        "focus": s["focus"], "age": s["age"]}, exercise_count(s))


def limitation_rules(text):
    text = (text or "").lower()
    return [r for r in LIMITS if any(k in text for k in r[0])]


MAX_SETS_PER_EXERCISE = 5
MIN_SETS_PER_EXERCISE = 2
MIN_EXERCISES_PER_DAY = 3


def _slots(plan):
    """Moegliche Uebungsplaetze je Tag: Muster aus der Vorlage, hoechstens drei je
    Zielgruppe und Tag."""
    out = []
    for label, _ in plan:
        seen, slots = {}, []
        for pattern in TEMPLATES[label]:
            g = volume.PATTERN_TARGET.get(pattern)
            if not g or seen.get(g, 0) >= 3:
                continue
            seen[g] = seen.get(g, 0) + 1
            slots.append(pattern)
        out.append(slots)
    return out


def distribute(plan, targets, per_session, focus_groups=()):
    """Wochensaetze auf die Tage verteilen: erst gleichmaessig je Muskelgruppe ueber die
    vorhandenen Plaetze, danach das Zeitbudget je Einheit durchsetzen.
    Ergebnis: je Tag eine Liste [(Muster, Saetze)] – ein Muster darf zweimal vorkommen."""
    slots_per_day = _slots(plan)
    by_group = {}
    for day, slots in enumerate(slots_per_day):
        for nth, pattern in enumerate(slots):
            g = volume.PATTERN_TARGET.get(pattern)
            by_group.setdefault(g, []).append({"day": day, "idx": nth, "pattern": pattern})

    assigned = [dict() for _ in plan]   # day -> {slot-index: (Muster, Saetze)}
    for group, slots in by_group.items():
        target = targets.get(group, 0)
        if target < MIN_SETS_PER_EXERCISE:
            continue
        days_available = len({sl["day"] for sl in slots})
        count = max(1, min(len(slots), math.ceil(target / MAX_SETS_PER_EXERCISE)))
        # lieber ueber mehrere Tage verteilen als 5 Saetze am Stueck
        while count < len(slots) and target / count > 4 and count < days_available:
            count += 1
        picked = sorted(slots, key=lambda sl: (sl["idx"], sl["day"]))[:count]
        base, rest = divmod(target, count)
        for i, slot in enumerate(sorted(picked, key=lambda sl: (sl["day"], sl["idx"]))):
            sets = min(MAX_SETS_PER_EXERCISE, max(MIN_SETS_PER_EXERCISE, base + (1 if i < rest else 0)))
            assigned[slot["day"]][slot["idx"]] = [slot["pattern"], sets]

    for day, slots in enumerate(assigned):
        # 1. Saetze kuerzen (bis mindestens zwei je Uebung), erst ohne Fokus und Isolation
        def order(idx):
            pattern = slots[idx][0]
            return (volume.PATTERN_TARGET.get(pattern) in focus_groups,
                    pattern in db.COMPOUND_PATTERNS, -slots[idx][1])
        while sum(v[1] for v in slots.values()) > per_session and \
                any(v[1] > MIN_SETS_PER_EXERCISE for v in slots.values()):
            idx = next(i for i in sorted(slots, key=order) if slots[i][1] > MIN_SETS_PER_EXERCISE)
            slots[idx][1] -= 1
        # 2. Reicht das nicht, faellt die unwichtigste Uebung weg
        while sum(v[1] for v in slots.values()) > per_session and len(slots) > MIN_EXERCISES_PER_DAY:
            slots.pop(sorted(slots, key=order)[0])
        # 3. Zu kurze Tage mit weiteren Mustern der Vorlage auffuellen; dafuer notfalls
        #    die vorhandenen Uebungen auf die Mindest-Satzzahl herunterziehen
        for idx, pattern in enumerate(slots_per_day[day]):
            if len(slots) >= MIN_EXERCISES_PER_DAY:
                break
            if idx in slots:
                continue
            while sum(v[1] for v in slots.values()) + MIN_SETS_PER_EXERCISE > per_session and \
                    any(v[1] > MIN_SETS_PER_EXERCISE for v in slots.values()):
                biggest = max(slots, key=lambda i: slots[i][1])
                slots[biggest][1] -= 1
            if sum(v[1] for v in slots.values()) + MIN_SETS_PER_EXERCISE <= per_session:
                slots[idx] = [pattern, MIN_SETS_PER_EXERCISE]

    return [[tuple(slot) for _, slot in sorted(day.items())] for day in assigned]


def generate(profile, option_key, overrides=None):
    """Plan aus Wochenvolumen: erst steht fest, wie viele Saetze jede Muskelgruppe pro
    Woche bekommt, danach werden diese Saetze auf die Trainingstage verteilt."""
    s = settings_from(profile, overrides)
    opt = splits.find(option_key)
    if not opt:
        raise ValueError("Unbekannte Split-Option.")
    goal = s["goal"] if s["goal"] in GOAL_ORDER else "aufbau"
    exp = s["experience"] if s["experience"] in EXPERIENCE else "einsteiger"
    focus = [f for f in (s.get("focus") or []) if f in volume.FOCUS_TARGETS][:3]
    age = s.get("age")
    sessions = max(2, s.get("days") or 3)

    weekly, notes, per_session = volume.targets(exp, goal, focus, sessions, s.get("minutes"))
    basis = list(notes)
    basis.append("Pausen: etwa 2,5–3 Minuten nach Grundübungen, 1–1,5 Minuten nach "
                 "Isolationsübungen – längere Pausen bringen mehr Kraft und Muskelaufbau.")
    if s.get("minutes"):
        basis.append(f"{s['minutes']} Minuten pro Einheit: rund {per_session} Arbeitssätze.")
    else:
        basis.append(f"Ohne Zeitgrenze: bis zu {per_session} Arbeitssätze pro Einheit.")

    # Alter: laengere Wiederholungen, etwas weniger Volumen
    if age and age >= 50:
        weekly = {g: max(volume.LANDMARKS[g][0], round(v * 0.85)) for g, v in weekly.items()}
        basis.append(f"Mit {age} Jahren: etwas weniger Volumen und keine ganz schweren "
                     "Wiederholungsbereiche – das schont die Gelenke.")
    elif age and age >= 40:
        basis.append(f"Mit {age} Jahren: Isolationsübungen im höheren Wiederholungsbereich.")

    rules = limitation_rules(s.get("limitations"))
    excluded = {n for r in rules for n in r[2]}
    preferred = [n for r in rules for n in r[3]] + PREFER.get(exp, [])
    for r in rules:
        basis.append(f"Wegen „{r[1]}“: {r[4]}.")
    if s.get("limitations") and not rules:
        basis.append("Deine Angabe zu Einschränkungen berücksichtigt nur die KI-Verfeinerung – "
                     "bitte die Übungen selbst prüfen.")
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

    plan = splits.layout(opt, s.get("weekdays"))
    # Beim Wochenwechsel dauert ein Durchlauf laenger als eine Woche – Volumen umrechnen
    cycle = len(plan) / sessions if opt.get("rotate") else 1.0
    focus_groups = {t for f in focus for t in volume.FOCUS_TARGETS[f]}
    per_day = distribute(plan, {g: round(v * cycle) for g, v in weekly.items()},
                         per_session, focus_groups)

    used = {}
    days = []
    for i, (label, weekdays) in enumerate(plan):
        chosen, taken = [], set()
        for pattern, sets in per_day[i]:
            candidates = [e for e in by_pattern.get(pattern, []) if e["id"] not in taken]
            if not candidates:
                # Ist das Muster durch eine Einschraenkung leer, ein anderes derselben
                # Muskelgruppe nehmen (sonst faellt der Tag zu kurz aus).
                group = volume.PATTERN_TARGET.get(pattern)
                for alt, items in by_pattern.items():
                    if volume.PATTERN_TARGET.get(alt) != group:
                        continue
                    free = [e for e in items if e["id"] not in taken]
                    if free:
                        pattern, candidates = alt, free
                        break
            if not candidates:
                continue
            # Durch alle Kandidaten rotieren statt nur die ersten zwei zu nutzen – sonst
            # bleibt der Rest des Katalogs für dieses Muster ungenutzt.
            ex = candidates[used.get(pattern, 0) % len(candidates)]
            used[pattern] = used.get(pattern, 0) + 1
            taken.add(ex["id"])
            compound = ex["kind"] == "compound"
            lo, hi = volume.reps_for(pattern, goal, compound)
            if age and age >= 50:
                lo, hi = max(lo, 6), max(hi, 10)
            elif age and age >= 40 and not compound:
                lo, hi = max(lo, 10), max(hi, 15)
            chosen.append({
                "exercise_id": ex["id"], "name": ex["name"], "pattern": pattern,
                "muscle_group": ex["muscle_group"], "unit": ex["unit"],
                "target_sets": sets, "target_reps_min": lo, "target_reps_max": hi,
                "rest_seconds": volume.rest_for(pattern, goal, compound),
                "focus": volume.PATTERN_TARGET.get(pattern) in focus_groups,
                "_compound": compound,
            })
        # Letzte Sicherung: Bleibt der Tag zu kurz (z. B. weil Einschraenkungen viele
        # Uebungen sperren), mit passenden Uebungen aus dem Katalog auffuellen.
        day_groups = {volume.PATTERN_TARGET.get(p) for p in TEMPLATES[label]}
        while len(chosen) < MIN_EXERCISES_PER_DAY:
            extra = next((e for pat, items in by_pattern.items() if volume.PATTERN_TARGET.get(pat) in day_groups
                          for e in items if e["id"] not in taken), None)
            if not extra:
                break
            taken.add(extra["id"])
            compound = extra["kind"] == "compound"
            lo, hi = volume.reps_for(extra["pattern"], goal, compound)
            chosen.append({
                "exercise_id": extra["id"], "name": extra["name"], "pattern": extra["pattern"],
                "muscle_group": extra["muscle_group"], "unit": extra["unit"],
                "target_sets": MIN_SETS_PER_EXERCISE, "target_reps_min": lo, "target_reps_max": hi,
                "rest_seconds": volume.rest_for(extra["pattern"], goal, compound),
                "focus": volume.PATTERN_TARGET.get(extra["pattern"]) in focus_groups,
                "_compound": compound,
            })
        chosen.sort(key=lambda e: not e.pop("_compound"))
        days.append({"label": label, "weekdays": list(weekdays), "exercises": chosen})

    # Was tatsaechlich im Plan steht (zum Vergleich mit dem Ziel)
    planned = {g: 0 for g in volume.LANDMARKS}
    for d in days:
        for e in d["exercises"]:
            g = volume.PATTERN_TARGET.get(e["pattern"])
            if g:
                planned[g] += e["target_sets"]
    planned = {g: round(v / cycle) for g, v in planned.items()}
    short = [(volume.LABEL[g], planned.get(g, 0), target) for g, target in weekly.items()
             if target >= 4 and planned.get(g, 0) < target - 2]
    if short:
        detail = ", ".join(f"{label} {have} statt {want}" for label, have, want in short[:4])
        basis.append(f"Nicht ganz erreicht: {detail} Sätze pro Woche. "
                     + ("Mit mehr Zeit pro Einheit oder einem Tag mehr ginge es auf."
                        if s.get("minutes") else
                        "In diesem Split kommen diese Muskeln zu selten dran – ein anderer Split "
                        "oder ein Tag mehr würde es lösen."))

    info = next((o for o in split_options(profile, overrides) if o["key"] == option_key), {})
    if opt.get("rotate") and s.get("weekdays"):
        basis.insert(0, "Die Tage wechseln automatisch der Reihe nach – an deinen Trainingstagen "
                     + " · ".join(splits.WD[d] for d in s["weekdays"]) + ".")
    today = date.today()
    return {
        "option": option_key,
        "name": f"{GOALS.get(goal, goal)} · {opt['name']} · {MONTHS[today.month - 1]} {today.year}",
        "goal": goal, "experience": exp, "minutes": s.get("minutes"),
        "reason": opt["text"],
        "reasons": info.get("reasons", []),
        "frequency": info.get("frequency", {}),
        "focus": focus,
        "rotate": bool(opt.get("rotate")),
        "weekdays_chosen": s.get("weekdays") or [],
        "volume": {"target": weekly, "planned": planned, "labels": volume.LABEL},
        "basis": info.get("pro", [])[:2] + basis,
        "days": days,
        "notes": [],
    }


def alternatives(pattern, exclude=()):
    exclude = set(exclude or ())
    return [e for e in catalog() if e["pattern"] == pattern and e["id"] not in exclude]
