"""Plan pruefen: Regeln statt Stichwoerter, gleiche Muskelgruppen wie der Generator.

Eingabe: Plan als {"days": [{"label", "weekdays", "exercises": [{"exercise_id", "target_sets"}]}]}
(Vorschau oder gespeicherter Split). Ausgabe: {"weekly", "findings": [{"level", "group", "text"}]}.
"""
from .. import db
from . import blocks, volume_plan as vp
from .muscles import BIG, GROUPS, INJURY_AREAS, LABEL, PATTERN_GROUP

MIN_FREQUENCY = 2
vp_max = blocks.MAX_EXERCISE_SETS


def _catalog():
    return {e["id"]: e for e in db.rows(
        "SELECT id, name, pattern, contraindications FROM exercises")}


def peak_sets(item, pattern, ratios):
    """Saetze der Spitzenwoche: aus dem Plan (peak_sets), bei alten Plaenen aus Woche 1 hochgerechnet."""
    if item.get("peak_sets"):
        return item["peak_sets"]
    peak = max((ratios.get(PATTERN_GROUP.get(pattern)) or [1.0])[:-1])
    return min(vp_max, round((item.get("target_sets") or 0) * peak))


PUSH = ("h_push", "v_push", "chest_iso")
PULL = ("h_pull", "v_pull", "rear_delt")


def push_pull(items):
    """(Druecken, Ziehen) aus [(Muster, Saetze)]. Seitheben zaehlt zu keiner Seite, Arme halb
    (Trizeps hilft beim Druecken, Bizeps beim Ziehen)."""
    push = pull = 0.0
    for pattern, sets in items:
        if pattern in PUSH:
            push += sets
        elif pattern in PULL:
            pull += sets
        elif pattern == "triceps":
            push += 0.5 * sets
        elif pattern == "biceps":
            pull += 0.5 * sets
    return push, pull


def analyze(plan, injuries=(), minutes=None, goal="aufbau", rotate=False, experience="fortgeschritten", focus=()):
    cat = _catalog()
    # Der Plan ist Woche 1; geprueft wird die Spitzenwoche des Blocks (dort zaehlt das Volumen).
    ratios = blocks.ratios_for(goal, experience, focus)
    pattern_of = lambda item: (cat.get(item["exercise_id"]) or {}).get("pattern")     # noqa: E731
    weekly = vp.weekly_volume(plan["days"], lambda item: (pattern_of(item), peak_sets(item, pattern_of(item), ratios)),
                              plan.get("cycle") or 1.0)
    days_with = {g: set() for g in GROUPS}
    patterns = set()
    loads = []
    findings = []
    per_session = vp.capacity(max(2, len(plan["days"])), minutes, goal)[1]
    for i, day in enumerate(plan["days"]):
        times = max(1, len(day.get("weekdays") or []))      # Tag mit mehreren Wochentagen zaehlt mehrfach
        exs = day.get("exercises", [])
        sets_today = 0
        if not exs:
            findings.append({"level": "warn", "group": "plan", "text": f"„{day['label']}“ hat keine Übungen."})
        for item in exs:
            ex = cat.get(item["exercise_id"])
            if not ex:
                continue
            g = PATTERN_GROUP.get(ex["pattern"])
            sets = peak_sets(item, ex["pattern"], ratios)
            sets_today += sets
            if g:
                days_with[g].update((i, k) for k in range(times))
                patterns.add(ex["pattern"])
                loads.append((ex["pattern"], sets))         # je Tageszeile wie im Generator (_balance_push_pull)
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
        # unter 6 Saetzen passt der Muskel gut in eine Einheit – dann ist einmal pro Woche kein Mangel
        if weekly[g] >= 6 and len(days_with[g]) < MIN_FREQUENCY and n_days >= 3 and not rotate:
            findings.append({"level": "info", "group": g,
                             "text": f"{LABEL[g]}: alle {weekly[g]} Sätze an einem Tag – auf zwei Tage verteilt "
                                     "bringt jeder Satz mehr."})
    for g, (_, mv, mev, mav, mrv) in GROUPS.items():
        if g == "rumpf":
            continue
        if weekly[g] == 0 and mv == 0:
            continue
        if weekly[g] == 0:
            findings.append({"level": "warn", "group": g, "text": f"Für {LABEL[g]} ist keine Übung eingeplant."})
        elif weekly[g] > mrv:
            findings.append({"level": "warn", "group": g,
                             "text": f"{LABEL[g]}: {weekly[g]} Sätze pro Woche sind mehr als sich erholen lässt (≈ {mrv})."})
        elif weekly[g] < mv - 1:
            findings.append({"level": "info", "group": g,
                             "text": f"{LABEL[g]}: {weekly[g]} Sätze pro Woche reichen nicht einmal zum Erhalten."})
    push, pull = push_pull(loads)
    if pull and push / pull > 1.6:
        findings.append({"level": "warn", "group": "balance",
                         "text": "Viel mehr Drücken als Ziehen – mehr Rudern oder Latzug schützt die Schultern."})
    if weekly["schultern"] and "rear_delt" not in patterns:
        findings.append({"level": "info", "group": "schultern", "text": "Keine Übung für die hintere Schulter."})
    if weekly["beinbeuger"] == 0 and weekly["quads"]:
        findings.append({"level": "warn", "group": "beinbeuger", "text": "Beinrückseite fehlt."})
    return {"weekly": weekly, "findings": findings}
