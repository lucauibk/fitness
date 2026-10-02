"""Split-Katalog und Empfehlung.

Ein Split ist eine Folge von Trainingstagen pro Woche (z. B. OK A, UK A, OK B). Die Folge
wird der Reihe nach auf die gewaehlten Wochentage gelegt. Wie oft jede Muskelgruppe
dran ist, wird aus den Tages-Vorlagen berechnet – daraus und aus den Profildaten
entsteht die Rangfolge samt Begruendung.

Grundlage der Regeln: Muskelgruppen mind. 2x pro Woche (Schoenfeld et al. 2016),
mind. ~48 h Pause fuer dieselbe Muskulatur, abwechselnde Koerperhaelften erlauben
Training an aufeinanderfolgenden Tagen.
"""

# Tages-Vorlagen: Muster in Prioritaet – gekuerzt wird von hinten
TEMPLATES = {
    "Ganzkörper A": ["squat", "h_push", "v_pull", "hinge", "lateral", "biceps", "core", "triceps"],
    "Ganzkörper B": ["hinge", "v_push", "h_pull", "lunge", "triceps", "core", "chest_iso", "calves"],
    "Ganzkörper C": ["squat", "h_push", "h_pull", "v_push", "leg_iso_back", "core", "biceps", "triceps"],
    "Push": ["h_push", "v_push", "h_push", "lateral", "triceps", "chest_iso", "triceps", "lateral"],
    "Pull": ["v_pull", "h_pull", "rear_delt", "biceps", "h_pull", "core", "biceps", "v_pull"],
    "Beine": ["squat", "hinge", "leg_iso_back", "core", "calves", "lunge", "leg_iso_front", "glutes"],
    "Oberkörper A": ["h_push", "v_pull", "v_push", "h_pull", "triceps", "lateral", "biceps", "chest_iso"],
    "Oberkörper B": ["v_pull", "h_push", "h_pull", "v_push", "biceps", "rear_delt", "triceps", "lateral"],
    "Unterkörper A": ["squat", "hinge", "lunge", "leg_iso_front", "core", "calves", "glutes", "leg_iso_back"],
    "Unterkörper B": ["hinge", "glutes", "lunge", "leg_iso_back", "core", "calves", "glutes", "squat"],
    "Brust": ["h_push", "h_push", "chest_iso", "v_push", "triceps", "chest_iso", "triceps", "core"],
    "Rücken": ["v_pull", "h_pull", "h_pull", "v_pull", "rear_delt", "biceps", "core", "biceps"],
    "Schultern & Arme": ["v_push", "lateral", "rear_delt", "biceps", "triceps", "biceps", "triceps", "lateral"],
}

# Muster -> Muskelgruppen (fuer Haeufigkeit und Ueberschneidung)
PATTERN_GROUPS = {
    "squat": ["beine", "po"], "lunge": ["beine", "po"], "leg_iso_front": ["beine"],
    "leg_iso_back": ["beine"], "calves": ["beine"], "hinge": ["po", "beine"],
    "glutes": ["po"], "h_push": ["brust", "arme"], "chest_iso": ["brust"],
    "v_push": ["schultern", "arme"], "lateral": ["schultern"], "rear_delt": ["schultern", "ruecken"],
    "h_pull": ["ruecken", "arme"], "v_pull": ["ruecken", "arme"],
    "biceps": ["arme"], "triceps": ["arme"], "core": ["rumpf"],
}
MAJOR = ("beine", "po", "brust", "ruecken", "schultern")
# Fuer die Erholung an aufeinanderfolgenden Tagen zaehlen nur die grossen Muskeln –
# Schultern arbeiten bei Druecken und Ziehen ohnehin immer etwas mit.
RECOVERY = ("beine", "po", "brust", "ruecken")
GROUP_LABEL = {"beine": "Beine", "po": "Po", "brust": "Brust", "ruecken": "Rücken",
               "schultern": "Schultern", "arme": "Arme", "rumpf": "Rumpf"}
WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
DEFAULT_DAYS = {2: [0, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5]}

OKA, OKB, UKA, UKB = "Oberkörper A", "Oberkörper B", "Unterkörper A", "Unterkörper B"
GKA, GKB, GKC = "Ganzkörper A", "Ganzkörper B", "Ganzkörper C"

# key: (Name, Kurztext, Tagesfolge, Art, Optionen)
CATALOG = {
    2: {
        "gk2": ("Ganzkörper A/B", "Zwei Einheiten, jede für den ganzen Körper.", [GKA, GKB], "gk", {}),
        "okuk2": ("Oberkörper / Unterkörper", "Ein Tag oben, ein Tag unten.", [OKA, UKA], "okuk", {}),
    },
    3: {
        "gk3": ("Ganzkörper A/B/C", "Jede Muskelgruppe dreimal pro Woche.", [GKA, GKB, GKC], "gk", {}),
        "ppl3": ("Push / Pull / Beine", "Drücken, Ziehen und Beine getrennt.", ["Push", "Pull", "Beine"], "ppl", {}),
        "ukokuk3": ("Unterkörper / Oberkörper / Unterkörper", "Beine und Po zweimal, Oberkörper einmal pro Woche.",
                    [UKA, OKA, UKB], "okuk", {}),
        "okukok3": ("Oberkörper / Unterkörper / Oberkörper", "Oberkörper zweimal, Beine und Po einmal pro Woche.",
                    [OKA, UKA, OKB], "okuk", {}),
        "okukwechsel3": ("Ober-/Unterkörper im Wochenwechsel", "Diese Woche OK/UK/OK, nächste UK/OK/UK – "
                         "jede Muskelgruppe dreimal in zwei Wochen.", [OKA, UKA, OKB, UKB], "okuk",
                         {"rotate": True}),
    },
    4: {
        "okuk4": ("Oberkörper / Unterkörper ×2", "Jede Hälfte zweimal pro Woche, mit A/B-Varianten.",
                  [OKA, UKA, OKB, UKB], "okuk", {}),
        "gk4": ("Ganzkörper ×4", "Vier kürzere Ganzkörper-Einheiten.", [GKA, GKB, GKA, GKB], "gk", {}),
        "ukokukgk4": ("Unterkörper-Fokus (UK/OK/UK/GK)", "Beine und Po bis zu dreimal pro Woche.",
                      [UKA, OKA, UKB, GKA], "mix", {}),
        "okukokgk4": ("Oberkörper-Fokus (OK/UK/OK/GK)", "Oberkörper bis zu dreimal pro Woche.",
                      [OKA, UKA, OKB, GKA], "mix", {}),
    },
    5: {
        "pplokuk5": ("Push / Pull / Beine + Ober-/Unterkörper", "Jede Muskelgruppe etwa zweimal pro Woche.",
                     ["Push", "Pull", "Beine", OKA, UKB], "ppl", {}),
        "okukgk5": ("Ober-/Unterkörper + Ganzkörper", "Vier geteilte Tage und ein Ganzkörpertag.",
                    [OKA, UKA, GKA, OKB, UKB], "okuk", {}),
        "bro5": ("Muskelgruppen-Split", "Jeder Tag eine Muskelgruppe – viel Volumen pro Muskel, "
                 "aber meist nur einmal pro Woche.", ["Brust", "Rücken", UKA, "Schultern & Arme", UKB],
                 "bro", {"min_experience": "fortgeschritten"}),
    },
    6: {
        "ppl6": ("Push / Pull / Beine ×2", "Jeder Teil zweimal pro Woche.",
                 ["Push", "Pull", "Beine", "Push", "Pull", "Beine"], "ppl", {}),
        "okuk6": ("Oberkörper / Unterkörper ×3", "Zwei Tagestypen im Wechsel.",
                  [OKA, UKA, OKB, UKB, OKA, UKB], "okuk", {}),
    },
}
LEVEL = {"einsteiger": 0, "fortgeschritten": 1, "erfahren": 2}


