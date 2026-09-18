"""ai.validate_plan ist die Sicherheitsgrenze zwischen einer KI-Antwort und der
Datenbank - nichts, was die KI zurueckgibt, darf ungeprueft gespeichert werden."""
import pytest

from app import ai

CATALOG = [
    {"id": 1, "name": "Bankdrücken", "pattern": "h_push", "muscle_group": "brust", "unit": "kg"},
    {"id": 2, "name": "Kniebeuge", "pattern": "squat", "muscle_group": "beine", "unit": "kg"},
]
ORIGINAL = {"days": [{"label": "Tag 1", "weekdays": [0], "exercises": [
    {"exercise_id": 1, "target_sets": 3, "target_reps_min": 8, "target_reps_max": 12},
]}]}


def _candidate(exercise_id=1, sets=3, lo=8, hi=12, label="Tag 1"):
    return {"days": [{"label": label, "exercises": [
        {"exercise_id": exercise_id, "target_sets": sets, "target_reps_min": lo, "target_reps_max": hi},
    ]}]}


def test_accepts_a_valid_plan():
    out = ai.validate_plan(_candidate(), ORIGINAL, CATALOG)
    assert out["days"][0]["exercises"][0]["name"] == "Bankdrücken"


def test_rejects_exercise_outside_the_catalog():
    with pytest.raises(ValueError):
        ai.validate_plan(_candidate(exercise_id=999), ORIGINAL, CATALOG)


def test_rejects_absurd_set_count():
    with pytest.raises(ValueError):
        ai.validate_plan(_candidate(sets=99), ORIGINAL, CATALOG)


def test_rejects_inverted_rep_range():
    with pytest.raises(ValueError):
        ai.validate_plan(_candidate(lo=12, hi=8), ORIGINAL, CATALOG)


def test_rejects_renamed_day():
    with pytest.raises(ValueError):
        ai.validate_plan(_candidate(label="Anderer Tag"), ORIGINAL, CATALOG)


def test_rejects_extra_training_day():
    candidate = {"days": [
        {"label": "Tag 1", "exercises": [{"exercise_id": 1, "target_sets": 3,
                                          "target_reps_min": 8, "target_reps_max": 12}]},
        {"label": "Tag 2", "exercises": [{"exercise_id": 2, "target_sets": 3,
                                          "target_reps_min": 8, "target_reps_max": 12}]},
    ]}
    with pytest.raises(ValueError):
        ai.validate_plan(candidate, ORIGINAL, CATALOG)
