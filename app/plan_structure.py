"""Struktur-Vorschlaege fuer einen bestehenden (auch selbst gebauten) Plan – hoechstens einer auf einmal:

1. Einen Trainingstag ergaenzen, z. B. OK/UK -> OK A · UK · OK B. Welche Folge passt, entscheidet der Split-Katalog
   (splits.CATALOG/rank: Fokus, Erfahrung, Erholung); bevorzugt wird ein Tag, der die zu vollen Einheiten entlastet.
   Das Wochenvolumen wird auf alle Einheiten verteilt statt verdoppelt: der Geschwistertag gibt Saetze ab.
2. Einen Tag, der an mehreren Wochentagen gleich laeuft, in A/B-Varianten teilen: Muskeln mit mehreren Uebungen
   verteilen diese auf A und B, das Volumen je Einheit bleibt gleich, die Einheiten haben weniger Uebungen.

Grundlage: Fuer Muskelaufbau zaehlt vor allem das Wochenvolumen, die Haeufigkeit kaum, solange die Saetze je
Einheit nicht zu viele werden (Pelland et al. 2026, Remmert et al. 2023). Ein zusaetzlicher Tag lohnt sich also,
wenn Einheiten zu lang werden oder Volumen nirgends mehr hineinpasst – nicht als Selbstzweck.
Umgesetzt wird alles in einer Transaktion (apply) und nur, solange der Plan noch so aussieht wie in der Vorschau.
"""
from collections import Counter

from . import analysis, db, splits
from .planner import generate, muscles
from .planner import volume_plan as vp
from .planner.profile_model import injuries_from

KIND_OF = {splits.OKA: "ok", splits.OKB: "ok", splits.UKA: "uk", splits.UKB: "uk",
           splits.GKA: "gk", splits.GKB: "gk", splits.GKC: "gk", "Push": "push", "Pull": "pull", "Beine": "uk"}
KIND_NAME = {"ok": "Oberkörper", "uk": "Unterkörper", "gk": "Ganzkörper", "push": "Push", "pull": "Pull"}
KIND_SHORT = {"ok": "OK", "uk": "UK", "gk": "GK", "push": "Push", "pull": "Pull"}
PUSHING = {"h_push", "v_push", "chest_iso", "lateral", "triceps", "core"}
PULLING = {"h_pull", "v_pull", "rear_delt", "biceps", "core"}
MAX_NEW_EXERCISES = 8
MAX_OPS = 80
WD = splits.WD


# ------------------------------------------------------------------ Tage einordnen
def day_kind(day):
    """Tagestyp aus den Muskeln der Uebungen (nicht aus dem Namen – "OK", "Oberkörper", "Push" …)."""
    groups = {analysis.group_of(e) for e in day["exercises"]} - {None, "rumpf"}
    if not groups:
        return None
    if groups <= analysis.UPPER:
        pats = {e.get("pattern") for e in day["exercises"]} - {None}
        if pats <= PUSHING:
            return "push"
        if pats <= PULLING:
            return "pull"
        return "ok"
    if groups <= analysis.LOWER:
        return "uk"
    return "gk"


def _template_groups(label, patterns=None):
    return {muscles.PATTERN_GROUP[p] for p in (patterns or splits.TEMPLATES[label])}


def _neighbors(wd):
    return ((wd - 1) % 7, (wd + 1) % 7)


def _place(free, days, groups):
    """Freie Wochentage, beste zuerst: keine grossen Muskeln am Nachbartag doppelt, moeglichst viel Abstand."""
    by_wd = {}
    for day in days:
        for wd in day.get("weekdays") or []:
            by_wd.setdefault(wd, set()).update(analysis.group_of(e) for e in day["exercises"])
    used = set(by_wd)
    scored = []
    for wd in free:
        clash = set()
        for n in _neighbors(wd):
            clash |= by_wd.get(n, set()) & set(groups) & set(muscles.BIG)
        after = min(((wd - u) % 7 for u in used), default=7)
        before = min(((u - wd) % 7 for u in used), default=7)
        scored.append((len(clash), -min(after, before), -(after + before), wd, sorted(clash)))
    scored.sort()
    return [{"weekday": wd, "label": WD[wd], "clash": [muscles.LABEL[g] for g in clash]}
            for *_, wd, clash in scored]


def _free_days(profile, days):
    available = db.parse_weekdays(profile.get("available_weekdays")) or list(range(7))
    used = {wd for d in days for wd in (d.get("weekdays") or [])}
    return [wd for wd in available if wd not in used]