def find(key):
    for days, options in CATALOG.items():
        if key in options:
            name, text, seq, kind, opts = options[key]
            return {"key": key, "days": days, "name": name, "text": text, "sequence": seq,
                    "kind": kind, **opts}
    return None


def day_groups(label, count):
    """Muskelgruppen, die ein Tag bei `count` Uebungen trainiert."""
    groups = set()
    for pattern in TEMPLATES[label][:count]:
        groups.update(PATTERN_GROUPS.get(pattern, []))
    return groups


def layout(option, weekdays=None):
    """[(Tag, [Wochentage])]. Beim Wochenwechsel ohne feste Tage (Rotation)."""
    seq = option["sequence"]
    if option.get("rotate"):
        return [(label, []) for label in seq]
    chosen = sorted(set(weekdays or []))
    if len(chosen) != len(seq):
        chosen = DEFAULT_DAYS[len(seq)]
    out = {}
    for wd, label in zip(chosen, seq):
        out.setdefault(label, []).append(wd)
    order = list(dict.fromkeys(seq))
    return [(label, out[label]) for label in order]


def week_sequence(option, days_per_week):
    """Tagesfolge einer Woche; beim Wochenwechsel der Durchschnitt zweier Wochen."""
    seq = option["sequence"]
    if option.get("rotate"):
        n = days_per_week
        two_weeks = [seq[i % len(seq)] for i in range(2 * n)]
        return two_weeks[:n], two_weeks[n:]
    return seq, seq


def frequency(option, count, days_per_week):
    weeks = week_sequence(option, days_per_week)
    freq = {g: 0.0 for g in GROUP_LABEL}
    for week in weeks:
        for label in week:
            for g in day_groups(label, count):
                freq[g] += 1 / len(weeks)
    return {g: round(v, 1) for g, v in freq.items()}


def back_to_back_pairs(weekdays):
    wd = sorted(set(weekdays or []))
    pairs = [(a, b) for a, b in zip(wd, wd[1:]) if b - a == 1]
    if 0 in wd and 6 in wd:
        pairs.append((6, 0))
    return pairs


def _names(groups):
    labels = [GROUP_LABEL[g] for g in groups]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " und " + labels[-1]


def _span(weekdays):
    """"Mi–Fr" bei einem durchgehenden Block, sonst die Paare ("Mo/Di und Do/Fr")."""
    wd = sorted(set(weekdays))
    pairs = back_to_back_pairs(wd)
    if len(pairs) == len(wd) - 1 and all(b - a == 1 for a, b in zip(wd, wd[1:])):
        return f"{WD[wd[0]]}–{WD[wd[-1]]}"
    return " und ".join(f"{WD[a]}/{WD[b]}" for a, b in pairs)


