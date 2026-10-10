"""Prueflauf ueber alle Kombinationen des Plan-Assistenten (Tage x Erfahrung x Ziel x Fokus x Split).

Zaehlt Plaene (nicht Meldungen) je Problem. Die Dauer wird nicht abgefragt, deshalb gibt es keine
Minuten-Achse: jede Einheit darf bis vp.MAX_SETS_PER_SESSION Saetze haben.

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


def main():
    db.init()
    base = db.profile_out(db.one("SELECT * FROM profiles ORDER BY id LIMIT 1")) or {}
    plans = 0
    hits = collections.defaultdict(set)
    examples = collections.defaultdict(list)

    def hit(kind, ctx, msg=""):
        hits[kind].add(ctx)
        if len(examples[kind]) < 3:
            examples[kind].append((ctx, msg))

    for days, exp, goal, focus in itertools.product(
            (2, 3, 4, 5, 6), ("einsteiger", "fortgeschritten", "erfahren"),
            ("aufbau", "kraft", "abnehmen", "fitness"), (None, "po", "brust", "arme")):
        fl = [focus] if focus else []
        prof = {**base, "experience": exp, "goal": goal, "focus": fl,
                "limitations": None, "sex": None}
        ov = {"days": days, "weekdays": generate.pick_days([], days)}
        per_session = vp.MAX_SETS_PER_SESSION
        for o in generate.split_options(prof, {**ov, "goal": goal, "experience": exp, "focus": fl or None}):
            ctx = (days, exp, goal, focus, o["key"])
            plans += 1
            try:
                plan = generate.generate(prof, o["key"], ov)
            except Exception as e:      # noqa: BLE001
                hit("Fehler", ctx, repr(e))
                continue
            exs = [e for d in plan["days"] for e in d["exercises"]]
            vol = plan["volume"]
            focus_groups = {g for f in fl for g in generate.FOCUS_GROUPS[f]}
            for g, t in vol["target"].items():
                if vol["planned"][g] < t - 2:      # wie der Hinweis „Nicht ganz erreicht“ im Generator
                    kind = f"Ziel verfehlt ({'Fokus' if g in focus_groups else 'sonst'}): {g}"
                    hit(kind, ctx, f"{vol['planned'][g]} statt {t}")
                    if o["recommended"]:
                        hit(kind + " [empfohlen]", ctx, f"{vol['planned'][g]} statt {t}")
            if vol["planned"]["po"] == 0:
                hit("Po ohne direkte Übung", ctx)
            if "trizeps" not in {group_of(e["pattern"]) for e in exs}:
                hit("ohne Trizeps", ctx)
            push = sum(e["peak_sets"] for e in exs if e["pattern"] in generate.PUSH) \
                + 0.5 * sum(e["peak_sets"] for e in exs if e["pattern"] == "triceps")
            pull = sum(e["peak_sets"] for e in exs if e["pattern"] in generate.PULL) \
                + 0.5 * sum(e["peak_sets"] for e in exs if e["pattern"] == "biceps")
            if push > generate.MAX_PUSH_PULL * max(pull, 1):
                hit("Druck:Zug > 1,3", ctx, f"{push}:{pull}")
            for d in plan["days"]:
                if len(d["exercises"]) < generate.MIN_EXERCISES:
                    hit("kurzer Tag", ctx, d["label"])
                ids = [e["exercise_id"] for e in d["exercises"]]
                if len(ids) != len(set(ids)):
                    hit("doppelte Übung", ctx, d["label"])
                for w in range(plan["block"]["weeks"]):
                    tot = sum(e["week_sets"][w] for e in d["exercises"])
                    if tot > per_session:
                        hit("über Zeit", ctx, f"{d['label']} W{w + 1} {tot}>{per_session}")
                per_g = collections.Counter()
                for e in d["exercises"]:
                    per_g[group_of(e["pattern"])] += e["peak_sets"]
                for g, v in per_g.items():
                    if v > generate.MAX_SESSION_SETS_PER_MUSCLE:
                        hit("Muskel > 8 je Einheit", ctx, f"{d['label']} {g} {v}")
            res = validate.analyze(plan, goal=goal, rotate=plan["rotate"], experience=exp, focus=fl)
            for f in res["findings"]:
                if "mehr als sich erholen" in f["text"]:
                    hit("über Höchstvolumen", ctx, f["text"])
                elif "nicht einmal" in f["text"]:
                    hit("unter Erhaltung", ctx, f["text"])
                    hit(f"unter Erhaltung: {f['group']}", ctx)
                elif f["group"] == "balance":
                    hit("Druck:Zug (Prüfer)", ctx)
                elif f["level"] == "warn" and "keine Übung" not in f["text"]:
                    hit("andere Warnung", ctx, f["text"])

    print(f"Pläne: {plans}")
    for k in sorted(hits):
        print(f"  {k:32} {len(hits[k]):4}")
        if VERBOSE:
            for ctx, msg in examples[k]:
                print("      ", ctx, msg)


if __name__ == "__main__":
    main()
