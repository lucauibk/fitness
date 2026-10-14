"""Auswertung einer fertigen Einheit, so wie ein Trainer sie nach dem Training durchgeht:
Was war geplant, was wurde stattdessen gemacht, wie viel kam bei jedem Muskel an, und wie
schlägt sich jede Übung gegen das letzte Mal.

Gerechnet, nicht gespeichert – einzige Quelle sind die geloggten Sätze, der Trainingstag und
die früheren Einheiten. Den Text dazu schreibt `workout_review.py` (Claude) aus diesen Fakten.

Was ein reiner ID-Abgleich falsch macht und hier bewusst anders läuft:

* **Ersatzübungen.** Schrägbankdrücken statt Brustpresse schräg ist kein Auslassen. Plan und
  Gemachtes werden erst über die Übung, dann über das Bewegungsmuster, dann über die
  Muskelgruppe einander zugeordnet (`_match`).
* **Reihenfolge.** Später in der Einheit schafft man weniger Wiederholungen (Simão et al. 2012).
  Steht eine Übung heute weiter hinten als beim letzten Mal, gilt ein kleiner Rückgang als
  gehalten, nicht als Rückschritt (`ORDER_TOLERANCE`).
* **Aufwärmen** braucht nur die erste Grundübung je Muskelgruppe – danach ist der Muskel warm.
* **Wiederholungen**: dass spätere Sätze abfallen, ist normal. Zu schwer ist das Gewicht erst,
  wenn schon der erste Arbeitssatz unter dem Bereich liegt.

Die Plan-Vorgaben werden im *aktuellen* Stand gelesen (Satzzahl über den Block zum Datum der
Einheit). Verschiebt man später die Zielwerte, verschiebt sich das Urteil über alte Einheiten.
"""
from datetime import datetime, timezone

from . import db, stats
from .planner import blocks, progression
from .planner import volume_plan as volume
from .planner.muscles import LABEL as MUSCLE_LABEL, PATTERN_GROUP

# Rückgang des e1RM (in %), der noch als "gehalten" zählt, wenn die Übung heute später in der
# Einheit lag als beim letzten Mal. Gesetzter Wert: eine Wiederholung weniger bei ~10 Wdh.
# sind rund 2,5 % e1RM – das ist die typische Ermüdung durch vorherige Übungen.
ORDER_TOLERANCE = 3.0
# Unterhalb dieser Änderung (in %) ist es gehalten – darunter liegt Messrauschen.
SAME = 1.0
# Standard-Anstrengung ohne Block: 2 Wiederholungen in Reserve
DEFAULT_RIR = 2
# Ab so vielen Arbeitssätzen taugt die Hochrechnung der Dauer (darunter eingetippte Einheiten)
MIN_SETS_FOR_DURATION = 6
# Eigene Übungen ohne Bewegungsmuster haben nur die grobe Gruppe. Wurde ein Teil davon
# trainiert, hat die grobe Gruppe nicht "nichts bekommen".
COARSE = {"beine": ("quads", "beinbeuger", "po", "waden"), "arme": ("bizeps", "trizeps")}
# Wichtigstes zuerst – wer nur die erste Zeile liest, liest die wichtigste
HINT_ORDER = ("plan", "reps", "rpe", "warmup", "dauer")


def _num(value):
    return value if isinstance(value, (int, float)) else None


def _at(workout):
    return workout.get("started_at") or workout.get("finished_at") or ""


def group_of(ex):
    """Muskelgruppe nach Bewegungsmuster (feiner: Bizeps/Trizeps statt Arme); eigene Übungen
    ohne Muster fallen auf ihre grobe Gruppe zurück."""
    return PATTERN_GROUP.get(ex.get("pattern")) or ex.get("muscle_group") or "sonstige"


def group_label(group):
    return MUSCLE_LABEL.get(group) or stats.GROUP_LABEL.get(group) or group.title()


def _fmt(value):
    """Deutsche Schreibweise wie fmtWeight() im Frontend: 37,5 statt 37.5, 40 statt 40.0."""
    return f"{value:g}".replace(".", ",")


