"""Prueflauf ueber alle Kombinationen des Plan-Assistenten (Tage x Erfahrung x Ziel x Minuten x Fokus x Split).

Zaehlt Plaene (nicht Meldungen) je Problem und trennt Plaene, deren Zeitbudget nicht einmal fuer
Erhaltung aller Muskeln reicht ("zu knapp") von denen, wo es reichen wuerde ("machbar").

    FITNESS_DB=/pfad/kopie.db PYTHONPATH=. .venv/bin/python scripts/plan_matrix.py [-v]

Nie gegen die echte Datenbank laufen lassen: db.init() schreibt ein Backup und migriert.
"""
import collections
import itertools
import sys

from app import db
from app.planner import generate, validate, volume_plan as vp
from app.planner.muscles import GROUPS, group_of

VERBOSE = "-v" in sys.argv
MV_SUM = sum(mv for _, mv, *_ in GROUPS.values())


def main():
    db.init()
    base = db.profile_out(db.one("SELECT * FROM profiles ORDER BY id LIMIT 1")) or {}
    plans = {"machbar": 0, "zu knapp": 0}
    hits = {k: collections.defaultdict(set) for k in plans}
    examples = collections.defaultdict(list)

    def hit(kind, budget, ctx, msg=""):
        hits[budget][kind].add(ctx)
        if len(examples[kind]) < 3:
            examples[kind].append((ctx, msg))

    for days, exp, goal, mins, focus in itertools.product(
            (2, 3, 4, 5, 6), ("einsteiger", "fortgeschritten", "erfahren"),
            ("aufbau", "kraft", "abnehmen", "fitness"), (45, 60, 90), (None, "po", "brust", "arme")):
        fl = [focus] if focus else []
        prof = {**base, "experience": exp, "goal": goal, "session_minutes": mins, "focus": fl,
                "limitations": None, "sex": None}
        ov = {"days": days, "weekdays": generate.pick_days([], days), "minutes": mins}
        total, per_session = vp.capacity(days, mins, goal)
        budget = "machbar" if total >= MV_SUM else "zu knapp"
        for o in generate.split_options(prof, {**ov, "goal": goal, "experience": exp, "focus": fl or None}):
            ctx = (days, exp, goal, mins, focus, o["key"])
            plans[budget] += 1
            try:
                plan = generate.generate(prof, o["key"], ov)
            except Exception as e:      # noqa: BLE001
                hit("Fehler", budget, ctx, repr(e))
                continue
            exs = [e for d in plan["days"] for e in d["exercises"]]
            if "trizeps" not in {group_of(e["pattern"]) for e in exs}:
                hit("ohne Trizeps", budget, ctx)
            push = sum(e["peak_sets"] for e in exs if e["pattern"] in generate.PUSH) \
                + 0.5 * sum(e["peak_sets"] for e in exs if e["pattern"] == "triceps")
            pull = sum(e["peak_sets"] for e in exs if e["pattern"] in generate.PULL) \
                + 0.5 * sum(e["peak_sets"] for e in exs if e["pattern"] == "biceps")
            if push > generate.MAX_PUSH_PULL * max(pull, 1):
                hit("Druck:Zug > 1,3", budget, ctx, f"{push}:{pull}")
            for d in plan["days"]:
                if len(d["exercises"]) < generate.MIN_EXERCISES:
                    hit("kurzer Tag", budget, ctx, d["label"])
                ids = [e["exercise_id"] for e in d["exercises"]]
                if len(ids) != len(set(ids)):
                    hit("doppelte Übung", budget, ctx, d["label"])
                for w in range(plan["block"]["weeks"]):
                    tot = sum(e["week_sets"][w] for e in d["exercises"])
                    if tot > per_session:
                        hit("über Zeit", budget, ctx, f"{d['label']} W{w + 1} {tot}>{per_session}")
                per_g = collections.Counter()
                for e in d["exercises"]:
                    per_g[group_of(e["pattern"])] += e["peak_sets"]
                for g, v in per_g.items():
                    if v > generate.MAX_SESSION_SETS_PER_MUSCLE:
                        hit("Muskel > 8 je Einheit", budget, ctx, f"{d['label']} {g} {v}")
            res = validate.analyze(plan, minutes=mins, goal=goal, rotate=plan["rotate"], experience=exp, focus=fl)
            for f in res["findings"]:
                if "mehr als sich erholen" in f["text"]:
                    hit("über Höchstvolumen", budget, ctx, f["text"])
                elif "nicht einmal" in f["text"]:
                    hit("unter Erhaltung", budget, ctx, f["text"])
                    hit(f"unter Erhaltung: {f['group']}", budget, ctx)
                elif f["group"] == "balance":
                    hit("Druck:Zug (Prüfer)", budget, ctx)
                elif f["level"] == "warn" and "keine Übung" not in f["text"]:
                    hit("andere Warnung", budget, ctx, f["text"])

    print(f"Pläne: {sum(plans.values())} (machbar {plans['machbar']}, zu knapp {plans['zu knapp']}; "
          f"Erhaltung aller Muskeln = {MV_SUM} Sätze/Woche)")
    kinds = sorted({k for b in hits.values() for k in b})
    for k in kinds:
        print(f"  {k:32} machbar {len(hits['machbar'][k]):4}   zu knapp {len(hits['zu knapp'][k]):4}")
        if VERBOSE:
            for ctx, msg in examples[k]:
                print("      ", ctx, msg)


if __name__ == "__main__":
    main()
