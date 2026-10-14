"""Plan-Generator: Split -> Wochensaetze je Muskel (ueber den Block) -> Tage -> Uebungen.

Regelbasiert und nachvollziehbar: jede Entscheidung landet als Satz in `basis`.
Gewaehlt wird nach Bewegungsmuster, Verletzungen, Geraet, Schwierigkeit und SFR.
"""
import itertools
import math

from .. import db, splits, stats
from . import volume_plan as vp
from .catalog_data import ACCESSORIES, STAPLES
from .muscles import BIG, COMPOUND, GROUPS, INJURY_AREAS, LABEL, PATTERN_GROUP, group_of
from .profile_model import EXPERIENCE, GOALS, settings_from

MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
FOCUS_GROUPS = {"brust": ["brust"], "ruecken": ["ruecken"], "schultern": ["schultern"],
                "arme": ["bizeps", "trizeps"], "beine": ["quads", "beinbeuger", "waden"],
                "po": ["po", "beinbeuger"], "rumpf": ["rumpf"]}
PULL = {"h_pull", "v_pull", "rear_delt"}
BIG_GROUPS = {"brust", "ruecken", "quads", "beinbeuger", "po", "schultern"}
MAX_SETS = 5
MIN_SETS = 2
MIN_EXERCISES = 3
ALLOWED_DIFFICULTY = {"einsteiger": 1, "fortgeschritten": 2, "erfahren": 3}
TEMPLATES = splits.TEMPLATES
# Standard-Split nach Trainingstagen (Frequenz ≥ 2×/Woche je Muskel; Schoenfeld 2016/2019, Pelland 2025)
DEFAULT_SPLIT = {2: "gk2", 3: "gk3", 4: "okuk4", 5: "pplokuk5", 6: "ppl6"}
MAX_SESSION_SETS_PER_MUSCLE = 8


def recommended_days(experience, goal, available_count=7):
    """Empfohlene Einheiten pro Woche (ACSM 2009, Schoenfeld 2016/2019, Pelland 2025): Einsteiger 3,
    Fortgeschrittene Aufbau 4, Erfahrene 4; Fitness 3 hoechstens. Nie mehr als die freien Tage,
    Einsteiger hoechstens 5."""
    base = {"einsteiger": 3, "fortgeschritten": 4 if goal == "aufbau" else 3, "erfahren": 4}.get(experience, 3)
    if goal == "fitness":
        base = min(base, 3)
    cap = 5 if experience in (None, "einsteiger") else 6
    return max(2, min(base, cap, max(2, available_count)))


def pick_days(available, n):
    """Aus den freien Wochentagen (0 = Montag; leer = alle) die n besten waehlen: moeglichst gleichmaessige
    Abstaende (kleinster Abstand so gross wie moeglich, ueber die Wochengrenze gerechnet), wenig direkt
    aufeinanderfolgende Tage, keine Luecken ueber 3 Tage. Die uebrigen freien Tage bleiben Reserve."""
    avail = sorted({d for d in (available or []) if 0 <= d <= 6}) or list(range(7))
    n = max(1, min(n, len(avail)))
    best, best_key = None, None
    for combo in itertools.combinations(avail, n):
        gaps = [((combo[(i + 1) % n] - combo[i]) % 7) or 7 for i in range(n)]
        run = best_run = 0
        for g in gaps + gaps:                       # laengste Kette direkt aufeinanderfolgender Tage (zyklisch)
            run = run + 1 if g == 1 else 0
            best_run = max(best_run, run)
        key = (-min(gaps), min(best_run, n - 1), sum(1 for g in gaps if g == 1), sum(1 for g in gaps if g > 3),
               max(gaps) - min(gaps), combo)
        if best_key is None or key < best_key:
            best, best_key = combo, key
    return list(best)


def catalog():
    return db.rows("SELECT id, name, muscle_group, unit, pattern, kind, equipment_type, difficulty,"
                   " sfr, contraindications, lengthened FROM exercises WHERE archived=0 AND pattern IS NOT NULL ORDER BY id")


def usable(exercise, injuries):
    bad = set((exercise.get("contraindications") or "").split(","))
    return not (bad & set(injuries))