def rank(days, weekdays, profile, count):
    """Alle passenden Optionen, bewertet und begruendet. profile: goal, experience,
    minutes, focus, age."""
    days = min(max(int(days or 3), 2), 6)
    weekdays = sorted(set(weekdays or []))
    if len(weekdays) != days:
        weekdays = DEFAULT_DAYS[days]          # Tage passen nicht zur Einheitenzahl: Standardtage bewerten
    exp = profile.get("experience") or "einsteiger"
    goal = profile.get("goal") or "aufbau"
    minutes = profile.get("minutes") or 60
    age = profile.get("age")
    focus = [f for f in (profile.get("focus") or []) if f in GROUP_LABEL]
    pairs = back_to_back_pairs(weekdays)
    out = []
    for key in CATALOG[days]:
        opt = find(key)
        if LEVEL.get(exp, 0) < LEVEL.get(opt.get("min_experience", "einsteiger"), 0):
            continue
        freq = frequency(opt, count, days)
        score, pro, contra = 0.0, [], []

        # --- Fokus bzw. Ausgewogenheit
        if focus:
            fmin = min(freq[f] for f in focus)
            others = [freq[g] for g in MAJOR if g not in focus] or [0]
            avg_other = sum(others) / len(others)
            if fmin >= 2:
                score += 3
                singular = len(focus) == 1 and focus[0] in ("brust", "ruecken", "po", "rumpf")
                pro.append(f"{_names(focus)} {'kommt' if singular else 'kommen'} "
                           f"{fmin:g}× pro Woche dran – passend zu deinem Fokus.")
            elif fmin < avg_other:
                score -= 1.5
                contra.append(f"Dein Fokus ({_names(focus)}) kommt hier seltener dran als der Rest.")
            score += max(-2, min(2, fmin - avg_other))
            if opt.get("rotate"):
                score -= 1
        else:
            low = min(freq[g] for g in MAJOR)
            if low >= 1.5:
                score += 2
                pro.append(f"Alle großen Muskelgruppen kommen mindestens {low:g}× pro Woche dran."
                           .replace(".5×", ",5×"))
            elif low < 1:
                score -= 1
            if opt.get("rotate"):
                score += 1
                pro.append("Ausgewogen: im Wechsel trainiert jede Muskelgruppe dreimal in zwei Wochen.")
            spread = max(freq[g] for g in MAJOR) - low
            if spread >= 1 and low < 1.5 and not opt.get("rotate") and opt["kind"] != "gk":
                score -= 0.5 * spread
                contra.append("Setzt einen Schwerpunkt, den du nicht als Fokus gewählt hast.")

        # --- Lage der Tage: Ueberschneidung an aufeinanderfolgenden Tagen
        if pairs:
            placed = {wd: label for label, wds in layout(opt, weekdays) for wd in wds}
            overlap = 0
            for a, b in pairs:
                if opt.get("rotate") or a not in placed or b not in placed:
                    continue  # wechselt ohnehin die Hälfte
                shared = (day_groups(placed[a], count) & day_groups(placed[b], count)) & set(RECOVERY)
                overlap += len(shared)
            factor = 1.5 if age and age >= 45 else 1
            if overlap:
                score -= overlap * factor
                contra.append(f"{_span(weekdays)}: an aufeinanderfolgenden Tagen werden dieselben "
                              "Muskeln belastet – wenig Erholung.")
            else:
                score += 1.5
                pro.append(f"{_span(weekdays)} liegen "
                           "direkt hintereinander – durch den Wechsel hat jede Muskelgruppe trotzdem genug Pause.")

        # --- Erfahrung
        if exp == "einsteiger":
            if opt["kind"] == "gk":
                score += 1.5
                pro.append("Als Einsteiger übst du die Grundübungen so öfter pro Woche.")
            if opt["kind"] in ("ppl", "bro"):
                score -= 1
        elif exp == "erfahren" and opt["kind"] in ("okuk", "ppl", "bro"):
            score += 0.5
            pro.append("Mehr Volumen pro Einheit – passend zu deiner Erfahrung.")

        # --- Dauer
        if opt["kind"] == "gk":
            if minutes <= 45:
                score -= 1.5
                contra.append(f"Ganzkörper in {minutes} Minuten ist knapp – nur eine Übung je Muskel.")
            elif minutes >= 90:
                score += 0.5

        # --- Ziel
        if goal == "kraft" and opt["kind"] in ("okuk", "ppl"):
            score += 1
            pro.append("Für Kraft: die Grundübungen kommen oft genug dran.")
        if goal in ("abnehmen", "fitness") and opt["kind"] in ("gk", "okuk"):
            score += 0.5
        if age and age >= 45 and opt["kind"] == "bro":
            score -= 0.5

        out.append({
            "key": key, "name": opt["name"], "text": opt["text"], "score": round(score, 2),
            "rotate": bool(opt.get("rotate")),
            "days": [{"label": label, "weekdays": wds} for label, wds in layout(opt, weekdays)],
            "frequency": freq, "reasons": (pro[:3] + contra[:2])[:4],
            "pro": pro[:3], "contra": contra[:2], "focus": focus,
        })
    out.sort(key=lambda o: -o["score"])
    for i, o in enumerate(out):
        o["recommended"] = i == 0
    return out
