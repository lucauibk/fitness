"""Wochenvolumen und Haeufigkeit zaehlen nach Ortszeit – auch am Rand des Zeitfensters.

Gespeichert wird in UTC (`toISOString()` im Browser), das Fenster beginnt aber an einem
oertlichen Montag. Eine Einheit am Montag kurz nach Mitternacht (Wien) liegt in UTC noch
am Sonntag und darf trotzdem nicht aus der aeltesten Woche fallen."""
from datetime import date

from app import db, stats

TODAY = date(2026, 10, 8)            # Donnerstag
FIRST_MONDAY = "2026-07-20"          # Beginn des 12-Wochen-Fensters
JUST_AFTER_MIDNIGHT = "2026-07-19T22:30:00.000Z"   # = 20.07. 00:30 in Wien (MESZ)


def _workout(profile_id, at):
    ex = db.one("SELECT id FROM exercises ORDER BY id LIMIT 1")
    db.save_workout({"profile_id": profile_id, "started_at": at, "finished_at": at,
                     "sets": [{"exercise_id": ex["id"], "set_no": 1, "reps": 8, "weight": 50}]})


def test_weekly_volume_keeps_workout_after_local_midnight(profile_id, monkeypatch):
    monkeypatch.setattr(stats, "today", lambda: TODAY)
    _workout(profile_id, JUST_AFTER_MIDNIGHT)
    weeks = stats.weekly_volume(profile_id)
    assert weeks[0]["week"] == FIRST_MONDAY
    assert weeks[0]["sets"] == 1


def test_frequency_keeps_workout_after_local_midnight(profile_id, monkeypatch):
    monkeypatch.setattr(stats, "today", lambda: TODAY)
    _workout(profile_id, JUST_AFTER_MIDNIGHT)
    freq = stats.frequency(profile_id)
    assert freq["weeks"][0] == {"week": FIRST_MONDAY, "count": 1}
    assert freq["days"] == [FIRST_MONDAY]


def test_window_does_not_pull_in_the_week_before(profile_id, monkeypatch):
    monkeypatch.setattr(stats, "today", lambda: TODAY)
    _workout(profile_id, "2026-07-19T12:00:00.000Z")   # Sonntag Mittag – Woche davor
    assert all(w["sets"] == 0 for w in stats.weekly_volume(profile_id))
    freq = stats.frequency(profile_id)
    assert all(w["count"] == 0 for w in freq["weeks"])
    assert freq["days"] == []