def exercise_count(s):
    _, per_session = vp.capacity(max(2, s.get("days") or 3), s.get("minutes"), s["goal"])
    return max(4, min(9, round(per_session / 3)))


def split_options(profile, overrides=None):
    s = settings_from(profile, overrides)
    return splits.rank(s["days"], s["weekdays"], {
        "goal": s["goal"], "experience": s["experience"], "minutes": 60,
        "focus": s["focus"], "age": s["age"]}, exercise_count(s))


def score(ex, s):
    """Hoeher = besser. SFR zaehlt am meisten, zu schwere Uebungen werden abgewertet,
    Einsteiger bekommen gefuehrte Geraete, bei Kraft zaehlen freie Grunduebungen."""
    sc = 2.0 * (ex["sfr"] or 3)
    if ex["name"] in STAPLES:
        sc += 3
    if ex.get("lengthened"):
        sc += 1.5            # Last in der gedehnten Position: mehr Reiz bei gleichem Aufwand
    if ex["name"] in ACCESSORIES and ex["pattern"] in COMPOUND:
        sc -= 3
    over = max(0, (ex["difficulty"] or 2) - ALLOWED_DIFFICULTY[s["experience"]])
    sc -= 4 * over
    if s["experience"] == "einsteiger" and ex["equipment_type"] in ("machine", "cable", "smith"):
        sc += 2
    if s["goal"] == "kraft" and ex["equipment_type"] == "barbell" and ex["pattern"] in COMPOUND:
        sc += 3
    if s["age"] and s["age"] >= 50 and ex["equipment_type"] == "barbell" and ex["pattern"] not in ("hinge",):
        sc -= 1
    return sc


REGION = {"brust": "oben", "ruecken": "oben", "schultern": "oben", "bizeps": "oben", "trizeps": "oben",
          "quads": "unten", "beinbeuger": "unten", "po": "unten", "waden": "unten"}


def _slots(plan, targets=None):
    """Moegliche Uebungsplaetze je Tag: Muster der Vorlage, hoechstens drei je Muskelgruppe. Reichen die Plaetze
    eines Muskels nicht fuer sein Ziel (z. B. Po im Ganzkoerperplan, Bizeps-Fokus), kommen Plaetze an Tagen
    derselben Koerperregion dazu – am Tag mit den wenigsten Plaetzen."""
    out = []
    for label, _ in plan:
        seen, slots = {}, []
        for pattern in TEMPLATES[label]:
            g = group_of(pattern)
            if not g or seen.get(g, 0) >= 3:
                continue
            seen[g] = seen.get(g, 0) + 1
            slots.append(pattern)
        out.append(slots)
    weight = [max(1, len(wds)) for _, wds in plan]
    for g, target in sorted((targets or {}).items()):
        patterns = GROUP_PATTERNS.get(g, ())
        if target < MIN_SETS or not patterns:
            continue
        fits = [d for d, slots in enumerate(out) if REGION.get(g) is None
                or any(REGION.get(group_of(p)) == REGION[g] for p in slots)]
        for _ in range(3):
            room = sum(MAX_SETS * weight[d] for d, slots in enumerate(out) for p in slots if group_of(p) == g)
            free = [d for d in fits if sum(group_of(p) == g for p in out[d]) < 2]
            if room >= target or not free:
                break
            d = min(free, key=lambda d: (sum(group_of(p) == g for p in out[d]), len(out[d]), d))
            mine = [p for p in out[d] if group_of(p) == g]
            out[d].append(next((p for p in patterns if p not in mine), patterns[0]))
    return out


# Reihenfolge der Muster je Muskelgruppe: so viele verschiedene wie moeglich, wichtigste zuerst
GROUP_PATTERNS = {
    "brust": ("h_push", "chest_iso"), "ruecken": ("v_pull", "h_pull"),
    "schultern": ("v_push", "lateral", "rear_delt"), "quads": ("squat", "leg_iso_front", "lunge"),
    "beinbeuger": ("hinge", "leg_iso_back"), "po": ("glutes",), "waden": ("calves",),
    "bizeps": ("biceps",), "trizeps": ("triceps",), "rumpf": ("core",),
}


