"""Auswertungen: Bestleistungen, Volumen, Häufigkeit, Gewicht und Zielvorschläge.
Einzige Quelle ist die Tabelle `sets` – es gibt keine vorberechneten Tabellen, die
auseinanderlaufen könnten."""
import os
from datetime import date, datetime, timedelta

from . import db

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# Steigerung beim nächsten Mal: kleine Muskelgruppen in kleineren Schritten
INCREMENT = {"beine": 5.0, "ruecken": 2.5, "brust": 2.5, "schultern": 2.5, "arme": 1.25,
             "rumpf": 1.25}
GROUP_LABEL = {"brust": "Brust", "ruecken": "Rücken", "beine": "Beine",
               "schultern": "Schultern", "arme": "Arme", "rumpf": "Rumpf"}


def tz():
    name = os.environ.get("TZ_NAME", "Europe/Vienna")
    if ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            pass
    return None


def today():
    return datetime.now(tz()).date()


ONE_RM_MAX_REPS = 12


def epley(weight, reps):
    """Geschätztes Einer-Maximum (Epley). Ohne Gewicht (Klimmzüge) nicht sinnvoll."""
    if not weight or not reps or reps <= 0:
        return None
    return round(weight * (1 + reps / 30), 1)


def _local_date(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo and tz():
        dt = dt.astimezone(tz())
    return dt.date()


def _sets(profile_id, since=None):
    # Aufwaermsaetze zaehlen nicht in Volumen, Bestleistungen oder Progression.
    sql = ("SELECT s.*, e.name AS exercise, e.muscle_group, e.unit,"
           " COALESCE(w.started_at, w.finished_at) AS at, w.id AS wid"
           " FROM sets s JOIN workouts w ON w.id = s.workout_id"
           " JOIN exercises e ON e.id = s.exercise_id"
           " WHERE w.profile_id=? AND s.warmup=0")
    params = [profile_id]
    if since:
        sql += " AND COALESCE(w.started_at, w.finished_at) >= ?"
        params.append(since)
    # id als zweites Kriterium: zwei Einheiten am selben Tag sollen eine feste
    # Reihenfolge haben, sonst schwankt der Vorschlag von Aufruf zu Aufruf.
    return db.rows(sql + " ORDER BY at, w.id, s.id", params)


def best_per_exercise(profile_id):
    """Je Übung der beste Satz und das beste geschätzte 1RM, jeweils mit Datum."""
    best = {}
    for s in _sets(profile_id):
        if not s["reps"]:
            continue
        key = s["exercise_id"]
        score = epley(s["weight"], s["reps"]) or (s["reps"] if not s["weight"] else 0)
        cur = best.get(key)
        if cur is None or (score or 0) > (cur["score"] or 0):
            best[key] = {
                "exercise_id": key, "exercise": s["exercise"], "unit": s["unit"],
                "muscle_group": s["muscle_group"], "reps": s["reps"], "weight": s["weight"],
                # Die Schaetzung taugt nur bis etwa 12 Wiederholungen – aus 20 Wdh. hochgerechnet
                # waere sie Fantasie. Gerankt wird weiter mit dem vollen Wert (score).
                "one_rm": epley(s["weight"], s["reps"]) if s["reps"] <= ONE_RM_MAX_REPS else None,
                "score": score,
                "date": _local_date(s["at"]).isoformat() if _local_date(s["at"]) else None,
            }
    out = sorted(best.values(), key=lambda b: (b["muscle_group"] or "", b["exercise"]))
    for b in out:
        b.pop("score", None)
    return out


def history(profile_id, exercise_id):
    """Ein Punkt je Einheit: das beste geschätzte 1RM (bzw. die meisten Wiederholungen,
    wenn ohne Zusatzgewicht trainiert wird)."""
    points = {}
    for s in _sets(profile_id):
        if s["exercise_id"] != exercise_id or not s["reps"]:
            continue
        day = _local_date(s["at"])
        if not day:
            continue
        value = epley(s["weight"], s["reps"]) or s["reps"]
        key = day.isoformat()
        if key not in points or value > points[key]["value"]:
            points[key] = {"date": key, "value": value, "reps": s["reps"], "weight": s["weight"]}
    return sorted(points.values(), key=lambda p: p["date"])


def _monday(d):
    return d - timedelta(days=d.weekday())


def weekly_volume(profile_id, weeks=12):
    """Sätze und bewegte Kilogramm je Muskelgruppe und Woche."""
    start = _monday(today()) - timedelta(weeks=weeks - 1)
    by_week = {}
    for s in _sets(profile_id, since=start.isoformat()):
        day = _local_date(s["at"])
        if not day:
            continue
        week = _monday(day).isoformat()
        group = s["muscle_group"] or "sonstige"
        slot = by_week.setdefault(week, {})
        entry = slot.setdefault(group, {"sets": 0, "kg": 0.0})
        entry["sets"] += 1
        if s["weight"] and s["reps"]:
            entry["kg"] += s["weight"] * s["reps"]
    out = []
    for i in range(weeks):
        week = (start + timedelta(weeks=i)).isoformat()
        groups = by_week.get(week, {})
        out.append({
            "week": week,
            "groups": [{"group": g, "label": GROUP_LABEL.get(g, g.title()),
                        "sets": v["sets"], "kg": round(v["kg"])}
                       for g, v in sorted(groups.items(), key=lambda kv: -kv[1]["sets"])],
            "sets": sum(v["sets"] for v in groups.values()),
            "kg": round(sum(v["kg"] for v in groups.values())),
        })
    return out


def frequency(profile_id, weeks=12):
    """Einheiten je Woche, Tage mit Training und die aktuelle Wochen-Serie."""
    start = _monday(today()) - timedelta(weeks=weeks - 1)
    workouts = db.rows(
        "SELECT COALESCE(started_at, finished_at) AS at FROM workouts"
        " WHERE profile_id=? AND COALESCE(started_at, finished_at) >= ?",
        (profile_id, start.isoformat()))
    days = sorted({_local_date(w["at"]).isoformat() for w in workouts if _local_date(w["at"])})
    per_week = {}
    for d in days:
        per_week[_monday(date.fromisoformat(d)).isoformat()] = per_week.get(
            _monday(date.fromisoformat(d)).isoformat(), 0) + 1
    # Serie: zusammenhängende Wochen mit mindestens einer Einheit, von dieser Woche
    # rückwärts. Die laufende Woche darf noch leer sein, ohne die Serie zu beenden.
    streak = 0
    cursor = _monday(today())
    if per_week.get(cursor.isoformat(), 0) == 0:
        cursor -= timedelta(weeks=1)
    while per_week.get(cursor.isoformat(), 0) > 0:
        streak += 1
        cursor -= timedelta(weeks=1)
    weeks_list = [{"week": (start + timedelta(weeks=i)).isoformat(),
                   "count": per_week.get((start + timedelta(weeks=i)).isoformat(), 0)}
                  for i in range(weeks)]
    return {"weeks": weeks_list, "days": days, "streak": streak,
            "this_week": per_week.get(_monday(today()).isoformat(), 0),
            "total": len(db.rows("SELECT id FROM workouts WHERE profile_id=?", (profile_id,)))}


def bodyweight(profile_id, limit=180):
    points = db.rows("SELECT date, kg FROM bodyweight WHERE profile_id=?"
                     " ORDER BY date DESC LIMIT ?", (profile_id, limit))
    points.reverse()
    # Geglättete Linie: gleitender Mittelwert über 7 Einträge
    for i, p in enumerate(points):
        window = points[max(0, i - 6):i + 1]
        p["avg"] = round(sum(w["kg"] for w in window) / len(window), 1)
    return points


def last_sets(profile_id, exercise_id):
    """Die Sätze der letzten Einheit, in der diese Übung vorkam."""
    last = db.one(
        "SELECT w.id FROM workouts w JOIN sets s ON s.workout_id = w.id"
        " WHERE w.profile_id=? AND s.exercise_id=? AND s.warmup=0"
        " ORDER BY COALESCE(w.started_at, w.finished_at) DESC, w.id DESC LIMIT 1",
        (profile_id, exercise_id))
    if not last:
        return []
    return db.rows("SELECT set_no, reps, weight, rpe FROM sets"
                   " WHERE workout_id=? AND exercise_id=? AND warmup=0"
                   " ORDER BY set_no, id", (last["id"], exercise_id))


def suggest(profile_id, exercise, target_sets=None, reps_min=None, reps_max=None, rir=2, deload=False):
    """Vorschlag fuer die naechste Einheit – Rechenlogik in planner.progression."""
    from .planner import progression
    lo = reps_min or 8
    return progression.for_exercise(profile_id, exercise["id"], lo, max(reps_max or lo, lo),
                                    target_sets, rir, deload)


def ready_to_raise(profile_id):
    """Fürs Fortschritt-Fenster: bei welchen Übungen ist der Bereich ausgereizt,
    das Gewicht also fällig?"""
    split = db.active_split(profile_id)
    if not split:
        return []
    out = []
    for day in split["days"]:
        for item in day["exercises"]:
            ex = {"id": item["exercise_id"], "muscle_group": item["muscle_group"]}
            s = suggest(profile_id, ex, item["target_sets"],
                        item["target_reps_min"], item["target_reps_max"])
            if s["raise_weight"]:
                out.append({"exercise": item["name"], "day": day["label"],
                            "unit": item["unit"], "from": max(
                                (p["weight"] or 0) for p in s["last"]),
                            "to": s["weight"], "range": s["range"]})
    return out


RECOVERY_GROUPS = {"beine", "po", "brust", "ruecken"}   # wie splits.RECOVERY


def _big_groups(day):
    return {e.get("muscle_group") for e in day.get("exercises", [])} & RECOVERY_GROUPS


def schedule_day(profile_id, split, on_date):
    """Adaptiver Wochenplan. Die Wochentage der Split-Tage sind nur der Soll-Plan; was in dieser
    Woche tatsaechlich trainiert wurde, hakt den passenden Tag ab – egal an welchem Wochentag.
    Offen bleibt, was noch fehlt: Wer am Unterkoerper-Tag Oberkoerper trainiert, bekommt als
    Naechstes den Unterkoerper. Ein ausgelassener Tag rutscht auf den naechsten Tag (auch ohne
    Praeferenz), sofern die Muskeln von gestern erholt sind.
    Rueckgabe: (Tag, fest, Hinweis) – fest = der Tag ist wie geplant dran; Hinweis "nachgeholt"
    oder "erholung" (ausstehender Tag belastet dieselben Muskeln wie gestern)."""
    slots = sorted(((wd, d) for d in split["days"] for wd in (d.get("weekdays") or [])),
                   key=lambda s: s[0])
    if not slots:
        return rotation_day(profile_id, split), False, None
    monday = on_date - timedelta(days=on_date.weekday())
    yesterday = on_date - timedelta(days=1)
    done, yesterday_ids = {}, set()
    for r in db.rows("SELECT w.split_day_id, COALESCE(w.started_at, w.finished_at) AS at,"
                     " (SELECT COUNT(*) FROM sets s WHERE s.workout_id = w.id AND s.warmup=0) AS n_sets"
                     " FROM workouts w WHERE w.profile_id=? AND w.split_day_id IS NOT NULL"
                     " ORDER BY at DESC LIMIT 60", (profile_id,)):
        d = _local_date(r["at"])
        if d and monday <= d <= on_date:
            done[r["split_day_id"]] = done.get(r["split_day_id"], 0) + 1
            # Erholungs-Sperre: gestern immer, vorgestern nach einer langen Einheit (>= 15 Saetze) – ~48 h
            if d == yesterday or (d == on_date - timedelta(days=2) and (r["n_sets"] or 0) >= 15):
                yesterday_ids.add(r["split_day_id"])
    remaining = []
    for wd, d in slots:
        if done.get(d["id"], 0) > 0:
            done[d["id"]] -= 1
        else:
            remaining.append((wd, d))
    tired = set()
    for d in split["days"]:
        if d["id"] in yesterday_ids:
            tired |= _big_groups(d)
    due = [(wd, d) for wd, d in remaining if wd <= on_date.weekday()]
    for wd, d in due:
        # Der heute geplante Tag gilt immer; nachgeholte nur, wenn die Muskeln erholt sind.
        if wd == on_date.weekday() or not (_big_groups(d) & tired):
            return d, wd == on_date.weekday(), ("nachgeholt" if wd < on_date.weekday() else None)
    nxt = remaining[0][1] if remaining else rotation_day(profile_id, split)
    return nxt, False, ("erholung" if due else None)


def rotation_day(profile_id, split):
    """Nächster Teil in der Rotation: der zuletzt trainierte + 1."""
    days = split["days"]
    if not days:
        return None
    last = db.one("SELECT split_day_id FROM workouts WHERE profile_id=? AND split_day_id IS NOT NULL"
                  " ORDER BY COALESCE(started_at, finished_at) DESC, id DESC LIMIT 1", (profile_id,))
    if not last or not last["split_day_id"]:
        return days[0]
    ids = [d["id"] for d in days]
    if last["split_day_id"] not in ids:
        return days[0]
    return days[(ids.index(last["split_day_id"]) + 1) % len(days)]


def day_plan(profile_id, day):
    """Ein Trainingstag mit letzten Werten und Vorschlägen je Übung – nach Blockwoche."""
    from .planner import blocks
    block = blocks.ensure(profile_id)
    st = blocks.state(block) if block else None
    items = []
    for item in day["exercises"]:
        ex = {"id": item["exercise_id"], "muscle_group": item["muscle_group"]}
        if st:
            item = {**item, "target_sets": blocks.sets_for(block, item)}
        s = suggest(profile_id, ex, item["target_sets"],
                    item["target_reps_min"], item["target_reps_max"],
                    rir=st["rir"] if st else 2, deload=bool(st and st["deload"]))
        items.append({
            "split_exercise_id": item["id"], "exercise_id": item["exercise_id"],
            "name": item["name"], "unit": item["unit"], "muscle_group": item["muscle_group"],
            "kind": item.get("kind"), "pattern": item.get("pattern"),
            "target_sets": item["target_sets"],
            "target_reps_min": item["target_reps_min"], "target_reps_max": item["target_reps_max"],
            "target_weight": item["target_weight"], "notes": item["notes"],
            "rest_seconds": item.get("rest_seconds"),
            "last": s["last"], "suggest": {"reps": s["reps"], "weight": s["weight"],
                                           "reason": s["reason"],
                                           "raise_weight": s["raise_weight"], "status": s.get("status"),
                                           "estimate": bool(s.get("estimate"))},
        })
    return {"id": day["id"], "label": day["label"], "weekdays": day.get("weekdays") or [],
            "exercises": items, "block": st}


def today_view(profile_id):
    split = db.active_split(profile_id)
    if not split:
        return {"split": None, "day": None, "fixed": False, "days": [],
                "last_workout": None}
    day, fixed, note = schedule_day(profile_id, split, today())
    last = db.rows("SELECT w.id, w.split_day_id, COALESCE(w.started_at, w.finished_at) AS at,"
                   " d.label AS day_label FROM workouts w"
                   " LEFT JOIN split_days d ON d.id = w.split_day_id"
                   " WHERE w.profile_id=? ORDER BY at DESC, w.id DESC LIMIT 1", (profile_id,))
    return {
        "split": {"id": split["id"], "name": split["name"]},
        "day": day_plan(profile_id, day) if day else None,
        "fixed": fixed,
        "note": note,
        "position": next((i + 1 for i, d in enumerate(split["days"]) if day and d["id"] == day["id"]), None),
        "day_count": len(split["days"]),
        "days": [{"id": d["id"], "label": d["label"], "weekdays": d.get("weekdays") or []}
                 for d in split["days"]],
        "last_workout": last[0] if last else None,
    }


def progress(profile_id):
    return {
        "best": best_per_exercise(profile_id),
        "volume": weekly_volume(profile_id),
        "frequency": frequency(profile_id),
        "bodyweight": bodyweight(profile_id),
        "ready": ready_to_raise(profile_id),
        "groups": GROUP_LABEL,
    }