def _labels(existing_label, kind, labels_in_use):
    """(neuer Name des vorhandenen Tages oder None, Name des neuen Tages). "OK" -> "OK A" + "OK B"."""
    if not existing_label and KIND_SHORT[kind] not in labels_in_use:
        return None, KIND_SHORT[kind]
    base = existing_label.strip() if existing_label else KIND_SHORT[kind]
    rename = None
    for suffix in (" A", " B", " C"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    else:
        if existing_label:
            rename = f"{base} A"
    taken = set(labels_in_use) | ({rename} if rename else set())
    for letter in "BCDEF":
        if f"{base} {letter}" not in taken:
            return rename, f"{base} {letter}"
    return rename, f"{base} {len(taken) + 1}"


# ------------------------------------------------------------------ Uebungen und Saetze
def _choose(pattern, avoid, settings, prefer_id=None, variant=False):
    """Uebung fuer ein Muster. variant: bewusst eine andere als prefer_id (Abwechslung, gleiche Art – Grunduebung
    bleibt Grunduebung), sonst gern die bekannte."""
    known = db.get_row("exercises", prefer_id) if prefer_id else None
    if known and not variant:
        return known
    options = [e for e in generate.catalog() if e["pattern"] == pattern and e["id"] not in avoid
               and generate.usable(e, settings["injuries"])]
    if not options:
        return known
    same = [e for e in options if not known or e["kind"] == known["kind"]] or options
    return max(same, key=lambda e: (generate.score(e, settings), -e["id"]))


def _entry(ex, sets, goal, like=None):
    compound = ex.get("kind") == "compound"
    lo, hi = ((like["target_reps_min"], like["target_reps_max"]) if like
              else vp.reps_for(ex["pattern"], goal, compound))
    return {"exercise_id": ex["id"], "name": ex["name"], "pattern": ex["pattern"], "kind": ex.get("kind"),
            "target_sets": sets, "base_sets": sets, "target_reps_min": lo, "target_reps_max": hi,
            "rest_seconds": (like or {}).get("rest_seconds") or vp.rest_for(ex["pattern"], goal, compound)}


def _spread(total, weights, lo=2, hi=analysis.MAX_BASE_SETS):
    """total Saetze im Verhaeltnis der Gewichte verteilen, je Uebung lo..hi."""
    s = sum(weights) or 1
    out = [max(lo, min(hi, round(total * w / s))) for w in weights]
    return out


def _rebalance(days, new_day, ctx, moved=()):
    """Saetze fuer die neuen Uebungen des neuen Tages und Kuerzungen an den anderen Tagen. Das Wochenvolumen je
    Muskel (mindestens das Block-Ziel) verteilt sich auf alle Einheiten mit diesem Muskel; vorhandene Uebungen werden
    nur gekuerzt. Der neue Tag bekommt nur, was die anderen wirklich abgeben – so waechst der Plan nicht ueber das
    Ziel.
    moved: Ids der Uebungen, die in den neuen Tag umziehen (ihre Saetze stehen schon fest)."""
    changes = {}                              # item_id -> neue Basis-Saetze
    fresh = [e for e in new_day["exercises"] if not e.get("moved_from")]
    for g in dict.fromkeys(analysis.group_of(e) for e in fresh):
        everything = [(d, e) for d in days for e in d["exercises"] if analysis.group_of(e) == g]
        old = [(d, e) for d, e in everything if e["id"] not in moved]
        w_all = sum(analysis.base_of(e) * analysis.per_week(d) for d, e in everything)
        w_moved = sum(e["target_sets"] for e in new_day["exercises"] if e.get("moved_from")
                      and analysis.group_of(e) == g)
        want = max(w_all, ctx["target"].get(g, 0))
        sessions_old = sum(analysis.per_week(d) for d in days
                           if any(analysis.group_of(e) == g for e in d["exercises"] if e["id"] not in moved))
        share = want / (sessions_old + 1)
        w_old = sum(analysis.base_of(e) * analysis.per_week(d) for d, e in old)
        cut = {}
        if w_old:
            factor = max(0.0, min(1.0, (want - share - w_moved) / w_old))
            for d, e in old:
                base = analysis.base_of(e)
                new = max(min(2, base), round(base * factor))
                if new < base:
                    cut[e["id"]] = new
        kept = sum(cut.get(e["id"], analysis.base_of(e)) * analysis.per_week(d) for d, e in old)
        total = want - kept - w_moved
        mine = [e for e in fresh if analysis.group_of(e) == g]
        while len(mine) > 1 and total < 2 * len(mine):         # zu wenig fuer alle: hintere Uebungen fallen weg
            new_day["exercises"].remove(mine.pop())
        if total < 2 and (w_old or w_moved):                    # der Muskel braucht hier nichts
            for e in mine:
                new_day["exercises"].remove(e)
            continue
        for e, sets in zip(mine, _spread(max(2, total), [1] * len(mine))):
            e["target_sets"] = e["base_sets"] = sets
        changes.update(cut)
    return changes


def _trim(day, ratios):
    """Neuen Tag unter die Grenzen der Spitzenwoche bringen (Einheit, Muskel je Einheit)."""
    def peak(e):
        return analysis.peak_of(e["pattern"], e["target_sets"], ratios)
    while True:
        total = sum(peak(e) for e in day["exercises"])
        by_g = Counter()
        for e in day["exercises"]:
            by_g[analysis.group_of(e)] += peak(e)
        over_g = [g for g, v in by_g.items() if v > analysis.MUSCLE_CAP]
        if total <= analysis.SESSION_CAP and not over_g:
            return
        pool = [e for e in day["exercises"] if not over_g or analysis.group_of(e) in over_g]
        big = [e for e in pool if e["target_sets"] > 2]
        if big:
            e = max(big, key=lambda x: x["target_sets"])
            e["target_sets"] = e["base_sets"] = e["target_sets"] - 1
        elif len(day["exercises"]) > 3:
            day["exercises"].remove(pool[-1] if pool else day["exercises"][-1])
        else:
            return


# ------------------------------------------------------------------ Vorschau
def _apply_preview(days, changes, renames=None, weekdays=None, removed=(), new_days=()):
    """Plan nach dem Vorschlag – nur im Speicher, um vorher/nachher zu vergleichen."""
    out = []
    for d in days:
        exs = [{**e, "target_sets": changes.get(e["id"], e["target_sets"]),
                "base_sets": changes.get(e["id"], analysis.base_of(e))}
               for e in d["exercises"] if e["id"] not in removed]
        out.append({**d, "label": (renames or {}).get(d["id"], d["label"]),
                    "weekdays": (weekdays or {}).get(d["id"], d.get("weekdays")), "exercises": exs})
    out.extend(new_days)
    return out


def _volume_diff(before, after, ctx):
    rows = []
    for g in muscles.GROUPS:
        b, a = before["week1"][g], after["week1"][g]
        if b == a and before["freq"][g] == after["freq"][g]:
            continue
        rows.append({"group": g, "label": muscles.LABEL[g], "before": b, "after": a, "target": ctx["target"][g],
                     "freq_before": before["freq"][g], "freq_after": after["freq"][g]})
    return rows


def _day_view(day, before=None, new=False):
    old = {e["id"]: e["target_sets"] for e in (before or {}).get("exercises", [])}
    return {"label": day["label"], "weekdays": day.get("weekdays") or [], "new": new,
            "exercises": [{"name": e["name"], "sets": e["target_sets"], "moved": bool(e.get("moved_from")),
                           "before": old.get(e.get("id")) if e.get("id") in old else None,
                           "reps": f"{e['target_reps_min']}–{e['target_reps_max']}"} for e in day["exercises"]]}


def _ops_new_day(day, position):
    return {"op": "add_day", "label": day["label"], "weekdays": day["weekdays"], "position": position,
            "exercises": [{k: e.get(k) for k in ("exercise_id", "target_sets", "target_reps_min", "target_reps_max",
                                                 "rest_seconds", "target_weight", "notes")}
                          for e in day["exercises"]]}


# ------------------------------------------------------------------ 1. Tag ergaenzen
def _add_day(profile, split, ctx, m, overflow):
    days = split["days"]
    n = ctx["sessions"]
    if ctx["cycle"] != 1.0 or n >= 6:
        return None
    kinds = Counter()
    for d in days:
        k = day_kind(d)
        if not k:
            return None
        kinds[k] += analysis.per_week(d)
    free = _free_days(profile, days)
    if not free:
        return None
    long_days = [load for load in m["days"] if load["peak"] > analysis.SESSION_CAP]
    crowded = [g for g in muscles.BIG if m["freq"][g] == 1 and m["peak"][g] > analysis.MUSCLE_CAP]
    available = db.parse_weekdays(profile.get("available_weekdays"))
    recommended = generate.recommended_days(ctx["experience"], ctx["goal"], len(available) or 7)
    # Nur bei echtem Bedarf: ein Tag mehr soll Volumen unterbringen, nicht den Plan aufblaehen. Gerechnet wird mit
    # den Zielen fuer eine Einheit mehr – mit wenigen Einheiten kuerzt der Block die Ziele schon wegen der Zeit.
    ctx = {**ctx, "target": analysis.week1_targets(ctx, n + 1)}
    short = [g for g in muscles.BIG if ctx["target"][g] >= 4                  # wie die Volumen-Pruefung
             and ctx["target"][g] - m["week1"][g] >= analysis.MIN_GAP]
    if not (overflow or long_days or crowded or (n < recommended and short)):
        return None

    # Muskeln, denen ein weiterer Tag hilft – mit Gewicht
    need = Counter()
    for g, gap in overflow.items():
        need[g] += gap
    for load in long_days:
        for g, sets in load["by_group"].items():
            need[g] += sets * (load["peak"] - analysis.SESSION_CAP) / load["peak"]
    for g in crowded:
        need[g] += m["peak"][g] - analysis.MUSCLE_CAP
    if n < recommended:
        for g in short:
            need[g] += ctx["target"][g] - m["week1"][g]

    weekdays_now = sorted({wd for d in days for wd in d.get("weekdays") or []})
    hyp = sorted(weekdays_now + free[:1])
    if len(hyp) != n + 1:            # z. B. zwei Tage am selben Wochentag: dann die Standardtage bewerten
        hyp = []
    ranked = {o["key"]: o for o in generate.split_options(profile, {"days": n + 1, "weekdays": hyp})}
    candidates = []
    for key, opt in ranked.items():
        seq = splits.find(key)
        if seq.get("rotate"):
            continue
        seq_kinds = [KIND_OF.get(label) for label in seq["sequence"]]
        if None in seq_kinds:
            continue
        extra = Counter(seq_kinds) - kinds
        if sum(extra.values()) != 1 or kinds - Counter(seq_kinds):
            continue
        kind = next(iter(extra))
        seen, template = Counter(), None
        for label, k in zip(seq["sequence"], seq_kinds):
            seen[k] += 1
            if k == kind and seen[k] > kinds[k]:
                template = label
                break
        relief = sum(v for g, v in need.items() if g in _template_groups(template))
        if need and not relief:
            continue                 # der Tag haelfe den Muskeln, die Platz brauchen, gar nicht
        if long_days and not any(set(load["by_group"]) & _template_groups(template) for load in long_days):
            continue                 # eine zu lange Einheit muss der neue Tag entlasten koennen
        candidates.append((opt["score"] + min(4.0, relief / 4), kind, template))
    seen_kinds = set()
    for _, kind, template in sorted(candidates, key=lambda c: -c[0]):
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        proposal = _propose(profile, split, ctx, m, overflow, kind, template, need, long_days, free)
        if proposal:
            return proposal
    return None


def _propose(profile, split, ctx, m, overflow, kind, template, need, long_days, free):
    """Den neuen Tag fuer einen Tagestyp bauen – None, wenn dabei kein sinnvoller Tag herauskommt."""
    days = split["days"]
    n = ctx["sessions"]
    available = db.parse_weekdays(profile.get("available_weekdays"))
    recommended = generate.recommended_days(ctx["experience"], ctx["goal"], len(available) or 7)
    short = [g for g in muscles.BIG if ctx["target"][g] >= 4                  # wie die Volumen-Pruefung
             and ctx["target"][g] - m["week1"][g] >= analysis.MIN_GAP]
    settings = generate.settings_from(profile)
    settings["injuries"] = injuries_from(profile.get("limitations"))
    sibling = max((d for d in days if day_kind(d) == kind), key=lambda d: len(d["exercises"]), default=None)
    used = {e["exercise_id"] for d in days for e in d["exercises"]}
    picked = []
    if sibling:
        # gleiche Muster wie der vorhandene Tag, aber andere Uebungen: Abwechslung, gleiche Logik
        for e in sibling["exercises"]:
            if not e.get("pattern"):
                continue
            avoid = used | {p["exercise_id"] for p in picked}
            ex = _choose(e["pattern"], avoid, settings, e["exercise_id"], variant=True)
            if ex:
                picked.append(_entry(ex, analysis.base_of(e), ctx["goal"], like=e))
        mirrored = {analysis.group_of(e) for e in picked}
        for g in need:                  # Muskel mit Bedarf, den der vorhandene Tag nicht trainiert (z. B. Po am UK)
            if g in mirrored or g not in _template_groups(template):
                continue
            for p in generate.GROUP_PATTERNS.get(g, ())[:1]:
                ex = _choose(p, used | {x["exercise_id"] for x in picked}, settings)
                if ex:
                    picked.append(_entry(ex, 3, ctx["goal"]))
    else:
        # Aus zu langen Einheiten ziehen Uebungen um: hat ein Muskel dort mehrere, wandert eine in den neuen Tag
        # (gleiches Wochenvolumen, der alte Tag wird kuerzer). Neue Uebungen gibt es nur, wo Volumen fehlt.
        groups_new = _template_groups(template)
        for load in long_days:
            d = load["day"]
            by_g = {}
            for e in d["exercises"]:
                by_g.setdefault(analysis.group_of(e), []).append(e)
            for g, exs in by_g.items():
                if g not in groups_new or len(exs) < 2:
                    continue
                e = min(reversed(exs), key=analysis.base_of)
                sets = min(analysis.MAX_BASE_SETS, analysis.base_of(e) * analysis.per_week(d))
                picked.append({**_entry(e | {"id": e["exercise_id"]}, sets, ctx["goal"], like=e),
                               "exercise_id": e["exercise_id"], "name": e["name"], "moved_from": e["id"],
                               "target_weight": e.get("target_weight"), "notes": e.get("notes")})
        covered = {analysis.group_of(e) for e in picked}
        mine = {}
        for d in days:
            for e in d["exercises"]:
                if e.get("pattern") and analysis.base_of(e) > analysis.base_of(mine.get(e["pattern"], {})):
                    mine[e["pattern"]] = e
        patterns = []
        for p in splits.TEMPLATES[template]:
            g = muscles.PATTERN_GROUP[p]
            short = ctx["target"][g] - m["week1"][g]
            missing = ctx["target"][g] and not m["week1"][g]
            if p not in patterns and g not in covered and (g in need or short >= 2 or missing):
                patterns.append(p)
        for g in overflow:          # Muskeln ohne Platz, die die Vorlage nicht kennt (z. B. Waden am Ganzkoerper-Tag)
            if g not in covered and not any(muscles.PATTERN_GROUP[p] == g for p in patterns):
                patterns.extend(generate.GROUP_PATTERNS.get(g, ())[:1])
        for p in patterns[:max(0, MAX_NEW_EXERCISES - len(picked))]:
            known = mine.get(p)
            ex = _choose(p, used | {x["exercise_id"] for x in picked}, settings, known and known["exercise_id"])
            if ex:
                picked.append(_entry(ex, 3, ctx["goal"], like=known))
    if not picked:
        return None

    new_day = {"id": None, "label": "", "weekdays": [], "position": len(days), "exercises": picked}
    moved = {e["moved_from"] for e in picked if e.get("moved_from")}
    changes = _rebalance(days, new_day, ctx, moved)
    _trim(new_day, ctx["ratios"])
    if len(new_day["exercises"]) < 3:            # eine Einheit mit ein, zwei Uebungen lohnt den Weg nicht
        return None
    if need and not any(analysis.group_of(e) in need for e in new_day["exercises"]):
        return None
    moved = {e["moved_from"] for e in new_day["exercises"] if e.get("moved_from")}
    # Name und Typ nach dem, was wirklich drin ist (aus einem GK-Vorschlag kann ein reiner Oberkoerper-Tag werden)
    kind = day_kind(new_day) or kind
    if not sibling:
        sibling = max((d for d in days if day_kind(d) == kind), key=lambda d: len(d["exercises"]), default=None)
    places = _place(free, days, {analysis.group_of(e) for e in new_day["exercises"]})
    rename, label = _labels(sibling["label"] if sibling else None, kind, [d["label"] for d in days])
    new_day.update(label=label, weekdays=[places[0]["weekday"]])

    renames = {sibling["id"]: rename} if rename else {}
    after_days = _apply_preview(days, changes, renames, removed=moved, new_days=[new_day])
    after = analysis.measure(after_days, ctx["ratios"], ctx["cycle"])
    sequence = " · ".join(label for _, label in sorted((wd, d["label"]) for d in after_days
                                                       for wd in (d.get("weekdays") or [7])))

    reasons = []
    for load in long_days:
        new_peak = next(x["peak"] for x in after["days"] if x["day"]["id"] == load["day"]["id"])
        reasons.append(f"„{load['day']['label']}“ wächst im Block auf {load['peak']} Sätze "
                       f"(≈ {vp.session_minutes(load['peak'], ctx['goal'])} Minuten) – danach {new_peak}.")
    more = [muscles.LABEL[g] for g in muscles.GROUPS
            if after["freq"][g] > m["freq"][g] and m["freq"][g] and ctx["target"][g]]
    if more:
        reasons.append(f"{', '.join(more)} verteilen sich auf mehr Einheiten – weniger Sätze am Stück, "
                       "jeder Satz bringt mehr.")
    if overflow:
        reasons.append(f"Für {', '.join(muscles.LABEL[g] for g in overflow)} war in den Einheiten kein Platz mehr.")
    if n < recommended and short and not reasons:
        reasons.append(f"{', '.join(muscles.LABEL[g] for g in short)} bekommen zu wenig Volumen; mit "
                       f"{n + 1} statt {n} Einheiten passt es hinein, ohne dass eine Einheit lang wird.")
    focus_more = [muscles.LABEL[g] for g in muscles.GROUPS
                  if g in ctx["focus_groups"] and after["freq"][g] > m["freq"][g]]
    if focus_more and not long_days:
        reasons.append(f"Dein Fokus ({', '.join(focus_more)}) kommt öfter dran.")

    before_by_id = {d["id"]: d for d in days}
    before_of = lambda d: before_by_id[d["id"]]      # noqa: E731
    ops = []
    if rename:
        ops.append({"op": "rename_day", "day_id": sibling["id"], "label": rename})
    ops.extend({"op": "sets", "item_id": i, "target_sets": s} for i, s in changes.items())
    ops.extend({"op": "remove", "item_id": i} for i in sorted(moved))
    note = "Deine Gewichte und der Verlauf hängen an der Übung und bleiben erhalten."
    if moved:
        note = (f"{len(moved)} Übungen ziehen mit ihren Sätzen in den neuen Tag um. " + note)
    ops.append(_ops_new_day(new_day, len(days)))
    changed_days = [d for d in after_days if d.get("id") and (d["id"] in renames or any(
        e["id"] in changes for e in d["exercises"]) or len(d["exercises"]) != len(before_of(d)["exercises"]))]
    return {
        "type": "add_day",
        "title": f"{'Vierten' if n == 3 else 'Dritten' if n == 2 else 'Weiteren'} Trainingstag ergänzen: {sequence}",
        "text": (f"Ein {KIND_NAME[kind]}-Tag „{label}“ am {splits.WD[new_day['weekdays'][0]]}"
                 + (f", dein „{sibling['label']}“ heißt dann „{rename}“" if rename else "")
                 + ". Das Volumen verteilt sich neu, statt sich zu verdoppeln."),
        "reasons": reasons[:3],
        "days": [_day_view(new_day, new=True)] + [_day_view(d, before_by_id[d["id"]]) for d in changed_days],
        "volume": _volume_diff(m, after, ctx),
        "weekday_options": places,
        "note": note,
        "action": {"type": "restructure", "split_id": split["id"], "fingerprint": analysis.fingerprint(split),
                   "ops": ops},
    }


# ------------------------------------------------------------------ 2. A/B-Varianten
def _split_ab(profile, split, ctx, m):
    candidates = [d for d in split["days"] if analysis.per_week(d) >= 2 and len(d["exercises"]) >= 6
                  and all(e.get("pattern") for e in d["exercises"])]
    for day in sorted(candidates, key=lambda d: -len(d["exercises"])):
        by_group = {}
        for e in day["exercises"]:
            by_group.setdefault(analysis.group_of(e), []).append(e)
        side = {}                         # item_id -> "A" | "B" | "AB"
        sets_a, sets_b = {}, {}
        split_groups = []
        for g, items in by_group.items():
            # Uebungen mit demselben Muster wechseln sich ab (Bankdruecken/Schraegbank), die einzige ihres Musters
            # bleibt in beiden Varianten (Butterfly, Rudern). Das Volumen des Muskels je Einheit bleibt gleich.
            by_pattern = {}
            for e in items:
                by_pattern.setdefault(e["pattern"], []).append(e)
            a = [e for e in items if len(by_pattern[e["pattern"]]) == 1 or by_pattern[e["pattern"]].index(e) % 2 == 0]
            b = [e for e in items if len(by_pattern[e["pattern"]]) == 1 or by_pattern[e["pattern"]].index(e) % 2 == 1]
            if len(a) == len(items):
                for e in items:
                    side[e["id"]] = "AB"
                continue
            per_session = sum(analysis.base_of(e) for e in items)
            new_a = _spread(per_session, [analysis.base_of(e) for e in a])
            new_b = _spread(per_session, [analysis.base_of(e) for e in b])
            if max(new_a + new_b) > analysis.MAX_BASE_SETS or abs(sum(new_a) - per_session) > 1 \
                    or abs(sum(new_b) - per_session) > 1:
                for e in items:
                    side[e["id"]] = "AB"
                continue
            split_groups.append(g)
            for e, sets in zip(a, new_a):
                sets_a[e["id"]] = sets
            for e, sets in zip(b, new_b):
                sets_b[e["id"]] = sets
            for e in items:
                side[e["id"]] = "AB" if (e in a and e in b) else "A" if e in a else "B"
        if len(split_groups) < 2:
            continue

        wds = sorted(day["weekdays"])
        wd_a, wd_b = wds[0::2], wds[1::2]
        rename, label_b = _labels(day["label"], day_kind(day) or "ok", [d["label"] for d in split["days"]])
        label_a = rename or day["label"]
        a_items = [{**e, "target_sets": sets_a.get(e["id"], e["target_sets"])} for e in day["exercises"]
                   if side[e["id"]] in ("A", "AB")]
        b_items = [{**e, "id": None, "target_sets": sets_b.get(e["id"], e["target_sets"]),
                    "base_sets": sets_b.get(e["id"], analysis.base_of(e))}
                   for e in day["exercises"] if side[e["id"]] in ("B", "AB")]
        day_b = {"id": None, "label": label_b, "weekdays": wd_b, "position": day["position"] + 1,
                 "exercises": b_items}
        removed = {e["id"] for e in day["exercises"] if side[e["id"]] == "B"}
        after_days = _apply_preview(split["days"], sets_a, {day["id"]: label_a}, {day["id"]: wd_a}, removed, [day_b])
        after = analysis.measure(after_days, ctx["ratios"], ctx["cycle"])
        day_a = next(d for d in after_days if d.get("id") == day["id"])

        ops = []
        if rename:
            ops.append({"op": "rename_day", "day_id": day["id"], "label": rename})
        ops.append({"op": "set_weekdays", "day_id": day["id"], "weekdays": wd_a})
        ops.extend({"op": "remove", "item_id": i} for i in removed)
        ops.extend({"op": "sets", "item_id": i, "target_sets": s} for i, s in sets_a.items()
                   if s != analysis.base_of(next(e for e in day["exercises"] if e["id"] == i)))
        ops.append(_ops_new_day(day_b, day["position"] + 1))
        names = ", ".join(muscles.LABEL[g] for g in split_groups)
        return {
            "type": "split_ab",
            "title": f"„{day['label']}“ in zwei Varianten teilen: {label_a} · {label_b}",
            "text": (f"„{day['label']}“ läuft {' und '.join(WD[w] for w in wds)} mit denselben "
                     f"{len(day['exercises'])} Übungen. Als {label_a} ({', '.join(WD[w] for w in wd_a)}) und "
                     f"{label_b} ({', '.join(WD[w] for w in wd_b)}) hat jede Einheit "
                     f"{len(a_items)} bzw. {len(b_items)} Übungen bei gleichem Volumen."),
            "reasons": [f"Bei {names} wechseln sich die Übungen ab – unterschiedliche Winkel und Geräte "
                        "treffen den Muskel vollständiger.",
                        "Weniger Gerätewechsel und Aufwärmsätze pro Einheit, dafür je Übung mehr Sätze "
                        "am Stück."],
            "days": [_day_view(day_a, day), _day_view(day_b, new=True)],
            "volume": _volume_diff(m, after, ctx),
            "weekday_options": [],
            "note": "Deine Gewichte und der Verlauf hängen an der Übung und bleiben erhalten.",
            "action": {"type": "restructure", "split_id": split["id"], "fingerprint": analysis.fingerprint(split),
                       "ops": ops},
        }
    return None


def suggest(profile, split, ctx, m, overflow):
    """Hoechstens ein Struktur-Vorschlag: erst ein zusaetzlicher Tag (loest Platzprobleme), sonst A/B-Varianten."""
    return _add_day(profile, split, ctx, m, overflow) or _split_ab(profile, split, ctx, m)


# ------------------------------------------------------------------ Umsetzen
def _int(value, what):
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"„{what}“ ist keine gültige Zahl.") from e