def _pick_slots(group, slots, count):
    """Plaetze fuer eine Muskelgruppe. Grosse Muskeln bekommen zuerst zwei verschiedene Tage
    (Frequenz 2x/Woche, Schoenfeld 2016), danach so viele verschiedene Muster wie moeglich."""
    prio = GROUP_PATTERNS.get(group, ())
    rank = lambda sl: prio.index(sl["pattern"]) if sl["pattern"] in prio else 9    # noqa: E731
    picked = []

    def days():
        return {sl["day"] for sl in picked}

    def patterns():
        return {sl["pattern"] for sl in picked}

    if group in BIG_GROUPS and count >= 2 and len({sl["day"] for sl in slots}) >= 2:
        picked.append(min(slots, key=lambda sl: (rank(sl), sl["idx"], sl["day"])))
        others = [sl for sl in slots if sl["day"] != picked[0]["day"]]
        picked.append(min(others, key=lambda sl: (sl["pattern"] in patterns(), rank(sl), sl["idx"], sl["day"])))
    while len(picked) < count:
        rest = [sl for sl in slots if sl not in picked]
        if not rest:
            break
        picked.append(min(rest, key=lambda sl: (sl["pattern"] in patterns(), sl["day"] in days(),
                                                rank(sl), sl["idx"], sl["day"])))
    return picked[:count]


