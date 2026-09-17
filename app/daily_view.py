"""Die Startseite "Heute" in einer Antwort: Plan, Check-in, angepasstes Training,
Mobility-Vorschlag, Wochen-Streak und Kalorienbilanz."""
from . import activity, db, mobility, nutrition, readiness, stats


def adjusted_day(profile_id, day, checkin):
    plan = stats.day_plan(profile_id, day)
    plan["exercises"] = readiness.adjust(plan["exercises"], checkin)
    return plan


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
    kcal_day = nutrition.day(profile_id, day_iso)["totals"]
    goals = nutrition.goals(profile)["goals"]
    ach = activity.achievements(profile_id)
    done_mobility = db.one("SELECT 1 AS x FROM mobility_log WHERE profile_id=? AND date=? AND routine=?",
                           (profile_id, day_iso, key))
    trained_today = any(w["active"] and w["today"] for w in ach["week"])
    return {
        **base,
        "date": day_iso,
        "checkin": checkin,
        "sore_today": sore,
        "alternative": alternative,
        "mobility": {**{k: v for k, v in mobility.routine(key).items() if k != "moves"},
                     "done": bool(done_mobility)},
        "streak": {"daily": ach["daily_streak"], "weekly": ach["weekly_streak"],
                   "week": ach["week"], "trained_today": trained_today},
        "nutrition": {"totals": kcal_day, "goals": goals},
    }
