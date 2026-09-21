"""Cardio (Kalorien-Schaetzung), aktive Tage, Serien und Meilensteine."""
from datetime import date, timedelta

from . import db
from .stats import _local_date, epley, today

# MET-Werte (Compendium of Physical Activities, gerundet) – fuer eine grobe kcal-Schaetzung
ACTIVITIES = {
    "laufen": ("Laufen", 9.8), "laufband": ("Laufband", 9.0), "gehen": ("Gehen", 3.5),
    "wandern": ("Wandern", 6.0), "radfahren": ("Radfahren", 7.5), "spinning": ("Spinning", 8.5),
    "rudern": ("Rudergerät", 7.0), "crosstrainer": ("Crosstrainer", 5.0),
    "stepper": ("Stepper", 9.0), "schwimmen": ("Schwimmen", 7.0), "hiit": ("HIIT", 8.0),
    "seilspringen": ("Seilspringen", 11.0), "tanzen": ("Tanzen", 5.0),
    "fussball": ("Fußball", 7.0), "sonstiges": ("Sonstiges", 5.0),
}
DEFAULT_WEIGHT = 75.0


def latest_weight(profile_id):
    row = db.one("SELECT kg FROM bodyweight WHERE profile_id=? ORDER BY date DESC LIMIT 1",
                 (profile_id,))
    return row["kg"] if row else None


def cardio_kcal(activity, minutes, weight_kg):
    met = ACTIVITIES.get(activity, ACTIVITIES["sonstiges"])[1]
    return round(met * (weight_kg or DEFAULT_WEIGHT) * (minutes or 0) / 60)


# -------------------------------------------------------------- Serien
def active_days(profile_id):
    days = set()
    for w in db.rows("SELECT COALESCE(started_at, finished_at) AS at FROM workouts WHERE profile_id=?",
                     (profile_id,)):
        d = _local_date(w["at"])
        if d:
            days.add(d)
    for table in ("cardio", "mobility_log"):
        for r in db.rows(f"SELECT date FROM {table} WHERE profile_id=?", (profile_id,)):
            try:
                days.add(date.fromisoformat(r["date"]))
            except ValueError:
                pass
    return days


def planned_weekdays(profile_id):
    """Trainingstage aus dem Profil, sonst aus dem aktiven Split."""
    p = db.get_row("profiles", profile_id) or {}
    wd = set(db.parse_weekdays(p.get("train_weekdays")))
    if not wd:
        split = db.active_split(profile_id)
        for d in (split or {}).get("days", []):
            wd.update(d.get("weekdays") or [])
    return wd


def _runs(days, planned, start, end):
    """Laengste und aktuelle Tagesserie zwischen start und end.
    Ein geplanter Ruhetag bricht die Serie nicht (zaehlt aber auch nicht mit).
    Ohne Plan werden bis zu zwei Ruhetage am Stueck toleriert."""
    best = cur = 0
    rest_run = 0
    d = start
    while d <= end:
        if d in days:
            cur += 1
            rest_run = 0
        else:
            rest_run += 1
            planned_rest = bool(planned) and d.weekday() not in planned
            tolerated = planned_rest or (not planned and rest_run <= 2)
            if not tolerated and d != end:   # der heutige Tag ist noch nicht vorbei
                cur = 0
        best = max(best, cur)
        d += timedelta(days=1)
    return best, cur


def _week_runs(days, end):
    weeks = {d - timedelta(days=d.weekday()) for d in days}
    if not weeks:
        return 0, 0
    monday = end - timedelta(days=end.weekday())
    best = cur = 0
    w = min(weeks)
    while w <= monday:
        if w in weeks:
            cur += 1
        elif w != monday:   # laufende Woche darf noch leer sein
            cur = 0
        best = max(best, cur)
        w += timedelta(weeks=1)
    return best, cur


