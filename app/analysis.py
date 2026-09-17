"""Plan verbessern: schaut sich einen bestehenden Plan an (auch einen selbst gebauten),
rechnet Wochensaetze und Haeufigkeit je Muskelgruppe aus und macht daraus konkrete,
anwendbare Vorschlaege – Saetze anpassen, Uebung ergaenzen oder streichen, Split wechseln.

Richtwerte kommen aus app/volume.py (Volumen-Landmarken, Schoenfeld 2017).
"""
from . import db, generator, splits, volume

# Wenn eine Uebung kein Bewegungsmuster hat, grob ueber die Muskelgruppe zuordnen
GROUP_FALLBACK = {"brust": "brust", "ruecken": "ruecken", "schultern": "schultern",
                  "arme": "arme", "beine": "quads", "rumpf": "rumpf"}
# Welche Muster passen zu welchem Tagestyp (fuer "wo ergaenzen?")
UPPER = {"brust", "ruecken", "schultern", "arme"}
LOWER = {"quads", "beinbeuger", "po", "waden"}
MIN_GAP = 3      # ab so vielen fehlenden Saetzen gibt es einen Vorschlag
MAX_EXTRA = 4    # ab so vielen Saetzen zu viel


def _focus_names(profile):
    names = [volume.LABEL[t] for f in (profile.get("focus") or [])
             for t in volume.FOCUS_TARGETS.get(f, [])]
    seen = list(dict.fromkeys(names))
    return " und ".join([", ".join(seen[:-1]), seen[-1]]) if len(seen) > 1 else (seen[0] if seen else "")


def _group_of(ex):
    if ex.get("pattern"):
        return volume.PATTERN_TARGET.get(ex["pattern"])
    return GROUP_FALLBACK.get(ex.get("muscle_group"))


def _per_week(day):
    return len(day.get("weekdays") or []) or 1


def measure(split):
    """Wochensaetze und Haeufigkeit je Zielgruppe, plus Einheiten pro Woche."""
    sets = {g: 0 for g in volume.LANDMARKS}
    freq = {g: 0 for g in volume.LANDMARKS}
    sessions = 0
    for day in split["days"]:
        times = _per_week(day)
        sessions += times
        groups = set()
        for ex in day["exercises"]:
            g = _group_of(ex)
            if not g:
                continue
            sets[g] += (ex.get("target_sets") or 0) * times
            groups.add(g)
        for g in groups:
            freq[g] += times
    return sets, freq, max(1, sessions)


def _day_for(group, split):
    """Passender Tag fuer eine neue Uebung: zuerst ein Tag, der die Gruppe schon
    trainiert, sonst der Tag mit den wenigsten Saetzen der passenden Koerperhaelfte."""
    scored = []
    for day in split["days"]:
        groups = {_group_of(e) for e in day["exercises"]}
        total = sum(e.get("target_sets") or 0 for e in day["exercises"])
        half_ok = (group in LOWER and groups & LOWER) or (group in UPPER and groups & UPPER) \
            or group == "rumpf"
        scored.append((0 if group in groups else 1 if half_ok else 2, total, day))
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2] if scored else None


def _pick_exercise(group, used_ids, profile, prefer_patterns=()):
    """Passende Uebung aus dem Katalog: beruecksichtigt Einschraenkungen und Erfahrung."""
    rules = generator.limitation_rules(profile.get("limitations"))
    excluded = {n for r in rules for n in r[2]}
    preferred = [n for r in rules for n in r[3]] + generator.PREFER.get(
        profile.get("experience") or "einsteiger", [])
    rank = {name: i for i, name in enumerate(preferred)}
    best = None
    for ex in generator.catalog():
        if ex["id"] in used_ids or ex["name"] in excluded:
            continue
        if volume.PATTERN_TARGET.get(ex["pattern"]) != group:
            continue
        key = (0 if (not prefer_patterns or ex["pattern"] in prefer_patterns) else 1,
               rank.get(ex["name"], 99), ex["id"])
        if best is None or key < best[0]:
            best = (key, ex)
    return best[1] if best else None


def _add_suggestion(group, missing, split, profile, goal, used_ids, prefer_patterns=(), why=""):
    ex = _pick_exercise(group, used_ids, profile, prefer_patterns)
    day = _day_for(group, split)
    if not ex or not day:
        return None
    sets = max(2, min(4, missing))
    compound = ex["kind"] == "compound"
    lo, hi = volume.reps_for(ex["pattern"], goal, compound)
    used_ids.add(ex["id"])
    return {
        "level": "warn" if missing >= 4 else "info",
        "text": f"{ex['name']} mit {sets} Sätzen zu „{day['label']}“ hinzufügen",
        "why": why or f"{volume.LABEL[group]}: {missing} Sätze pro Woche fehlen zum Richtwert.",
        "action": {"type": "add", "day_id": day["id"], "exercise_id": ex["id"],
                   "target_sets": sets, "target_reps_min": lo, "target_reps_max": hi,
                   "rest_seconds": volume.rest_for(ex["pattern"], goal, compound)},
    }