def distribute(plan, targets, per_session, focus_groups=(), cycle=1.0):
    """Spitzenwochen-Saetze auf Tage verteilen (targets je Durchlauf, beim Wechselplan also mal cycle).
    -> je Tag [(Muster, Saetze)]"""
    slots_per_day = _slots(plan, targets)
    weight = [max(1, len(wds)) for _, wds in plan]       # ein Tag mit zwei Wochentagen kommt zweimal pro Woche dran
    by_group = {}
    for day, slots in enumerate(slots_per_day):
        for nth, pattern in enumerate(slots):
            by_group.setdefault(group_of(pattern), []).append({"day": day, "idx": nth, "pattern": pattern})

    assigned = [dict() for _ in plan]
    for group, slots in by_group.items():
        target = targets.get(group, 0)
        if target < MIN_SETS:
            continue
        days_available = len({sl["day"] for sl in slots})
        # Wie oft pro Woche muss der Muskel drankommen, damit die Saetze je Einheit im Rahmen bleiben?
        reachable = sum(weight[d] for d in {sl["day"] for sl in slots})
        need = max(math.ceil(target / MAX_SETS), min(math.ceil(target / 4), reachable),     # lieber verteilen als 5 am Stueck
                   2 if group in BIG_GROUPS and target >= 6 and days_available >= 2 else 1)
        variety = min(len(GROUP_PATTERNS.get(group, ())), 2 if target < 8 else 3, len(slots))
        count = max(1, variety)
        while count < len(slots) and sum(weight[sl["day"]] for sl in _pick_slots(group, slots, count)) < need:
            count += 1
        picked = _pick_slots(group, slots, count)
        appearances = sum(weight[sl["day"]] for sl in picked)
        for slot in sorted(picked, key=lambda sl: (sl["day"], sl["idx"])):
            sets = min(MAX_SETS, max(MIN_SETS, math.ceil(target / appearances)))
            assigned[slot["day"]][slot["idx"]] = [slot["pattern"], sets]

    for day, slots in enumerate(assigned):
        # nie mehr als 8 harte Saetze je Muskel und Einheit (Rest bleibt auf anderen Tagen)
        for g in {group_of(v[0]) for v in slots.values()}:
            mine = sorted((i for i in slots if group_of(slots[i][0]) == g), key=lambda i: -i)
            while sum(slots[i][1] for i in mine) > MAX_SESSION_SETS_PER_MUSCLE:
                idx = next((i for i in mine if slots[i][1] > MIN_SETS), None)
                if idx is None:
                    break
                slots[idx][1] -= 1

        def spare(idx, sets, day=day):
            # bleibt der Muskel ohne diese Saetze noch auf Erhaltung (ueber die ganze Woche)?
            g = group_of(assigned[day][idx][0])
            return _weekly(assigned, weight).get(g, 0) - sets * weight[day] >= GROUPS[g][1] * cycle

        def keeps_frequency(idx, day=day):
            # grosse Muskeln sollen an zwei Tagen dran bleiben (Schoenfeld 2016)
            g = group_of(assigned[day][idx][0])
            others = {d for d, sl in enumerate(assigned) if d != day and any(group_of(p) == g for p, _ in sl.values())}
            on_day = sum(1 for p, _ in assigned[day].values() if group_of(p) == g)
            return g not in BIG or on_day > 1 or sum(weight[d] for d in others) >= 2

        def last_one(idx, day=day):
            # die einzige Uebung eines Muskels in der Woche bleibt (z. B. Rumpf) – notfalls mit weniger Saetzen
            g = group_of(assigned[day][idx][0])
            return sum(group_of(p) == g for sl in assigned for p, _ in sl.values()) <= 1

        def order(idx):
            # zuerst Muskeln ueber Erhaltung, dann Isolationsuebungen, bei Grunduebungen eher Druecken als Ziehen
            pattern = slots[idx][0]
            return (group_of(pattern) in focus_groups, not spare(idx, 1), pattern in COMPOUND, pattern in PULL,
                    -slots[idx][1])
        # Zu lang: erst ganze Uebungen streichen (nie Ziehen – Schulterbalance), deren Muskel auch ohne sie auf Erhaltung bleibt (Rumpf und Po
        # arbeiten bei den Grunduebungen mit) – sonst wuerden alle Uebungen auf 2 Saetze gestutzt
        # Knapp zu lang: erst grosse Uebungen (ueber 3 Saetze) von Muskeln ueber Erhaltung um je einen Satz kuerzen
        while sum(v[1] for v in slots.values()) > per_session:
            big = [i for i in slots if slots[i][1] > 3 and group_of(slots[i][0]) not in focus_groups and spare(i, 1)]
            if not big:
                break
            slots[min(big, key=order)][1] -= 1
        while sum(v[1] for v in slots.values()) > per_session and len(slots) > MIN_EXERCISES:
            cands = [i for i in slots if slots[i][0] not in PULL and group_of(slots[i][0]) not in focus_groups
                     and spare(i, slots[i][1]) and keeps_frequency(i) and not last_one(i)]
            if not cands:
                break
            slots.pop(min(cands, key=lambda i: (GROUPS[group_of(slots[i][0])][1] > 0, slots[i][0] in COMPOUND,
                                                slots[i][1], -i)))
        while sum(v[1] for v in slots.values()) > per_session and any(v[1] > MIN_SETS for v in slots.values()):
            idx = next(i for i in sorted(slots, key=order) if slots[i][1] > MIN_SETS)
            slots[idx][1] -= 1
        while sum(v[1] for v in slots.values()) > per_session and len(slots) > MIN_EXERCISES:
            slots.pop(sorted(slots, key=order)[0])
        for idx, pattern in enumerate(slots_per_day[day]):
            if len(slots) >= MIN_EXERCISES:
                break
            if idx in slots:
                continue
            while sum(v[1] for v in slots.values()) + MIN_SETS > per_session and any(
                    v[1] > MIN_SETS for v in slots.values()):
                slots[max(slots, key=lambda i: slots[i][1])][1] -= 1
            if sum(v[1] for v in slots.values()) + MIN_SETS <= per_session:
                slots[idx] = [pattern, MIN_SETS]
    return [[tuple(slot) for _, slot in sorted(day.items())] for day in assigned]


def _weekly(assigned, weight):
    out = {}
    for day, slots in enumerate(assigned):
        for pattern, sets in slots.values():
            out[group_of(pattern)] = out.get(group_of(pattern), 0) + sets * weight[day]
    return out


def _pick(candidates, used, taken, variation):
    """Beste Uebung; unter gleich guten die am seltensten genutzte, bei neuem Block rotierend."""
    free = [e for e in candidates if e["id"] not in taken]
    if not free:
        return None
    best = max(e["_score"] for e in free)
    top = sorted((e for e in free if e["_score"] >= best - 1.5), key=lambda e: (used.get(e["id"], 0), -e["_score"], e["id"]))
    return top[variation % len(top)] if variation else top[0]


