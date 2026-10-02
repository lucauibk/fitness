"""Plan speichern: Split, Tage, Uebungen und der zugehoerige Block in einem Rutsch."""
from datetime import date

from .. import db
from . import blocks


def save_plan(profile_id, plan, activate=True):
    """plan: Vorschau aus generate.generate (oder gleich geformt). -> gespeicherter Split."""
    with db.connect() as c:
        split_id = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,0,?)",
                             (profile_id, plan["name"].strip(), db.now())).lastrowid
        for pos, day in enumerate(plan["days"]):
            day_id = c.execute("INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                               (split_id, pos, day["label"], db.format_weekdays(day.get("weekdays")))).lastrowid
            for ex_pos, ex in enumerate(day["exercises"]):
                c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, target_sets,"
                          " base_sets, target_reps_min, target_reps_max, rest_seconds) VALUES (?,?,?,?,?,?,?,?)",
                          (day_id, ex["exercise_id"], ex_pos, ex["target_sets"], ex["target_sets"],
                           ex["target_reps_min"], ex["target_reps_max"], ex.get("rest_seconds")))
    if activate:
        db.activate_split(split_id, profile_id)
        profile = db.get_row("profiles", profile_id) or {}
        age = (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None
        blocks.create(profile_id, split_id, plan.get("goal") or profile.get("goal"),
                      plan.get("experience") or profile.get("experience"),
                      plan.get("focus") or db.parse_focus(profile.get("focus")), age)
    return db.split_detail(split_id)
