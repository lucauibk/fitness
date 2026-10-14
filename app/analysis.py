"""Plan verbessern: schaut sich einen bestehenden Plan an (auch einen selbst gebauten),
rechnet Wochensaetze und Haeufigkeit je Muskelgruppe aus und macht daraus konkrete,
anwendbare Vorschlaege – Saetze anpassen, Uebung ergaenzen oder streichen, Struktur aendern.

Gerechnet wird wie im Block: Der Plan wird mit Woche 1 der Block-Ziele verglichen
(planner/volume_plan.block_targets). Die Satzzahl bleibt im Block konstant (blocks.sets_for), die
"Spitzenwoche" fuer Einheitslaenge und Erholungsgrenze (MRV) ist also der Plan selbst.
Struktur-Vorschlaege (zusaetzlicher Tag, A/B-Varianten) kommen aus plan_structure.py.
"""
import hashlib
import json
from datetime import date

from . import db
from .planner import blocks, generate, muscles, validate
from .planner import volume_plan as vp
from .planner.profile_model import GOALS

# Welche Muster passen zu welchem Tagestyp (fuer "wo ergaenzen?")
UPPER = {"brust", "ruecken", "schultern", "bizeps", "trizeps"}
LOWER = {"quads", "beinbeuger", "po", "waden"}
MIN_GAP = 3      # ab so vielen fehlenden Saetzen gibt es einen Vorschlag
MAX_EXTRA = 4    # ab so vielen Saetzen zu viel
MAX_BASE_SETS = 5                                    # mehr Saetze je Uebung schlaegt die App nicht vor
SESSION_CAP = vp.MAX_SETS_PER_SESSION                # Saetze je Einheit in der Spitzenwoche
MUSCLE_CAP = generate.MAX_SESSION_SETS_PER_MUSCLE    # Saetze je Muskel und Einheit in der Spitzenwoche
# Groesse einer Einheit: 6–8 Uebungen sind ueblich; mehr kostet vor allem Umbau- und Aufwaermzeit. Ab FULL_DAY
# schlaegt die App dort keine weitere Uebung vor, ab TOO_MANY_EXERCISES weist sie darauf hin.
FULL_DAY = 8
TOO_MANY_EXERCISES = 10


def _focus_names(focus_groups):
    seen = [muscles.LABEL[g] for g in muscles.GROUPS if g in focus_groups]
    return " und ".join([", ".join(seen[:-1]), seen[-1]]) if len(seen) > 1 else (seen[0] if seen else "")


def group_of(ex):
    """Muskelgruppe nur ueber das Bewegungsmuster – wie Plan-Check und Block. Uebungen ohne Muster zaehlen nicht mit
    (dafuer gibt es den Vorschlag, das Muster einzutragen)."""
    return muscles.PATTERN_GROUP.get(ex.get("pattern"))


def per_week(day):
    return len(day.get("weekdays") or []) or 1


def base_of(item):
    return item.get("base_sets") or item.get("target_sets") or 0