def generate(profile, option_key, overrides=None, variation=0):
    """Plan fuer einen Block. variation: Nummer des Blocks (wechselt Isolationsuebungen)."""
    s = settings_from(profile, overrides)
    opt = splits.find(option_key)
    if not opt:
        raise ValueError("Unbekannte Split-Option.")
    goal, exp, age = s["goal"], s["experience"], s["age"]
    focus = [f for f in (s.get("focus") or []) if f in FOCUS_GROUPS][:3]
    focus_groups = {g for f in focus for g in FOCUS_GROUPS[f]}
    sessions = max(2, s["days"])
    total, per_session = vp.capacity(sessions, s.get("minutes"), goal)

    weeks_t = vp.block_targets(exp, goal, focus_groups, age)
    weeks_t, cut_note = vp.fit_to_capacity(weeks_t, total)
    n_weeks = len(next(iter(weeks_t.values())))
    # Die Satzzahl bleibt im ganzen Block gleich (blocks.sets_for) – geplant wird deshalb auf das Ziel der
    # ersten Blockwoche, nicht auf den frueheren Gipfel. Der Name `peak` heisst hier: staerkste Woche = jede Woche.
    peak = {g: v[0] for g, v in weeks_t.items()}

    basis = [f"Block von {n_weeks} Wochen: die Sätze bleiben jede Woche gleich, automatisch steigen nur "
             f"Gewicht und Wiederholungen. Die letzte Woche ist eine Entlastungswoche (Deload) mit halben Sätzen."]
    if cut_note:
        basis.append(cut_note)
    if focus_groups:
        basis.append("Spezialisierung: " + ", ".join(LABEL[g] for g in sorted(focus_groups))
                     + " bekommen deutlich mehr Sätze, alle anderen laufen auf Erhaltung.")
    basis.append("Pausen: etwa 2,5–3 Minuten nach Grundübungen, 1–1,5 Minuten nach Isolationsübungen.")
    if age and age >= 50:
        basis.append(f"Mit {age} Jahren: etwa 15 % weniger Volumen und keine ganz schweren Wiederholungsbereiche.")
    elif age and age >= 40:
        basis.append(f"Mit {age} Jahren: Isolationsübungen im höheren Wiederholungsbereich.")
    for area in sorted(s["injuries"]):
        basis.append(f"Wegen „{INJURY_AREAS[area]}“ sind belastende Übungen ausgeschlossen.")
    if s["limitations"] and not s["injuries"]:
        basis.append("Deine Angabe zu Einschränkungen konnte keinem Körperbereich zugeordnet werden – "
                     "bitte die Übungen selbst prüfen.")
    if exp == "einsteiger":
        basis.append("Als Einsteiger bekommst du eher geführte Übungen an Maschinen und Kabelzug.")

    by_pattern = {}
    for ex in catalog():
        if not usable(ex, s["injuries"]):
            continue
        ex["_score"] = score(ex, s)
        by_pattern.setdefault(ex["pattern"], []).append(ex)

    plan = splits.layout(opt, s.get("weekdays"))
    cycle = len(plan) / sessions if opt.get("rotate") else 1.0
    per_day = distribute(plan, {g: round(v * cycle) for g, v in peak.items()}, per_session, focus_groups, cycle)

    used, days = {}, []
    for i, (label, weekdays) in enumerate(plan):
        chosen, taken = [], set()
        for pattern, sets in per_day[i]:
            ex = _pick(by_pattern.get(pattern, []), used, taken, variation if pattern not in COMPOUND else 0)
            if not ex:
                grp = group_of(pattern)
                for alt, items in by_pattern.items():
                    if group_of(alt) == grp and alt != pattern:
                        ex = _pick(items, used, taken, 0)
                        if ex:
                            pattern = alt
                            break
            if not ex:
                continue
            taken.add(ex["id"])
            used[ex["id"]] = used.get(ex["id"], 0) + 1
            chosen.append(_entry(ex, pattern, sets, goal, age, focus_groups, weeks_t, peak))
        day_groups = {group_of(p) for p in TEMPLATES[label]}
        while len(chosen) < MIN_EXERCISES:
            extra = next((e for pat, items in by_pattern.items() if group_of(pat) in day_groups
                          for e in items if e["id"] not in taken), None)
            if not extra:
                break
            taken.add(extra["id"])
            chosen.append(_entry(extra, extra["pattern"], MIN_SETS, goal, age, focus_groups, weeks_t, peak))
        chosen.sort(key=lambda e: e["pattern"] not in COMPOUND)
        days.append({"label": label, "weekdays": list(weekdays), "exercises": chosen})

    _fill_gaps(days, by_pattern, peak, per_session, goal, age, focus_groups, weeks_t, used)
    _balance_push_pull(days, by_pattern, per_session, goal, age, focus_groups, weeks_t, peak, used)

    durations = [vp.session_minutes(sum(e["week_sets"][w] for e in d["exercises"]), goal)
                 for d in days for w in range(n_weeks - 1)]
    lo, hi = min(durations), max(durations)
    basis.append(f"Dauer: etwa {lo}–{hi} Minuten pro Einheit inklusive Aufwärmen." if hi > lo
                 else f"Dauer: etwa {hi} Minuten pro Einheit inklusive Aufwärmen.")

    planned_by_week = [vp.weekly_volume(days, lambda e, w=w: (e["pattern"], e["week_sets"][w]), cycle)
                       for w in range(n_weeks)]
    planned_peak = {g: max(wk[g] for wk in planned_by_week[: n_weeks - 1]) for g in GROUPS}
    short = [(LABEL[g], planned_peak[g], t) for g, t in peak.items() if t >= 4 and planned_peak[g] < t - 2]
    if short:
        detail = ", ".join(f"{a} {b} statt {c}" for a, b, c in short[:4])
        basis.append(f"Nicht ganz erreicht: {detail} Sätze pro Woche. In diesem Split kommen diese Muskeln "
                     "zu selten dran – ein anderer Split oder ein Tag mehr würde es lösen.")

    info = next((o for o in split_options(profile, overrides) if o["key"] == option_key), {})
    if opt.get("rotate") and s.get("weekdays"):
        basis.insert(0, "Die Tage wechseln automatisch der Reihe nach – an deinen Trainingstagen "
                     + " · ".join(splits.WD[d] for d in s["weekdays"]) + ".")
    today = stats.today()
    return {
        "option": option_key,
        "name": f"{GOALS[goal]} · {opt['name']} · {MONTHS[today.month - 1]} {today.year}",
        "goal": goal, "experience": exp, "minutes": hi,
        "reason": opt["text"], "reasons": info.get("reasons", []), "frequency": info.get("frequency", {}),
        "focus": focus, "rotate": bool(opt.get("rotate")), "weekdays_chosen": s.get("weekdays") or [],
        "volume": {"target": peak, "planned": planned_peak, "labels": LABEL},
        "cycle": cycle,
        "block": {"weeks": n_weeks, "rir": vp.rir_by_week(n_weeks), "target": weeks_t,
                  "planned": planned_by_week, "variation": variation},
        "basis": info.get("pro", [])[:2] + basis,
        "days": days, "notes": [],
    }