# --------------------------------------------------------- Meilensteine
MILESTONES = [
    # key, Titel, Kennzahl, Schwelle, Beschreibung
    ("w1", "Erste Einheit", "workouts", 1, "Die erste Einheit gespeichert."),
    ("w10", "10 Einheiten", "workouts", 10, "Zehn Einheiten absolviert."),
    ("w25", "25 Einheiten", "workouts", 25, "Dranbleiben zahlt sich aus."),
    ("w50", "50 Einheiten", "workouts", 50, "Ein halbes Hundert."),
    ("w100", "100 Einheiten", "workouts", 100, "Dreistellig!"),
    ("d7", "7 Tage Serie", "best_daily", 7, "Eine Woche ohne Unterbrechung aktiv."),
    ("d30", "30 Tage Serie", "best_daily", 30, "Einen Monat am Stück dabei."),
    ("wk4", "4 Wochen am Stück", "best_weekly", 4, "Vier Wochen in Folge trainiert."),
    ("wk12", "12 Wochen am Stück", "best_weekly", 12, "Ein Vierteljahr ohne Pause."),
    ("v10", "10 Tonnen bewegt", "volume_t", 10, "Gesamtvolumen über alle Einheiten."),
    ("v50", "50 Tonnen bewegt", "volume_t", 50, "Das Gewicht eines Lkw-Zugs."),
    ("v100", "100 Tonnen bewegt", "volume_t", 100, "Ein Blauwal."),
    ("pr1", "Erste Bestleistung", "prs", 1, "Eine Übung stärker als je zuvor."),
    ("pr10", "10 Bestleistungen", "prs", 10, "Zehnmal die eigene Bestmarke geknackt."),
    ("c50", "50 km Cardio", "cardio_km", 50, "Strecke aus allen Cardio-Einheiten."),
    ("c100", "100 km Cardio", "cardio_km", 100, "Dreistellige Kilometer."),
    ("ci30", "30 Check-ins", "checkins", 30, "Einen Monat lang auf den Körper gehört."),
    ("bf1", "Erste Körperanalyse", "bodyfat", 1, "Körperfett zum ersten Mal erfasst."),
]


def metrics(profile_id):
    end = today()
    days = active_days(profile_id)
    planned = planned_weekdays(profile_id)
    start = min(days) if days else end
    best_daily, daily = _runs(days, planned, start, end)
    best_weekly, weekly = _week_runs(days, end)

    sets = db.rows("SELECT s.exercise_id, s.reps, s.weight FROM sets s"
                   " JOIN workouts w ON w.id = s.workout_id WHERE w.profile_id=? AND s.warmup=0"
                   " ORDER BY COALESCE(w.started_at, w.finished_at), w.id, s.id", (profile_id,))
    volume = sum((s["weight"] or 0) * (s["reps"] or 0) for s in sets)
    prs, best = 0, {}
    for s in sets:
        v = epley(s["weight"], s["reps"])
        if not v:
            continue
        prev = best.get(s["exercise_id"])
        if prev is not None and v > prev:
            prs += 1
        best[s["exercise_id"]] = max(prev or 0, v)

    one = lambda sql: (db.one(sql, (profile_id,)) or {}).get("n") or 0  # noqa: E731
    return {
        "workouts": one("SELECT COUNT(*) AS n FROM workouts WHERE profile_id=?"),
        "daily": daily, "best_daily": best_daily,
        "weekly": weekly, "best_weekly": best_weekly,
        "volume_t": round(volume / 1000, 1),
        "prs": prs,
        "cardio_km": round(one("SELECT SUM(distance_km) AS n FROM cardio WHERE profile_id=?"), 1),
        "checkins": one("SELECT COUNT(*) AS n FROM checkins WHERE profile_id=?"),
        "bodyfat": one("SELECT COUNT(*) AS n FROM body_measurements"
                       " WHERE profile_id=? AND body_fat_pct IS NOT NULL"),
        "_days": days, "_planned": planned,
    }


def achievements(profile_id):
    m = metrics(profile_id)
    end = today()
    monday = end - timedelta(days=end.weekday())
    week = []
    for i in range(7):
        d = monday + timedelta(days=i)
        week.append({"date": d.isoformat(), "active": d in m["_days"],
                     "planned": d.weekday() in m["_planned"], "today": d == end,
                     "future": d > end})
    items = []
    for key, title, metric, threshold, desc in MILESTONES:
        value = m[metric]
        items.append({"key": key, "title": title, "desc": desc, "metric": metric, "value": value,
                      "threshold": threshold, "achieved": value >= threshold,
                      "progress": min(1, value / threshold) if threshold else 1})
    reached = [i for i in items if i["achieved"]]
    # Pro Kennzahl nur das naechste offene Ziel zeigen
    upcoming, seen = [], set()
    for (key, _t, metric, *_), item in zip(MILESTONES, items):
        if not item["achieved"] and metric not in seen:
            seen.add(metric)
            upcoming.append(item)
    return {
        "daily_streak": m["daily"], "best_daily": m["best_daily"],
        "weekly_streak": m["weekly"], "best_weekly": m["best_weekly"],
        "week": week,
        "totals": {k: m[k] for k in ("workouts", "volume_t", "prs", "cardio_km", "checkins")},
        "reached": reached, "upcoming": upcoming,
    }


def reached_keys(profile_id):
    m = metrics(profile_id)
    return {key for key, _t, metric, threshold, _d in MILESTONES if m[metric] >= threshold}


def new_milestones(before, profile_id):
    after = reached_keys(profile_id)
    return [{"key": k, "title": t} for k, t, *_ in MILESTONES if k in after and k not in before]