def analyze(profile_id, split_id=None):
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    split = db.split_detail(split_id) if split_id else db.active_split(profile_id)
    if not split or not split["days"]:
        return {"split": None, "suggestions": [], "volume": [], "sessions": 0}

    actual, freq, sessions = measure(split)
    goal = profile.get("goal") or "aufbau"
    target, notes, per_session = volume.targets(
        profile.get("experience") or "einsteiger", goal, profile.get("focus") or [],
        sessions, profile.get("session_minutes"))
    focus_groups = {t for f in (profile.get("focus") or []) for t in volume.FOCUS_TARGETS.get(f, [])}

    items = [(day, ex) for day in split["days"] for ex in day["exercises"]]
    used_ids = {ex["exercise_id"] for _, ex in items}
    suggestions = []

    # 1. Uebungen ohne Bewegungsmuster – sie zaehlen sonst nicht mit
    for day, ex in items:
        if not ex.get("pattern"):
            suggestions.append({
                "level": "info",
                "text": f"Bewegungsmuster für „{ex['name']}“ eintragen",
                "why": "Ohne Muster kann die App diese Übung beim Volumen nur grob zuordnen.",
                "action": {"type": "open_exercise", "exercise_id": ex["exercise_id"]},
            })

    # 2. Volumen je Muskelgruppe
    for group, want in sorted(target.items(), key=lambda kv: -kv[1]):
        have = actual.get(group, 0)
        gap = want - have
        if want >= 4 and gap >= MIN_GAP:
            # zuerst Saetze bei einer vorhandenen Uebung erhoehen
            candidates = [(day, ex) for day, ex in items
                          if _group_of(ex) == group and (ex.get("target_sets") or 0) < 5]
            if candidates:
                day, ex = min(candidates, key=lambda t: t[1]["target_sets"])
                add = min(5 - ex["target_sets"], max(1, round(gap / _per_week(day))))
                suggestions.append({
                    "level": "warn" if gap >= 5 else "info",
                    "text": f"„{ex['name']}“ auf {ex['target_sets'] + add} Sätze erhöhen"
                            f" ({day['label']})",
                    "why": f"{volume.LABEL[group]}: {have} statt {want} Sätze pro Woche"
                           + (" – das ist deine Fokus-Muskelgruppe." if group in focus_groups else "."),
                    "action": {"type": "sets", "item_id": ex["id"],
                               "target_sets": ex["target_sets"] + add},
                })
            else:
                s = _add_suggestion(group, gap, split, profile, goal, used_ids)
                if s:
                    suggestions.append(s)
        elif want and have - want >= MAX_EXTRA:
            group_items = [(day, ex) for day, ex in items if _group_of(ex) == group]
            isolation = [t for t in group_items if t[1].get("kind") != "compound"]
            day, ex = max(isolation or group_items, key=lambda t: t[1]["target_sets"])
            over = have - want
            if ex["target_sets"] > 2:
                new = max(2, ex["target_sets"] - max(1, round(over / _per_week(day))))
                suggestions.append({
                    "level": "info",
                    "text": f"„{ex['name']}“ auf {new} Sätze reduzieren ({day['label']})",
                    "why": (f"{volume.LABEL[group]}: {have} Sätze pro Woche. "
                            + (f"Weil {_focus_names(profile)} im Fokus stehen, reicht hier "
                               f"Erhaltungsvolumen ({want} Sätze) – die Zeit steckst du besser in den Fokus."
                               if focus_groups else
                               f"Mehr als {want} Sätze bringen kaum zusätzlichen Aufbau.")),
                    "action": {"type": "sets", "item_id": ex["id"], "target_sets": new},
                })
            else:
                suggestions.append({
                    "level": "info",
                    "text": f"„{ex['name']}“ aus „{day['label']}“ streichen",
                    "why": (f"{volume.LABEL[group]}: {have} statt {want} Sätze pro Woche"
                            + (f" – im Fokus stehen {_focus_names(profile)}." if focus_groups else ".")),
                    "action": {"type": "remove", "item_id": ex["id"]},
                })

    # 3. Klassische Luecken
    patterns = {ex.get("pattern") for _, ex in items}
    if actual["quads"] and not (patterns & {"hinge", "leg_iso_back"}):
        s = _add_suggestion("beinbeuger", 6, split, profile, goal, used_ids,
                            ("hinge", "leg_iso_back"),
                            "Für die Beinrückseite fehlt eine gezielte Übung – das erhöht "
                            "auf Dauer das Verletzungsrisiko.")
        if s:
            suggestions.append(s)
    if not (patterns & {"rear_delt"}) and actual["brust"] >= 6:
        s = _add_suggestion("schultern", 4, split, profile, goal, used_ids, ("rear_delt",),
                            "Die hintere Schulter fehlt – sie hält bei viel Drücken das "
                            "Gleichgewicht in der Schulter.")
        if s:
            suggestions.append(s)
    if not (patterns & {"calves"}) and target["waden"]:
        s = _add_suggestion("waden", target["waden"], split, profile, goal, used_ids, ("calves",),
                            "Waden kommen im Plan gar nicht vor.")
        if s:
            suggestions.append(s)
    if not (patterns & {"core"}) and target["rumpf"]:
        s = _add_suggestion("rumpf", target["rumpf"], split, profile, goal, used_ids, ("core",),
                            "Für den Rumpf gibt es keine eigene Übung.")
        if s:
            suggestions.append(s)

    push = actual["brust"] + actual["schultern"]
    pull = actual["ruecken"]
    if pull and push > pull * 1.6:
        suggestions.append({
            "level": "info",
            "text": "Mehr Zugarbeit einplanen (Rudern oder Latzug)",
            "why": f"Drücken {push} zu Ziehen {pull} Sätze pro Woche – ein Verhältnis nahe 1:1 "
                   "entlastet die Schultern.",
            "action": None,
        })

    # 4. Wiederholungsbereiche zum Ziel
    for day, ex in items:
        if not ex.get("pattern"):
            continue
        compound = ex.get("kind") == "compound"
        lo, hi = volume.reps_for(ex["pattern"], goal, compound)
        cur_lo, cur_hi = ex["target_reps_min"], ex["target_reps_max"]
        if abs(cur_lo - lo) >= 4 or abs(cur_hi - hi) >= 5:
            suggestions.append({
                "level": "info",
                "text": f"„{ex['name']}“ auf {lo}–{hi} Wiederholungen umstellen",
                "why": f"Für dein Ziel ({generator.GOALS.get(goal, goal)}) passt dieser Bereich "
                       f"besser als {cur_lo}–{cur_hi}.",
                "action": {"type": "reps", "item_id": ex["id"],
                           "target_reps_min": lo, "target_reps_max": hi},
            })

    # 5. Split-Empfehlung
    weekdays = sorted({wd for day in split["days"] for wd in (day.get("weekdays") or [])})
    advice = None
    if len(weekdays) >= 2:
        options = generator.split_options(profile, {"weekdays": weekdays})
        best = options[0] if options else None
        if best:
            # Haeufigkeit vergleichen: die Split-Bewertung nutzt groebere Gruppen
            coarse = {"brust": "brust", "ruecken": "ruecken", "schultern": "schultern",
                      "arme": "arme", "quads": "beine", "beinbeuger": "beine",
                      "waden": "beine", "po": "po", "rumpf": None}
            better, worse = [], []
            for group, c in coarse.items():
                if not c or not target.get(group):
                    continue
                now, then = freq.get(group, 0), best["frequency"].get(c, 0)
                if then >= 2 > now:
                    better.append(volume.LABEL[group])
                elif now >= 2 > then:
                    worse.append(volume.LABEL[group])
            if better and not worse:
                text = (f"Mit „{best['name']}“ kämen {', '.join(dict.fromkeys(better))} zweimal "
                        "pro Woche dran statt einmal – bei gleichem Volumen bringt das mehr.")
            elif better and worse:
                text = (f"„{best['name']}“ würde {', '.join(dict.fromkeys(better))} öfter trainieren, "
                        f"dafür {', '.join(dict.fromkeys(worse))} seltener – das ist Geschmackssache.")
            else:
                seldom = [volume.LABEL[g] for g in volume.LANDMARKS
                          if target.get(g) and actual.get(g) and freq.get(g, 0) < 2]
                text = ("Dein Split passt gut zu deinen Trainingstagen."
                        + (f" Nur {', '.join(seldom)} kommen einmal pro Woche dran – "
                           "ein zusätzlicher Tag wäre der einfachste Hebel." if seldom else ""))
            advice = {
                "current": {"sessions": sessions, "frequency": freq},
                "best": {"key": best["key"], "name": best["name"], "reasons": best["reasons"],
                         "frequency": best["frequency"]},
                "better": list(dict.fromkeys(better)), "worse": list(dict.fromkeys(worse)),
                "text": text,
            }

    order = {"warn": 0, "info": 1}
    suggestions.sort(key=lambda s: order.get(s["level"], 2))
    return {
        "split": {"id": split["id"], "name": split["name"]},
        "sessions": sessions,
        "per_session": per_session,
        "notes": notes,
        "volume": [{"group": g, "label": volume.LABEL[g], "actual": actual[g],
                    "target": target[g], "frequency": freq[g], "focus": g in focus_groups}
                   for g in volume.LANDMARKS if target[g] or actual[g]],
        "suggestions": suggestions[:12],
        "split_advice": advice,
    }


def apply(profile_id, action):
    """Einen Vorschlag umsetzen. Prueft, dass alles zum Profil gehoert."""
    kind = (action or {}).get("type")
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
        data = ({"target_sets": int(action["target_sets"])} if kind == "sets" else
                {"target_reps_min": int(action["target_reps_min"]),
                 "target_reps_max": int(action["target_reps_max"])})
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
            "split_day_id": day["id"], "exercise_id": action["exercise_id"],
            "position": len(siblings), "target_sets": int(action.get("target_sets") or 3),
            "target_reps_min": int(action.get("target_reps_min") or 8),
            "target_reps_max": int(action.get("target_reps_max") or 12),
            "rest_seconds": action.get("rest_seconds")})
    raise ValueError("Diesen Vorschlag kann die App nicht automatisch umsetzen.")
