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