def _fill_gaps(days, by_pattern, peak, per_session, goal, age, focus_groups, weeks_t, used):
    """Eine Muskelgruppe, die unter Erhaltung bleibt (z. B. Waden im Ganzkoerperplan), bekommt
    eine kurze Zusatzuebung am Tag mit der meisten Luft – sofern die Zeit es hergibt."""
    from .muscles import GROUPS as G
    for g, (_, mv, *_rest) in G.items():
        if not mv or not peak.get(g):
            continue
        have = sum(e["peak_sets"] for d in days for e in d["exercises"] if group_of(e["pattern"]) == g)
        if have >= mv:
            continue
        pattern = next((p for p in GROUP_PATTERNS.get(g, ()) if by_pattern.get(p)), None)
        if not pattern:
            continue
        need = min(mv - have, MAX_SETS)
        day = min(days, key=lambda d: sum(e["peak_sets"] for e in d["exercises"]))
        room = per_session - sum(e["peak_sets"] for e in day["exercises"])
        taken = {e["exercise_id"] for e in day["exercises"]}
        ex = _pick(by_pattern[pattern], used, taken, 0)
        if not ex or room < MIN_SETS:
            continue
        used[ex["id"]] = used.get(ex["id"], 0) + 1
        day["exercises"].append(_entry(ex, pattern, max(MIN_SETS, min(need, room)), goal, age, focus_groups, weeks_t, peak))


PUSH = ("h_push", "v_push", "chest_iso")
MAX_PUSH_PULL = 1.3


