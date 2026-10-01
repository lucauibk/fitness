"""Adaptiver Wochenplan: abgehakt wird, was trainiert wurde – nicht, was am Wochentag stand."""
from datetime import date, datetime, timedelta

from app import db, stats

MONDAY = date(2026, 9, 28)   # ein Montag


def _split(profile_id, plan):
    """plan: [(Label, Wochentag, [Muskelgruppen])]"""
    sp = db.insert_row("splits", {"profile_id": profile_id, "name": "Test", "active": 1})
    ids = {}
    for i, (label, wd, groups) in enumerate(plan):
        d = db.insert_row("split_days", {"split_id": sp["id"], "position": i, "label": label, "weekdays": str(wd)})
        ids[label] = d["id"]
        for j, g in enumerate(groups):
            ex = db.one("SELECT id FROM exercises WHERE muscle_group=? LIMIT 1", (g,))
            db.insert_row("split_exercises", {"split_day_id": d["id"], "exercise_id": ex["id"], "position": j,
                                              "target_sets": 3, "target_reps_min": 8, "target_reps_max": 12})
    return db.active_split(profile_id), ids


def _trained(profile_id, day_id, on):
    at = datetime(on.year, on.month, on.day, 18, 0).isoformat()
    db.insert_row("workouts", {"profile_id": profile_id, "split_day_id": day_id, "started_at": at, "finished_at": at})


PLAN = [("OK A", 0, ["brust"]), ("UK A", 2, ["beine"]), ("OK B", 4, ["ruecken"])]


def test_planned_day_is_fixed(profile_id):
    split, ids = _split(profile_id, PLAN)
    _trained(profile_id, ids["OK A"], MONDAY)
    day, fixed, note = stats.schedule_day(profile_id, split, MONDAY + timedelta(days=2))
    assert (day["label"], fixed, note) == ("UK A", True, None)


def test_other_body_half_is_counted_and_missing_day_comes_next(profile_id):
    split, ids = _split(profile_id, PLAN)
    _trained(profile_id, ids["OK A"], MONDAY)
    _trained(profile_id, ids["OK B"], MONDAY + timedelta(days=2))   # Mittwoch statt UK
    day, fixed, note = stats.schedule_day(profile_id, split, MONDAY + timedelta(days=3))   # Donnerstag
    assert (day["label"], fixed, note) == ("UK A", False, "nachgeholt")


def test_skipped_day_moves_to_next_day_without_preference(profile_id):
    split, ids = _split(profile_id, PLAN)
    # Montag ausgelassen, Dienstag ist kein Trainingstag -> OK A wird nachgeholt
    day, fixed, note = stats.schedule_day(profile_id, split, MONDAY + timedelta(days=1))
    assert (day["label"], fixed, note) == ("OK A", False, "nachgeholt")


def test_makeup_day_waits_when_yesterdays_muscles_are_not_recovered(profile_id):
    split, ids = _split(profile_id, [("UK A", 0, ["beine"]), ("UK B", 1, ["beine"]), ("OK", 3, ["brust"])])
    _trained(profile_id, ids["UK B"], MONDAY)       # Montag UK B statt UK A
    # Dienstag: UK A steht noch aus, aber die Beine sind von gestern müde -> kein Nachholen
    day, fixed, note = stats.schedule_day(profile_id, split, MONDAY + timedelta(days=1))
    assert note == "erholung" and not fixed
