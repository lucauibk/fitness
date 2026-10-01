"""Plan pruefen: Regeln statt Stichwoerter, gleiche Muskelgruppen wie der Generator.

Eingabe: Plan als {"days": [{"label", "weekdays", "exercises": [{"exercise_id", "target_sets"}]}]}
(Vorschau oder gespeicherter Split). Ausgabe: {"weekly", "findings": [{"level", "group", "text"}]}.
"""
from .. import db
from . import volume_plan as vp
from .muscles import BIG, GROUPS, INJURY_AREAS, LABEL, PATTERN_GROUP

MIN_FREQUENCY = 2


def _catalog():
    return {e["id"]: e for e in db.rows(
        "SELECT id, name, pattern, contraindications FROM exercises")}


def analyze(plan, injuries=(), minutes=None, goal="aufbau", rotate=False):
    cat = _catalog()
    weekly = {g: 0 for g in GROUPS}
    days_with = {g: set() for g in GROUPS}
    patterns = set()
    findings = []
    per_session = vp.capacity(max(2, len(plan["days"])), minutes, goal)[1]
    for i, day in enumerate(plan["days"]):
        exs = day.get("exercises", [])
        sets_today = 0
        if not exs:
            findings.append({"level": "warn", "group": "plan", "text": f"„{day['label']}“ hat keine Übungen."})
        for item in exs:
            ex = cat.get(item["exercise_id"])
            if not ex:
                continue
            sets = item.get("target_sets") or 0
            sets_today += sets
            g = PATTERN_GROUP.get(ex["pattern"])
            if g:
                weekly[g] += sets
                days_with[g].add(i)
                patterns.add(ex["pattern"])
            bad = set((ex["contraindications"] or "").split(",")) & set(injuries)
            if bad:
                findings.append({"level": "warn", "group": "verletzung",
                                 "text": f"{ex['name']} belastet {', '.join(INJURY_AREAS[b] for b in sorted(bad))} – "
                                         "bitte tauschen."})
        if minutes and sets_today > per_session + 2:
            findings.append({"level": "info", "group": "zeit",
                             "text": f"„{day['label']}“ hat {sets_today} Sätze – für {minutes} Minuten sind ≈ {per_session} realistisch."})

    n_days = len(plan["days"])
    for g in BIG:
        if weekly[g] and len(days_with[g]) < MIN_FREQUENCY and n_days >= 3 and not rotate:
            findings.append({"level": "info", "group": g,
                             "text": f"{LABEL[g]} kommt nur an {len(days_with[g])} Tag pro Woche dran – "
                                     "zweimal pro Woche wächst besser."})
    for g, (_, mv, mev, mav, mrv) in GROUPS.items():
        if g == "rumpf":
            continue
        if weekly[g] == 0:
            findings.append({"level": "warn", "group": g, "text": f"Für {LABEL[g]} ist keine Übung eingeplant."})
        elif weekly[g] > mrv:
            findings.append({"level": "warn", "group": g,
                             "text": f"{LABEL[g]}: {weekly[g]} Sätze pro Woche sind mehr als sich erholen lässt (≈ {mrv})."})
        elif weekly[g] < mv:
            findings.append({"level": "info", "group": g,
                             "text": f"{LABEL[g]}: {weekly[g]} Sätze pro Woche reichen nicht einmal zum Erhalten."})
    push = weekly["brust"] + weekly["schultern"] + weekly["trizeps"] * 0.5
    pull = weekly["ruecken"] + weekly["bizeps"] * 0.5
    if pull and push / pull > 1.6:
        findings.append({"level": "warn", "group": "balance",
                         "text": "Viel mehr Drücken als Ziehen – mehr Rudern oder Latzug schützt die Schultern."})
    if weekly["schultern"] and "rear_delt" not in patterns:
        findings.append({"level": "info", "group": "schultern", "text": "Keine Übung für die hintere Schulter."})
    if weekly["beinbeuger"] == 0 and weekly["quads"]:
        findings.append({"level": "warn", "group": "beinbeuger", "text": "Beinrückseite fehlt."})
    return {"weekly": weekly, "findings": findings}