def set_text(s):
    w, r = _num(s.get("weight")), _num(s.get("reps")) or 0
    text = f"{_fmt(w)}×{r}" if w else f"{r} Wdh."
    return text + (f" @{_fmt(s['rpe'])}" if _num(s.get("rpe")) is not None else "")


# ------------------------------------------------------------------ Daten holen
def _meta(exercise_ids):
    if not exercise_ids:
        return {}
    marks = ",".join("?" * len(exercise_ids))
    return {r["id"]: r for r in db.rows(
        f"SELECT id, name, muscle_group, pattern, kind, unit FROM exercises WHERE id IN ({marks})",
        tuple(exercise_ids))}


def plan_items(split_day_id):
    if not split_day_id:
        return []
    return db.rows(
        "SELECT se.*, e.name, e.muscle_group, e.pattern, e.kind, e.unit FROM split_exercises se"
        " JOIN exercises e ON e.id = se.exercise_id"
        " WHERE se.split_day_id=? ORDER BY se.position, se.id", (split_day_id,))


def block_for_day(split_day_id):
    day = db.get_row("split_days", split_day_id) if split_day_id else None
    return blocks.for_split(day["split_id"]) if day else None


def _history(profile_id, exercise_ids, before_at, before_id):
    """Alle früheren Arbeitssätze dieser Übungen, je Übung nach Einheit gruppiert, neueste
    Einheit zuerst – und für jede dieser Einheiten die Reihenfolge ihrer Übungen.
    Gleiche Zeitstempel werden über die id aufgelöst wie in `db.list_workouts`."""
    if not exercise_ids:
        return {}, {}
    marks = ",".join("?" * len(exercise_ids))
    rows = db.rows(
        "SELECT s.exercise_id, s.reps, s.weight, s.rpe, w.id AS wid,"
        " COALESCE(w.started_at, w.finished_at) AS at FROM sets s"
        " JOIN workouts w ON w.id = s.workout_id"
        f" WHERE w.profile_id=? AND s.warmup=0 AND s.reps > 0 AND s.exercise_id IN ({marks})"
        " AND (COALESCE(w.started_at, w.finished_at) < ?"
        "  OR (COALESCE(w.started_at, w.finished_at) = ? AND w.id < ?))"
        " ORDER BY at DESC, w.id DESC, s.set_no, s.id",
        (profile_id, *exercise_ids, before_at, before_at, before_id))
    per_ex = {}
    for r in rows:
        sessions = per_ex.setdefault(r["exercise_id"], [])
        if not sessions or sessions[-1]["wid"] != r["wid"]:
            local = stats._local_date(r["at"])
            sessions.append({"wid": r["wid"], "date": local.isoformat() if local else r["at"][:10],
                             "sets": []})
        sessions[-1]["sets"].append({"reps": r["reps"], "weight": r["weight"], "rpe": r["rpe"]})
    # Position der Übung in der jeweils letzten Einheit – eine Abfrage für alle
    last_ids = sorted({s[0]["wid"] for s in per_ex.values()})
    orders = {}
    if last_ids:
        marks = ",".join("?" * len(last_ids))
        for r in db.rows(f"SELECT workout_id, exercise_id FROM sets WHERE workout_id IN ({marks})"
                         " AND warmup=0 ORDER BY id", tuple(last_ids)):
            seq = orders.setdefault(r["workout_id"], [])
            if r["exercise_id"] not in seq:
                seq.append(r["exercise_id"])
    return per_ex, orders


# ------------------------------------------------------------------ Zuordnung
def _match(items, done_order, meta):
    """Plan ↔ gemacht, eins zu eins. Drei Durchgänge, jeweils in Planreihenfolge:
    gleiche Übung, gleiches Bewegungsmuster, gleiche Muskelgruppe. Gierig – bei mehreren
    Kandidaten gewinnt die zuerst gemachte Übung. -> {exercise_id: plan_item}"""
    matched, used = {}, set()
    keys = (lambda i, ex: i["exercise_id"] == ex,
            lambda i, ex: bool(i.get("pattern")) and i["pattern"] == meta[ex].get("pattern"),
            lambda i, ex: group_of(i) == group_of(meta[ex]))
    for same in keys:
        for item in items:
            if item["id"] in used:
                continue
            for ex in done_order:
                if ex not in matched and same(item, ex):
                    matched[ex] = item
                    used.add(item["id"])
                    break
    return matched


