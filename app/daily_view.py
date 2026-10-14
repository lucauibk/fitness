"""Die Startseite "Heute" in einer Antwort: Plan, Check-in, angepasstes Training,
Mobility-Vorschlag, Wochen-Streak und Kalorienbilanz."""
import logging
from datetime import timedelta

from . import activity, coach, db, mobility, nutrition, readiness, session_review, stats

# "Heute" zeigt nur frische Plan-Aenderungen; die ganze Liste steht im Plan unter "Was sich geaendert hat"
EVENT_DAYS = 3

log = logging.getLogger(__name__)


def adjusted_day(profile_id, day, checkin):
    plan = stats.day_plan(profile_id, day)
    plan["exercises"] = readiness.adjust(plan["exercises"], checkin)
    return plan


def today_workout(profile_id, last):
    """Die heute schon gespeicherte Einheit fuer die Erledigt-Karte auf "Heute" – mit Kurzbilanz.
    Die Auswertung laeuft nur an Tagen, an denen wirklich trainiert wurde."""
    if not last or stats._local_date(last["at"]) != stats.today():
        return None
    w = db.get_workout(last["id"])
    if not w:
        return None
    try:
        facts = session_review.facts(profile_id, w)
    except Exception:   # noqa: BLE001 – die Karte ist Beiwerk, die Startseite darf daran nicht haengen
        log.exception("Auswertung fuer die Heute-Karte fehlgeschlagen (Einheit %s)", w["id"])
        return None
    return {"id": w["id"], "day_label": w.get("day_label") or "Training",
            "sets": facts["sets_done"], "headline": facts["headline"]}


def recent_events(events, today=None):
    since = ((today or stats.today()) - timedelta(days=EVENT_DAYS)).isoformat()
    return [e for e in events if (e.get("date") or "") >= since][:3]


def build(profile_id):
    block_info = None
    try:   # Wochen-Check: laeuft nur einmal pro Woche wirklich, sonst kaum Aufwand
        from .planner import autoregulate
        block_info = autoregulate.review(profile_id)
    except Exception:   # noqa: BLE001 – die Startseite darf daran nicht haengen
        log.exception("Wochen-Check fehlgeschlagen")
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
        "block": block_info["block"] if block_info else None,
        "plan_events": recent_events(block_info["events"]) if block_info else [],
        "coach_notes": coach.open_notes(profile_id),
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
                     "water_ml": day_nutrition["water_ml"], "water_goal": water_goal,
                      "burned_kcal": day_nutrition["burned_kcal"]},
    }