# ------------------------------------------------------------------ Rahmen
def context(profile, split):
    """Alles, was die Rechnung braucht: Ziel, Fokus, Steigerung im Block, Ziele fuer Woche 1 und Spitzenwoche."""
    goal = profile.get("goal") or "aufbau"
    experience = profile.get("experience") or "einsteiger"
    focus = profile.get("focus") or []
    focus_groups = {t for f in focus for t in generate.FOCUS_GROUPS.get(f, [])}
    age = (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None
    block = blocks.for_split(split["id"]) if split.get("id") else None
    params = blocks._params(block)
    # Nicht params["ratios"]: alte Bloecke tragen dort noch die frueher steigende Satzzahl
    ratios = blocks.ratios_for(goal, experience, focus, age)
    cycle = params.get("cycle") or 1.0
    sessions = max(1, round(sum(per_week(d) for d in split["days"]) / cycle))
    weeks, note = _weeks(experience, goal, focus_groups, age, sessions)
    return {"goal": goal, "experience": experience, "focus": focus, "focus_groups": focus_groups, "age": age,
            "ratios": ratios, "cycle": cycle, "sessions": sessions, "notes": [note] if note else [],
            "target": {g: v[0] for g, v in weeks.items()},
            "peak_target": {g: max(v[:-1]) for g, v in weeks.items()}}


def _weeks(experience, goal, focus_groups, age, sessions):
    total, _ = vp.capacity(max(2, sessions), None, goal)      # die Dauer wird nicht abgefragt: nur das Volumen zaehlt
    return vp.fit_to_capacity(vp.block_targets(experience, goal, focus_groups, age), total)


def week1_targets(ctx, sessions):
    """Ziele fuer Woche 1 bei einer anderen Zahl an Einheiten (mehr Einheiten = mehr Platz fuer Volumen)."""
    weeks, _ = _weeks(ctx["experience"], ctx["goal"], ctx["focus_groups"], ctx["age"], sessions)
    return {g: v[0] for g, v in weeks.items()}


def peak_of(pattern, base, ratios, peak_cap=None):
    """Saetze einer Uebung in der staerksten Blockwoche – dieselbe Rechnung wie blocks.sets_for."""
    if base < 2:
        return base
    ratio = max((ratios.get(muscles.PATTERN_GROUP.get(pattern)) or [1.0, 1.0])[:-1] or [1.0])
    cap = blocks.MAX_EXERCISE_SETS
    return max(2, min(cap, round(base * ratio), base + 3, max(peak_cap or cap, base)))


def item_peak(item, ratios):
    return peak_of(item.get("pattern"), base_of(item), ratios, item.get("peak_sets"))


def measure(days, ratios, cycle=1.0):
    """Wochensaetze (Woche 1 und Spitzenwoche), Haeufigkeit je Gruppe und die Last je Tag."""
    week1 = {g: 0 for g in muscles.GROUPS}
    peak = {g: 0 for g in muscles.GROUPS}
    freq = {g: 0 for g in muscles.GROUPS}
    per_day = []
    for day in days:
        times = per_week(day)
        groups, total, by_group = set(), 0, {}
        for ex in day["exercises"]:
            g = group_of(ex)
            p = item_peak(ex, ratios) if ex.get("pattern") else base_of(ex)
            total += p
            if not g:
                continue
            week1[g] += base_of(ex) * times
            peak[g] += p * times
            by_group[g] = by_group.get(g, 0) + p
            groups.add(g)
        for g in groups:
            freq[g] += times
        per_day.append({"day": day, "peak": total, "by_group": by_group})
    norm = lambda d: {g: round(v / (cycle or 1.0)) for g, v in d.items()}      # noqa: E731
    return {"week1": norm(week1), "peak": norm(peak), "freq": freq, "days": per_day}


def room(day_load, group, extra_peak):
    """Passt extra_peak Saetze (Spitzenwoche) dieser Gruppe noch in die Einheit?"""
    return (day_load["peak"] + extra_peak <= SESSION_CAP
            and day_load["by_group"].get(group, 0) + extra_peak <= MUSCLE_CAP)


def fingerprint(split):
    """Kurzer Abdruck des Plans: ein Struktur-Vorschlag gilt nur fuer genau diesen Stand."""
    shape = [(d["id"], d["label"], sorted(d.get("weekdays") or []),
              [(e["id"], e["exercise_id"], e.get("target_sets")) for e in d["exercises"]]) for d in split["days"]]
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ Uebungen waehlen
def pick_exercise(group, used_ids, profile, prefer_patterns=()):
    """Passende Uebung aus dem Katalog: beruecksichtigt Verletzungen, Geraet und Erfahrung."""
    settings = generate.settings_from(profile)
    best = None
    for ex in generate.catalog():
        if ex["id"] in used_ids or not generate.usable(ex, settings["injuries"]):
            continue
        if muscles.PATTERN_GROUP.get(ex["pattern"]) != group:
            continue
        key = (0 if (not prefer_patterns or ex["pattern"] in prefer_patterns) else 1,
               -generate.score(ex, settings), ex["id"])
        if best is None or key < best[0]:
            best = (key, ex)
    return best[1] if best else None


def _day_for(group, loads, sets_peak):
    """Passender Tag fuer eine neue Uebung: zuerst ein Tag, der die Gruppe schon trainiert, sonst der Tag mit den
    wenigsten Saetzen der passenden Koerperhaelfte – nur Tage, in die sie noch passt."""
    scored = []
    for load in loads:
        groups = set(load["by_group"])
        half_ok = (group in LOWER and groups & LOWER) or (group in UPPER and groups & UPPER) or group == "rumpf"
        if not room(load, group, sets_peak) or len(load["day"]["exercises"]) >= FULL_DAY:
            continue
        rank = 0 if group in groups else 1 if half_ok else 2
        scored.append((rank, load["peak"], load["day"]["position"], load["day"]))
    scored.sort(key=lambda x: x[:3])
    return scored[0][3] if scored else None


def _add_suggestion(group, missing, ctx, loads, profile, used_ids, prefer_patterns=(), why=""):
    """Neue Uebung vorschlagen. None, wenn keine passt oder kein Tag mehr Platz hat (dann hilft nur mehr Struktur)."""
    ex = pick_exercise(group, used_ids, profile, prefer_patterns)
    if not ex:
        return None
    sets = max(2, min(4, missing))
    day = _day_for(group, loads, peak_of(ex["pattern"], sets, ctx["ratios"]))
    if not day:
        return None
    compound = ex["kind"] == "compound"
    lo, hi = vp.reps_for(ex["pattern"], ctx["goal"], compound)
    used_ids.add(ex["id"])
    return {
        "level": "warn" if missing >= 4 else "info",
        "text": f"{ex['name']} mit {sets} Sätzen zu „{day['label']}“ hinzufügen",
        "why": why or f"{muscles.LABEL[group]}: {missing} Sätze pro Woche fehlen zum Richtwert.",
        "action": {"type": "add", "day_id": day["id"], "exercise_id": ex["id"],
                   "target_sets": sets, "target_reps_min": lo, "target_reps_max": hi,
                   "rest_seconds": vp.rest_for(ex["pattern"], ctx["goal"], compound)},
    }


def _sets_text(item, new, ratios):
    peak = peak_of(item.get("pattern"), new, ratios, item.get("peak_sets"))
    return f"{new} Sätze" + (f", im Block bis {peak}" if peak > new else "")


# ------------------------------------------------------------------ Analyse
def analyze(profile_id, split_id=None):
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    split = db.split_detail(split_id) if split_id else db.active_split(profile_id)
    if not split or not split["days"]:
        return {"split": None, "suggestions": [], "volume": [], "sessions": 0}

    ctx = context(profile, split)
    m = measure(split["days"], ctx["ratios"], ctx["cycle"])
    actual, peak, freq, loads = m["week1"], m["peak"], m["freq"], m["days"]
    load_of = {load["day"]["id"]: load for load in loads}
    target, focus_groups, goal, ratios = ctx["target"], ctx["focus_groups"], ctx["goal"], ctx["ratios"]

    items = [(day, ex) for day in split["days"] for ex in day["exercises"]]
    used_ids = {ex["exercise_id"] for _, ex in items}
    suggestions = []
    overflow = {}             # Gruppe -> fehlende Saetze, die in keine Einheit mehr passen

    # 1. Uebungen ohne Bewegungsmuster – sie zaehlen sonst nicht mit
    for day, ex in items:
        if not ex.get("pattern"):
            suggestions.append({
                "level": "info",
                "text": f"Bewegungsmuster für „{ex['name']}“ eintragen",
                "why": "Ohne Muster kann die App diese Übung beim Volumen nur grob zuordnen.",
                "action": {"type": "open_exercise", "exercise_id": ex["exercise_id"]},
            })

    # 2. Zwei Trainingstage auf demselben Wochentag
    seen = {}
    for day in split["days"]:
        for wd in day.get("weekdays") or []:
            if wd in seen:
                suggestions.append({
                    "level": "warn",
                    "text": f"„{seen[wd]['label']}“ und „{day['label']}“ liegen beide am {WEEKDAYS_LONG[wd]}",
                    "why": "Zwei Einheiten am selben Tag zählen als eine lange – einen der Tage verschieben.",
                    "action": {"type": "open_day", "day_id": day["id"]},
                })
            seen.setdefault(wd, day)

    # 3. Volumen je Muskelgruppe (Woche 1 gegen Woche 1 der Block-Ziele)
    for group, want in sorted(target.items(), key=lambda kv: -kv[1]):
        have = actual.get(group, 0)
        gap = want - have
        mrv = muscles.landmark(group, "mrv")
        if want >= 4 and gap >= MIN_GAP:
            # zuerst Saetze bei einer vorhandenen Uebung erhoehen – nur, wo die Einheit noch Platz hat
            candidates = []
            for day, ex in items:
                if group_of(ex) != group or not ex.get("pattern") or base_of(ex) >= MAX_BASE_SETS:
                    continue
                add = min(MAX_BASE_SETS - base_of(ex), max(1, round(gap / per_week(day))))
                extra = peak_of(ex["pattern"], base_of(ex) + add, ratios, ex.get("peak_sets")) - item_peak(ex, ratios)
                if room(load_of[day["id"]], group, extra):
                    candidates.append((base_of(ex), day, ex, add))
            if candidates:
                _, day, ex, add = min(candidates, key=lambda t: (t[0], t[1]["position"]))
                new = base_of(ex) + add
                suggestions.append({
                    "level": "warn" if gap >= 5 else "info",
                    "text": f"„{ex['name']}“ auf {_sets_text(ex, new, ratios)} erhöhen ({day['label']})",
                    "why": f"{muscles.LABEL[group]}: {have} statt {want} Sätze pro Woche"
                           + (" – das ist deine Fokus-Muskelgruppe." if group in focus_groups else "."),
                    "action": {"type": "sets", "item_id": ex["id"], "target_sets": new},
                })
                continue
            s = _add_suggestion(group, gap, ctx, loads, profile, used_ids)
            if s:
                suggestions.append(s)
            else:
                overflow[group] = gap
            continue
        # zu viel: ueber der Erholungsgrenze (Spitzenwoche) oder – ausser beim Fokus – deutlich ueber dem Ziel
        over_mrv = peak.get(group, 0) > mrv
        over_target = want and have - want >= MAX_EXTRA and group not in focus_groups
        if not (over_mrv or over_target):
            continue
        group_items = [(day, ex) for day, ex in items if group_of(ex) == group]
        isolation = [t for t in group_items if t[1].get("kind") != "compound"]
        day, ex = max(isolation or group_items, key=lambda t: (base_of(t[1]), -t[0]["position"]))
        if over_mrv:
            why = (f"{muscles.LABEL[group]}: bis Blockende {peak[group]} Sätze pro Woche – mehr, als sich erholen "
                   f"lässt (≈ {mrv}).")
            over = max(1, have - round(have * mrv / peak[group]))
        else:
            over = have - want
            why = (f"{muscles.LABEL[group]}: {have} Sätze pro Woche. "
                   + (f"Weil {_focus_names(focus_groups)} im Fokus stehen, reicht hier Erhaltungsvolumen "
                      f"({want} Sätze) – die Zeit steckst du besser in den Fokus."
                      if focus_groups else f"Mehr als {want} Sätze bringen kaum zusätzlichen Aufbau."))
        if base_of(ex) > 2:
            new = max(2, base_of(ex) - max(1, round(over / per_week(day))))
            suggestions.append({
                "level": "warn" if over_mrv else "info",
                "text": f"„{ex['name']}“ auf {_sets_text(ex, new, ratios)} reduzieren ({day['label']})",
                "why": why, "action": {"type": "sets", "item_id": ex["id"], "target_sets": new},
            })
        elif len(group_items) > 1:
            suggestions.append({
                "level": "warn" if over_mrv else "info",
                "text": f"„{ex['name']}“ aus „{day['label']}“ streichen",
                "why": why, "action": {"type": "remove", "item_id": ex["id"]},
            })

    # 4. Klassische Luecken
    patterns = {ex.get("pattern") for _, ex in items}
    if actual["quads"] and not (patterns & {"hinge", "leg_iso_back"}):
        s = _add_suggestion("beinbeuger", 6, ctx, loads, profile, used_ids, ("hinge", "leg_iso_back"),
                            "Für die Beinrückseite fehlt eine gezielte Übung – das erhöht "
                            "auf Dauer das Verletzungsrisiko.")
        if s:
            suggestions.append(s)
    if "rear_delt" not in patterns and actual["brust"] >= 6:
        s = _add_suggestion("schultern", 4, ctx, loads, profile, used_ids, ("rear_delt",),
                            "Die hintere Schulter fehlt – sie hält bei viel Drücken das "
                            "Gleichgewicht in der Schulter.")
        if s:
            suggestions.append(s)
    if "calves" not in patterns and target["waden"]:
        s = _add_suggestion("waden", target["waden"], ctx, loads, profile, used_ids, ("calves",),
                            "Waden kommen im Plan gar nicht vor.")
        if s:
            suggestions.append(s)
    if "core" not in patterns and target["rumpf"]:
        s = _add_suggestion("rumpf", target["rumpf"], ctx, loads, profile, used_ids, ("core",),
                            "Für den Rumpf gibt es keine eigene Übung.")
        if s:
            suggestions.append(s)

    # Druecken und Ziehen – dieselbe Zaehlung wie der Plan-Check
    push, pull = validate.push_pull([(ex.get("pattern"), base_of(ex) * per_week(day)) for day, ex in items])
    if pull and push > pull * 1.6:
        why = (f"Drücken {push:g} zu Ziehen {pull:g} Sätze pro Woche – ein Verhältnis nahe 1:1 "
               "entlastet die Schultern.")
        rows = [(day, ex) for day, ex in items if ex.get("pattern") == "h_pull" and base_of(ex) < MAX_BASE_SETS
                and room(load_of[day["id"]], "ruecken", 1)]
        if rows:
            day, ex = min(rows, key=lambda t: base_of(t[1]))
            new = base_of(ex) + 1
            suggestions.append({"level": "info", "why": why,
                                "text": f"„{ex['name']}“ auf {_sets_text(ex, new, ratios)} erhöhen "
                                        f"({day['label']})",
                                "action": {"type": "sets", "item_id": ex["id"], "target_sets": new}})
        else:
            s = _add_suggestion("ruecken", 3, ctx, loads, profile, used_ids, ("h_pull",), why)
            if s:
                suggestions.append(s)

    # 5. Wiederholungsbereiche: nur, wenn der Bereich gar nicht zum Ziel passt. Fuer Muskelaufbau wirkt fast jeder
    #    Bereich zwischen 6 und 30 Wiederholungen, solange es nah ans Muskelversagen geht.
    for day, ex in items:
        if not ex.get("pattern"):
            continue
        compound = ex.get("kind") == "compound"
        lo, hi = vp.reps_for(ex["pattern"], goal, compound)
        cur_lo, cur_hi = ex["target_reps_min"], ex["target_reps_max"]
        if cur_hi < lo or cur_lo > hi:
            suggestions.append({
                "level": "info",
                "text": f"„{ex['name']}“ auf {lo}–{hi} Wiederholungen umstellen",
                "why": f"Für dein Ziel ({GOALS.get(goal, goal)}) passt dieser Bereich "
                       f"besser als {cur_lo}–{cur_hi}.",
                "action": {"type": "reps", "item_id": ex["id"],
                           "target_reps_min": lo, "target_reps_max": hi},
            })

    # 6. Struktur: zusaetzlicher Tag oder A/B-Varianten (hoechstens ein Vorschlag)
    from . import plan_structure
    structure = plan_structure.suggest(profile, split, ctx, m, overflow)
    crowded = [load for load in loads if len(load["day"]["exercises"]) >= TOO_MANY_EXERCISES]
    if crowded and not structure:
        load = max(crowded, key=lambda x: len(x["day"]["exercises"]))
        suggestions.append({
            "level": "info",
            "text": f"„{load['day']['label']}“ hat {len(load['day']['exercises'])} Übungen",
            "why": "6–8 Übungen pro Einheit reichen meist: mehr Übungen für denselben Muskel bringen wenig "
                   "zusätzlich, "
                   "kosten aber Zeit für Umbau und Aufwärmen. Lieber je Übung einen Satz mehr.",
            "action": None,
        })
    long_days = [load for load in loads if load["peak"] > SESSION_CAP]
    if long_days and (not structure or structure["type"] != "add_day"):
        load = max(long_days, key=lambda x: x["peak"])
        suggestions.append({
            "level": "info",
            "text": f"„{load['day']['label']}“ wächst im Block auf {load['peak']} Sätze "
                    f"(≈ {vp.session_minutes(load['peak'], goal)} Minuten)",
            "why": f"Mehr als {SESSION_CAP} Sätze in einer Einheit bringen wenig – gegen Ende fehlt die Kraft. "
                   "Weniger Sätze bei Übungen mit viel Volumen oder ein weiterer Trainingstag helfen.",
            "action": None,
        })

    # 7. Split-Empfehlung aus dem Katalog (nur, wenn kein Struktur-Vorschlag den Plan direkt umbauen kann)
    advice = None if structure else _split_advice(profile, split, ctx, actual, freq)

    order = {"warn": 0, "info": 1}
    suggestions.sort(key=lambda s: order.get(s["level"], 2))
    return {
        "split": {"id": split["id"], "name": split["name"]},
        "sessions": ctx["sessions"],
        "per_session": SESSION_CAP,
        "notes": ctx["notes"],
        "volume": [{"group": g, "label": muscles.LABEL[g], "actual": actual[g], "target": target[g],
                    "peak": peak[g], "peak_target": ctx["peak_target"][g],
                    "frequency": freq[g], "focus": g in focus_groups}
                   for g in muscles.GROUPS if target[g] or actual[g]],
        "suggestions": suggestions[:12],
        "structure": structure,
        "split_advice": advice,
    }


WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _split_advice(profile, split, ctx, actual, freq):
    weekdays = sorted({wd for day in split["days"] for wd in (day.get("weekdays") or [])})
    if len(weekdays) < 2:
        return None
    options = generate.split_options(profile, {"weekdays": weekdays})
    best = options[0] if options else None
    if not best:
        return None
    target = ctx["target"]
    # Haeufigkeit vergleichen: die Split-Bewertung nutzt groebere Gruppen
    coarse = {"brust": "brust", "ruecken": "ruecken", "schultern": "schultern",
              "bizeps": "arme", "trizeps": "arme", "quads": "beine", "beinbeuger": "beine",
              "waden": "beine", "po": "po", "rumpf": None}
    better, worse = [], []
    for group, c in coarse.items():
        if not c or not target.get(group):
            continue
        now, then = freq.get(group, 0), best["frequency"].get(c, 0)
        if then >= 2 > now:
            better.append(muscles.LABEL[group])
        elif now >= 2 > then:
            worse.append(muscles.LABEL[group])
    if better and not worse:
        text = (f"Mit „{best['name']}“ kämen {', '.join(dict.fromkeys(better))} zweimal pro Woche dran statt "
                "einmal – das Volumen verteilt sich auf mehr Einheiten, jeder Satz bringt mehr.")
    elif better and worse:
        text = (f"„{best['name']}“ würde {', '.join(dict.fromkeys(better))} öfter trainieren, "
                f"dafür {', '.join(dict.fromkeys(worse))} seltener – das ist Geschmackssache.")
    else:
        seldom = [muscles.LABEL[g] for g in muscles.GROUPS
                  if target.get(g) and actual.get(g) and freq.get(g, 0) < 2]
        text = ("Dein Split passt gut zu deinen Trainingstagen."
                + (f" Nur {', '.join(seldom)} kommen einmal pro Woche dran – "
                   "ein zusätzlicher Tag wäre der einfachste Hebel." if seldom else ""))
    return {
        "current": {"sessions": ctx["sessions"], "frequency": freq},
        "best": {"key": best["key"], "name": best["name"], "reasons": best["reasons"],
                 "frequency": best["frequency"]},
        "better": list(dict.fromkeys(better)), "worse": list(dict.fromkeys(worse)),
        "text": text,
    }


# ------------------------------------------------------------------ Umsetzen
def _int(action, key, default=None):
    """action[key] als int, mit klarer ValueError statt KeyError/TypeError bei kaputter
    oder fehlender Eingabe – Vorschlaege kommen roh vom Client, nichts davon ist garantiert."""
    value = action.get(key, default)
    if value is None:
        raise ValueError(f"„{key}“ fehlt.")
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"„{key}“ ist keine gültige Zahl.") from e