# ------------------------------------------------------------------ Vergleich je Übung
def _top(sets):
    """Bester Satz: schwerstes Gewicht, dort die meisten Wiederholungen (ohne Gewicht: Wdh.)."""
    weights = [_num(s.get("weight")) for s in sets if _num(s.get("weight"))]
    if not weights:
        return None, max((_num(s.get("reps")) or 0 for s in sets), default=0)
    top = max(weights)
    return top, max((_num(s.get("reps")) or 0) for s in sets if _num(s.get("weight")) == top)


def _e1rm(sets):
    best = progression.session_e1rm({"sets": sets})
    return best[0] if best else None


def _compare(now_sets, now_pos, prev, prev_pos):
    """Heute gegen die letzte Einheit mit derselben Übung."""
    # Früher in der Einheit = frischer, später = mit Ermüdung aus den Übungen davor
    slot = None if not prev_pos else "gleich" if now_pos == prev_pos else \
        "frueher" if now_pos < prev_pos else "spaeter"
    out = {"date": prev["date"], "position": prev_pos, "slot": slot,
           "best": set_text(max(prev["sets"], key=lambda s: (_num(s.get("weight")) or 0,
                                                             _num(s.get("reps")) or 0))),
           "delta_pct": None, "order_note": False}
    a, b = _e1rm(now_sets), _e1rm(prev["sets"])
    if a and b:
        delta = 100 * (a - b) / b
        out["delta_pct"] = round(delta, 1)
        if delta >= SAME:
            out["verdict"] = "up"
        elif delta > -SAME:
            out["verdict"] = "hold"
        elif prev_pos and now_pos > prev_pos and delta > -ORDER_TOLERANCE:
            out["verdict"], out["order_note"] = "hold", True
        else:
            out["verdict"] = "down"
        return out
    # Ohne Gewicht oder über 12 Wdh. taugt keine 1RM-Schätzung: bester Satz direkt vergleichen
    (cw, cr), (ow, orr) = _top(now_sets), _top(prev["sets"])
    if (cw or 0) != (ow or 0):
        out["verdict"] = "up" if (cw or 0) > (ow or 0) else "down"
    else:
        out["verdict"] = "up" if cr > orr else "hold" if cr == orr else "down"
    return out


# ------------------------------------------------------------------ Hinweise
def _minutes(workout):
    start, end = workout.get("started_at"), workout.get("finished_at")
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Eine Seite ohne Zeitzone (per API nachgetragen): als UTC lesen statt abzustürzen
    a, b = (x if x.tzinfo else x.replace(tzinfo=timezone.utc) for x in (a, b))
    dauer = (b - a).total_seconds() / 60
    return round(dauer) if dauer > 0 else None


def _duration_hint(minutes, n_sets, profile):
    """Zu lang heisst: die Pausen sind ausgeufert. Zu kurz meldet schon die Satzbilanz."""
    if minutes is None or n_sets < MIN_SETS_FOR_DURATION:
        return None
    per_set = volume.MINUTES_PER_SET.get(profile.get("goal") or "aufbau", 3.2)
    expected = volume.WARMUP_MINUTES + per_set * n_sets
    if minutes > expected * 1.8:
        return (f"{minutes} Minuten für {n_sets} Sätze – rund {round(expected)} wären normal, "
                "die Pausen sind ausgeufert")
    return None


def _rpe_hint(work_sets, rir):
    rpes = [_num(s.get("rpe")) for s in work_sets if _num(s.get("rpe")) is not None]
    target = 10 - rir
    if not rpes:
        return None, "Anstrengung (RPE) nicht notiert – ein Tipp nach dem letzten Satz reicht"
    avg = sum(rpes) / len(rpes)
    if avg < target - 1.5:
        return avg, (f"⌀ RPE {_fmt(round(avg, 1))} bei Ziel {_fmt(target)} – "
                     "da lag noch Reserve auf der Hantel")
    if avg > target + 1.5:
        return avg, (f"⌀ RPE {_fmt(round(avg, 1))} bei Ziel {_fmt(target)} – "
                     "diese Woche ist weniger Anstrengung vorgesehen")
    return avg, None


