from datetime import date, timedelta

from app.activity import _runs


def test_consecutive_days_count_as_one_run():
    d1 = date(2026, 1, 1)
    d2, d3 = d1 + timedelta(days=1), d1 + timedelta(days=2)
    best, current = _runs({d1, d2, d3}, planned=set(), start=d1, end=d3)
    assert best == 3
    assert current == 3


def test_planned_rest_day_does_not_break_streak():
    d1 = date(2026, 1, 5)   # Montag
    d3 = d1 + timedelta(days=2)   # Mittwoch
    planned = {d1.weekday(), d3.weekday()}   # Montag und Mittwoch trainiert, Dienstag frei
    best, current = _runs({d1, d3}, planned, d1, d3)
    assert current == 2


def test_unplanned_gap_breaks_the_streak():
    d1 = date(2026, 1, 5)
    d5 = d1 + timedelta(days=4)
    # Kein Plan: mehr als zwei Ruhetage am Stueck werden nicht toleriert.
    best, current = _runs({d1, d5}, planned=set(), start=d1, end=d5)
    assert current == 1


def test_bestleistung_hat_trend_der_letzten_einheiten(profile_id):
    from app import db, stats
    ex = db.one("SELECT id FROM exercises LIMIT 1")["id"]
    for i, weight in enumerate((40.0, 45.0, 50.0)):
        db.save_workout({"profile_id": profile_id, "started_at": f"2026-10-0{i + 1}T08:00:00+00:00",
                         "sets": [{"exercise_id": ex, "set_no": 1, "reps": 8, "weight": weight, "warmup": 0}]})
    best = next(b for b in stats.best_per_exercise(profile_id) if b["exercise_id"] == ex)
    assert len(best["trend"]) == 3 and best["trend"] == sorted(best["trend"])