def apply(profile_id, action):
    """Struktur-Vorschlag in einer Transaktion umsetzen – erst alles pruefen, dann schreiben."""
    split = db.split_detail(_int(action.get("split_id"), "split_id"))
    if not split or split["profile_id"] != profile_id:
        raise ValueError("Dieser Plan gehört zu einem anderen Profil.")
    if action.get("fingerprint") != analysis.fingerprint(split):
        raise ValueError("Der Plan hat sich inzwischen geändert – bitte „Plan verbessern“ neu öffnen.")
    ops = action.get("ops")
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS:
        raise ValueError("Dieser Vorschlag ist unvollständig.")
    days = {d["id"]: d for d in split["days"]}
    items = {e["id"]: d["id"] for d in split["days"] for e in d["exercises"]}
    weekdays = {d["id"]: list(d.get("weekdays") or []) for d in split["days"]}
    new_weekdays = []
    clean = []
    for op in ops:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind in ("rename_day", "set_weekdays"):
            day_id = _int(op.get("day_id"), "day_id")
            if day_id not in days:
                raise ValueError("Dieser Trainingstag gehört nicht zu diesem Plan.")
            if kind == "rename_day":
                label = str(op.get("label") or "").strip()[:40]
                if not label:
                    raise ValueError("Der Tag braucht einen Namen.")
                clean.append((kind, day_id, label))
            else:
                wds = db.parse_weekdays(op.get("weekdays"))
                weekdays[day_id] = wds
                clean.append((kind, day_id, db.format_weekdays(wds)))
        elif kind in ("sets", "remove"):
            item_id = _int(op.get("item_id"), "item_id")
            if item_id not in items:
                raise ValueError("Diese Übung steht nicht mehr im Plan.")
            sets = _int(op.get("target_sets"), "target_sets") if kind == "sets" else None
            if sets is not None and not 1 <= sets <= 10:
                raise ValueError("Unpassende Satzzahl.")
            clean.append((kind, item_id, sets))
        elif kind == "add_day":
            label = str(op.get("label") or "").strip()[:40]
            exercises = op.get("exercises")
            if not label or not isinstance(exercises, list) or not 1 <= len(exercises) <= 12:
                raise ValueError("Der neue Tag ist unvollständig.")
            rows = []
            for e in exercises:
                ex_id = _int(e.get("exercise_id"), "exercise_id")
                if not db.exists("exercises", ex_id):
                    raise ValueError("Diese Übung gibt es nicht (mehr).")
                sets = _int(e.get("target_sets"), "target_sets")
                lo = _int(e.get("target_reps_min"), "target_reps_min")
                hi = _int(e.get("target_reps_max"), "target_reps_max")
                if not (1 <= sets <= 10 and 1 <= lo <= hi <= 50):
                    raise ValueError("Sätze oder Wiederholungen außerhalb des Rahmens.")
                rest = e.get("rest_seconds")
                rows.append((ex_id, sets, lo, hi, _int(rest, "rest_seconds") if rest is not None else None,
                             e.get("target_weight"), e.get("notes")))
            wds = db.parse_weekdays(op.get("weekdays"))
            new_weekdays.extend(wds)
            position = _int(op.get("position", len(days)), "position")
            clean.append((kind, (label, db.format_weekdays(wds), position), rows))
        else:
            raise ValueError("Diesen Vorschlag kann die App nicht automatisch umsetzen.")
    taken = [wd for wds in weekdays.values() for wd in wds] + new_weekdays
    if len(taken) - len(set(taken)) > _double_booked(split):     # alte Doppelbelegungen blockieren den Umbau nicht
        raise ValueError("Dieser Wochentag ist schon belegt – bitte einen anderen wählen.")

    with db.connect() as c:
        for kind, key, value in clean:
            if kind == "rename_day":
                c.execute("UPDATE split_days SET label=? WHERE id=?", (value, key))
            elif kind == "set_weekdays":
                c.execute("UPDATE split_days SET weekdays=? WHERE id=?", (value, key))
            elif kind == "sets":
                c.execute("UPDATE split_exercises SET target_sets=?, base_sets=? WHERE id=?", (value, value, key))
            elif kind == "remove":
                c.execute("DELETE FROM split_exercises WHERE id=?", (key,))
            else:
                label, wds, position = key
                c.execute("UPDATE split_days SET position = position + 1 WHERE split_id=? AND position >= ?",
                          (split["id"], position))
                day_id = c.execute("INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                                   (split["id"], position, label, wds)).lastrowid
                for pos, (ex_id, sets, lo, hi, rest, weight, notes) in enumerate(value):
                    c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, target_sets,"
                              " base_sets, target_reps_min, target_reps_max, rest_seconds, target_weight, notes)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (day_id, ex_id, pos, sets, sets, lo, hi, rest, weight, notes))
        if split.get("active"):
            # Trainingstage im Profil nachziehen – Aktivitaet und Ernaehrung lesen sie
            wds = sorted(set(taken))
            c.execute("UPDATE profiles SET train_weekdays=?, days_per_week=? WHERE id=?",
                      (db.format_weekdays(wds), max(2, min(6, len(wds))) if wds else None, profile_id))
    return {"ok": True}


def _double_booked(split):
    """Schon vorhandene Doppelbelegungen (z. B. zwei Tage am Freitag)."""
    wds = [wd for d in split["days"] for wd in (d.get("weekdays") or [])]
    return len(wds) - len(set(wds))