# ------------------------------------------------------------------ Hauptfunktion
def facts(profile_id, workout):
    sets = workout.get("sets") or []
    work = [s for s in sets if not s.get("warmup") and (_num(s.get("reps")) or 0) > 0]
    minutes = _minutes(workout)
    if not work:
        return {"headline": "Kein Arbeitssatz geloggt", "minutes": minutes, "planned": False,
                "sets_done": 0, "sets_planned": None, "muscles": [], "exercises": [],
                "skipped": [], "hints": [], "good": []}

    done_order, by_ex = [], {}
    for s in work:                       # Reihenfolge = Reihenfolge der Eingabe (set.id)
        if s["exercise_id"] not in by_ex:
            done_order.append(s["exercise_id"])
        by_ex.setdefault(s["exercise_id"], []).append(s)
    warmed = {s["exercise_id"] for s in sets if s.get("warmup")}

    items = plan_items(workout.get("split_day_id"))
    meta = _meta(set(done_order) | {i["exercise_id"] for i in items})
    profile = db.get_row("profiles", profile_id) or {}
    day = stats._local_date(_at(workout))
    block = block_for_day(workout.get("split_day_id"))
    state = blocks.state(block, day) if block else None
    for item in items:
        item["planned_sets"] = blocks.sets_for(block, item, day) if block else (item["target_sets"] or 0)
    matched = _match(items, done_order, meta)

    history, orders = _history(profile_id, done_order, _at(workout), workout.get("id") or 0)

    exercises, hints, good, prs = [], [], [], []
    for pos, ex in enumerate(done_order, start=1):
        m, ex_sets, item = meta[ex], by_ex[ex], matched.get(ex)
        entry = {"exercise_id": ex, "name": m["name"], "group": group_of(m), "position": pos,
                 "sets": [set_text(s) for s in ex_sets],
                 "planned": bool(item), "substitute_for": None, "planned_sets": None,
                 "compare": None, "pr": False}
        if item:
            entry["planned_sets"] = item["planned_sets"]
            if item["exercise_id"] != ex:
                entry["substitute_for"] = item["name"]
            # Der Wiederholungsbereich gehört zur geplanten Übung, nicht zu ihrem Ersatz – selbst
            # gleiches Muster reicht nicht (Hip Thrust und Abduktoren-Maschine sind beide "glutes").
            lo, hi = ((item.get("target_reps_min"), item.get("target_reps_max"))
                      if item["exercise_id"] == ex else (None, None))
            reps = [_num(s.get("reps")) or 0 for s in ex_sets]
            if lo and hi and reps[0] < lo:
                hints.append({"key": "reps", "text": f"{m['name']}: schon der erste Satz nur "
                              f"{reps[0]} Wdh. (Ziel {lo}–{hi}) – Gewicht zu hoch"})
            elif lo and hi and all(r > hi for r in reps):
                hints.append({"key": "reps", "text": f"{m['name']}: alle Sätze über {hi} Wdh. – "
                              "das Gewicht darf hoch"})
        past = history.get(ex) or []
        if past:
            prev = past[0]
            prev_pos = (orders.get(prev["wid"]) or []).index(ex) + 1 \
                if ex in (orders.get(prev["wid"]) or []) else None
            entry["compare"] = _compare(ex_sets, pos, prev, prev_pos)
            now_e1rm = _e1rm(ex_sets)
            best_before = max((v for v in (_e1rm(p["sets"]) for p in past) if v), default=None)
            # Bestleistung erst ab zwei früheren Einheiten – gegen eine einzige ist es nur "stärker"
            if len(past) >= 2 and now_e1rm and best_before and now_e1rm > best_before * (1 + SAME / 100):
                entry["pr"] = True
                prs.append(m["name"])
        exercises.append(entry)

    # Muskelbilanz: gemacht gegen geplant, in Planreihenfolge, Ungeplantes hinten an
    muscles = {}
    for item in items:
        g = group_of(item)
        muscles.setdefault(g, {"group": g, "label": group_label(g), "done": 0, "planned": 0})
        muscles[g]["planned"] += item["planned_sets"]
    for ex in done_order:
        g = group_of(meta[ex])
        muscles.setdefault(g, {"group": g, "label": group_label(g), "done": 0, "planned": 0})
        muscles[g]["done"] += len(by_ex[ex])
    muscles = list(muscles.values())

    planned_total = sum(i["planned_sets"] for i in items) if items else None
    used_items = {i["id"] for i in matched.values()}
    skipped = [{"name": i["name"], "group": group_of(i), "sets": i["planned_sets"]}
               for i in items if i["id"] not in used_items]
    # Angefangen, aber nicht fertig – in einer Zeile, vier fast gleiche Sätze lesen sich wie ein Tadel
    short = [f"{e['name']} {len(e['sets'])}/{e['planned_sets']}" for e in exercises
             if e["planned_sets"] and len(e["sets"]) < e["planned_sets"]]
    if short:
        hints.append({"key": "plan", "text": "Weniger Sätze als geplant: " + ", ".join(short)})
    if skipped:
        trained = {m["group"] for m in muscles if m["done"]}
        empty = [m["label"] for m in muscles if m["planned"] and not m["done"]
                 and not trained & set(COARSE.get(m["group"], ()))]
        text = "Ausgelassen: " + ", ".join(s["name"] for s in skipped)
        if empty:
            text += f" – {', '.join(empty)} {'hat' if len(empty) == 1 else 'haben'} heute nichts bekommen"
        hints.insert(0, {"key": "plan", "text": text})

    rir = state["rir"] if state else DEFAULT_RIR
    avg_rpe, rpe_hint = _rpe_hint(work, rir)
    if rpe_hint:
        hints.append({"key": "rpe", "text": rpe_hint})

    # Aufwärmen: nur die erste Grundübung je Muskelgruppe braucht es
    seen_groups, cold = set(), []
    for ex in done_order:
        g = group_of(meta[ex])
        if g in seen_groups:
            continue
        seen_groups.add(g)
        if meta[ex].get("kind") == "compound" and meta[ex].get("unit") != "koerper" and ex not in warmed:
            cold.append(meta[ex]["name"])
    if cold:
        hints.append({"key": "warmup", "text": f"{', '.join(cold)}: erste Übung für den Muskel, "
                      "ein leichter Aufwärmsatz davor schützt und bringt meist eine Wdh. mehr"})

    duration = _duration_hint(minutes, len(work), profile)
    if duration:
        hints.append({"key": "dauer", "text": duration})

    hints.sort(key=lambda h: HINT_ORDER.index(h["key"]))

    ups = [e["name"] for e in exercises if (e["compare"] or {}).get("verdict") == "up"]
    if prs:
        good.append(f"Bestleistung bei {', '.join(prs)}")
    rest_up = [n for n in ups if n not in prs]
    if rest_up:
        good.append(f"stärker als letztes Mal bei {', '.join(rest_up)}")
    if items and not skipped and planned_total and len(work) >= planned_total:
        good.append(f"alle {planned_total} geplanten Sätze gemacht")
    if avg_rpe is not None and not rpe_hint:
        good.append(f"Anstrengung passt (⌀ RPE {_fmt(round(avg_rpe, 1))})")

    parts = [f"{len(work)} von {planned_total} Sätzen" if planned_total else f"{len(work)} Sätze"]
    parts += [f"{m['label']} {m['done']}/{m['planned']}" if m["planned"] else f"{m['label']} {m['done']}"
              for m in muscles]
    if minutes:
        parts.append(f"{minutes} min")
    return {
        "headline": " · ".join(parts),
        "day_label": workout.get("day_label"),
        "date": day.isoformat() if day else None,
        "minutes": minutes, "planned": bool(items),
        "sets_done": len(work), "sets_planned": planned_total,
        "block": {"week": state["week"], "weeks": state["weeks"], "deload": state["deload"],
                  "rir": state["rir"]} if state else None,
        "avg_rpe": round(avg_rpe, 1) if avg_rpe is not None else None,
        "muscles": muscles, "exercises": exercises, "skipped": skipped,
        "hints": hints, "good": good,
    }
