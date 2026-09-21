"""Die Startseite "Heute" in einer Antwort: Plan, Check-in, angepasstes Training,
Mobility-Vorschlag, Wochen-Streak und Kalorienbilanz."""
from . import activity, db, mobility, nutrition, readiness, session_feedback, stats


def adjusted_day(profile_id, day, checkin):
    plan = stats.day_plan(profile_id, day)
    plan["exercises"] = readiness.adjust(plan["exercises"], checkin)
    return plan


def today_workout(profile_id, last):
    """Die heute schon gespeicherte Einheit fuer die Erledigt-Karte auf "Heute" – mit Punktzahl.
    Die Bewertung laeuft nur an Tagen, an denen wirklich trainiert wurde."""
    if not last or stats._local_date(last["at"]) != stats.today():
        return None
    w = db.get_workout(last["id"])
    if not w:
        return None
    fb = session_feedback.evaluate(profile_id, w)
    return {"id": w["id"], "day_label": w.get("day_label") or "Training",
            "sets": sum(1 for s in w["sets"] if not s.get("warmup")), "score": fb["score"],
            "label": fb["label"]}


def build(profile_id):
    base = stats.today_view(profile_id)
    day_iso = stats.today().isoformat()
    checkin = readiness.get(profile_id, day_iso)
    level_key = checkin["level"]["key"] if checkin else None

    split = db.active_split(profile_id) if base.get("split") else None
    raw_day = None
    if split and base.get("day"):
        raw_day = next((d for d in split["days"] if d["id"] == base["day"]["id"]), None)
    if raw_day:
        base["day"] = adjusted_day(profile_id, raw_day, checkin)

    # Muskelkater in Gruppen von heute -> anderen Tag ohne diese Gruppen anbieten
    sore = readiness.sore_groups_in(raw_day, checkin)
    alternative = None
    if sore and split:
        for d in split["days"]:
            if d["id"] != (raw_day or {}).get("id") and d["exercises"] and \
                    not ({e.get("muscle_group") for e in d["exercises"]} & set(sore)):
                alternative = {"id": d["id"], "label": d["label"]}
                break

    key = mobility.suggest(raw_day, level_key)
    profile = db.get_row("profiles", profile_id)
    day_nutrition = nutrition.day(profile_id, day_iso)
    kcal_day = day_nutrition["totals"]
    goal_info = nutrition.goals(profile)
    goals = goal_info["goals"]
    water_goal = nutrition.water_goals(profile)["goal"]
    ach = activity.achievements(profile_id)
    done_mobility = db.one("SELECT 1 AS x FROM mobility_log WHERE profile_id=? AND date=? AND routine=?",
                           (profile_id, day_iso, key))
    trained_today = any(w["active"] and w["today"] for w in ach["week"])
    return {
        **base,
        "date": day_iso,
        "today_workout": today_workout(profile_id, base.get("last_workout")),
        "checkin": checkin,
        "sore_today": sore,
        "alternative": alternative,
        "mobility": {**{k: v for k, v in mobility.routine(key).items() if k != "moves"},
                     "done": bool(done_mobility)},
        "streak": {"daily": ach["daily_streak"], "weekly": ach["weekly_streak"],
                   "week": ach["week"], "trained_today": trained_today},
        # Was fuer Kalorien-/Trinkziel noch fehlt – Heute zeigt dann "Profil vervollstaendigen".
        "profile_missing": goal_info["missing"],
        "nutrition": {"totals": kcal_day, "goals": goals,
                     "water_ml": day_nutrition["water_ml"], "water_goal": water_goal},
    }