def apply(profile_id, action):
    """Einen Vorschlag umsetzen. Prueft, dass alles zum Profil gehoert."""
    if not isinstance(action, dict):
        raise ValueError("Diesen Vorschlag kann die App nicht automatisch umsetzen.")
    kind = action.get("type")
    if kind == "restructure":
        from . import plan_structure
        return plan_structure.apply(profile_id, action)
    if kind in ("sets", "reps", "remove"):
        item = db.get_row("split_exercises", action.get("item_id"))
        if not item:
            raise ValueError("Diese Übung steht nicht mehr im Plan.")
        day = db.get_row("split_days", item["split_day_id"])
        split = db.get_row("splits", day["split_id"]) if day else None
        if not split or split["profile_id"] != profile_id:
            raise ValueError("Dieser Plan gehört zu einem anderen Profil.")
        if kind == "remove":
            db.delete_row("split_exercises", item["id"])
            return {"ok": True}
        data = ({"target_sets": _int(action, "target_sets"), "base_sets": _int(action, "target_sets")}
                if kind == "sets" else
                {"target_reps_min": _int(action, "target_reps_min"),
                 "target_reps_max": _int(action, "target_reps_max")})
        return db.update_row("split_exercises", item["id"], data)
    if kind == "add":
        day = db.get_row("split_days", action.get("day_id"))
        split = db.get_row("splits", day["split_id"]) if day else None
        if not split or split["profile_id"] != profile_id:
            raise ValueError("Dieser Trainingstag gehört zu einem anderen Profil.")
        if not db.exists("exercises", action.get("exercise_id")):
            raise ValueError("Diese Übung gibt es nicht (mehr).")
        siblings = db.list_rows_where("split_exercises", "split_day_id", day["id"])
        return db.insert_row("split_exercises", {
            "split_day_id": day["id"], "exercise_id": _int(action, "exercise_id"),
            "position": len(siblings), "target_sets": _int(action, "target_sets", 3),
            "base_sets": _int(action, "target_sets", 3),
            "target_reps_min": _int(action, "target_reps_min", 8),
            "target_reps_max": _int(action, "target_reps_max", 12),
            "rest_seconds": action.get("rest_seconds")})
    raise ValueError("Diesen Vorschlag kann die App nicht automatisch umsetzen.")