def _balance_push_pull(days, by_pattern, per_session, goal, age, focus_groups, weeks_t, peak, used):
    """Druecken : Ziehen hoechstens 1,3 : 1 – erst Ziehen ergaenzen, wo Zeit ist, sonst eine Druckuebung kuerzen."""
    def totals():
        push = sum(e["peak_sets"] for d in days for e in d["exercises"] if e["pattern"] in PUSH)
        push += 0.5 * sum(e["peak_sets"] for d in days for e in d["exercises"] if e["pattern"] == "triceps")
        pull = sum(e["peak_sets"] for d in days for e in d["exercises"] if e["pattern"] in PULL)
        pull += 0.5 * sum(e["peak_sets"] for d in days for e in d["exercises"] if e["pattern"] == "biceps")
        return push, pull

    for _ in range(4):
        push, pull = totals()
        if not push or push <= MAX_PUSH_PULL * max(pull, 1):
            return
        added = False
        for pattern in ("h_pull", "rear_delt", "v_pull"):
            for day in sorted(days, key=lambda d: sum(e["peak_sets"] for e in d["exercises"])):
                room = per_session - sum(e["peak_sets"] for e in day["exercises"])
                taken = {e["exercise_id"] for e in day["exercises"]}
                ex = _pick(by_pattern.get(pattern, []), used, taken, 0)
                if ex and room >= MIN_SETS and not any(e["pattern"] == pattern for e in day["exercises"]):
                    used[ex["id"]] = used.get(ex["id"], 0) + 1
                    day["exercises"].append(_entry(ex, pattern, MIN_SETS, goal, age, focus_groups, weeks_t, peak))
                    added = True
                    break
            if added:
                break
        if added:
            continue
        # keine Zeit mehr: Brust-Isolation streichen, sonst die groesste Druckuebung um einen Satz kuerzen
        cand = [(d, e) for d in days for e in d["exercises"] if e["pattern"] == "chest_iso" and len(d["exercises"]) > MIN_EXERCISES]
        if cand:
            cand[0][0]["exercises"].remove(cand[0][1])
            continue
        pushes = [e for d in days for e in d["exercises"] if e["pattern"] in PUSH and e["peak_sets"] > MIN_SETS]
        if not pushes:
            return
        e = max(pushes, key=lambda x: x["peak_sets"])
        e["peak_sets"] -= 1
        e["week_sets"] = _week_sets(e["pattern"], e["peak_sets"], weeks_t, peak)
        e["target_sets"] = e["week_sets"][0]


def _week_sets(pattern, peak_sets, weeks_t, peak):
    """Saetze je Blockwoche einer Uebung: jede Trainingswoche gleich, der Deload halb (wie blocks.sets_for)."""
    n = len(weeks_t[group_of(pattern)])
    return [peak_sets] * (n - 1) + [max(1, math.ceil(peak_sets / 2))]


def _entry(ex, pattern, peak_sets, goal, age, focus_groups, weeks_t, peak):
    compound = pattern in COMPOUND
    lo, hi = vp.reps_for(pattern, goal, compound)
    if age and age >= 50:
        lo, hi = max(lo, 6), max(hi, 10)
    elif age and age >= 40 and not compound:
        lo, hi = max(lo, 10), max(hi, 15)
    g = group_of(pattern)
    week_sets = _week_sets(pattern, peak_sets, weeks_t, peak)
    return {
        "exercise_id": ex["id"], "name": ex["name"], "pattern": pattern,
        "muscle_group": ex["muscle_group"], "unit": ex["unit"],
        "target_sets": week_sets[0], "target_reps_min": lo, "target_reps_max": hi,
        "rest_seconds": vp.rest_for(pattern, goal, compound),
        "focus": g in focus_groups, "week_sets": week_sets, "peak_sets": peak_sets,
        "equipment_type": ex["equipment_type"], "sfr": ex["sfr"],
    }


def alternatives(pattern, exclude=(), injuries=()):
    """Gleiches Muster, ohne Ausgeschlossene und ohne belastende Uebungen, beste zuerst."""
    exclude = set(exclude or ())
    items = [e for e in catalog() if e["pattern"] == pattern and e["id"] not in exclude
             and usable(e, injuries)]
    return sorted(items, key=lambda e: (-(e["sfr"] or 0), e["id"]))
