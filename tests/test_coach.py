"""Coach-Hinweise: Claude-Antworten werden streng geprueft, bevor sie in der App landen,
und der Lauf schreibt nur in coach_notes – nie in den Plan."""
import json
import os
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app import coach, db, main
from app.stats import today


def _plan(profile_id):
    with db.connect() as c:
        ex = c.execute("INSERT INTO exercises (name, muscle_group, unit, kind) VALUES (?,?,?,?)",
                       (f"Bank-{os.urandom(4).hex()}", "brust", "kg", "compound")).lastrowid
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "Test", db.now())).lastrowid
        day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,?)",
                        (split, "Push")).lastrowid
        c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, target_sets,"
                  " target_reps_min, target_reps_max) VALUES (?,?,0,3,8,12)", (day, ex))
    return ex, db.get_row("exercises", ex)["name"]


def _answer(*items):
    return json.dumps(list(items))


def test_parse_maps_exercise_and_clips(profile_id):
    ex, name = _plan(profile_id)
    notes = coach.parse(_answer({"category": "plan", "title": "T" * 100, "text": "x " * 300,
                                 "exercise": name.upper()}), profile_id)
    assert len(notes) == 1
    assert notes[0]["exercise_id"] == ex
    assert len(notes[0]["title"]) <= coach.MAX_TITLE
    assert len(notes[0]["text"]) <= coach.MAX_TEXT


def test_parse_drops_invalid_unknown_and_duplicates(profile_id):
    _plan(profile_id)
    coach.store(profile_id, [{"category": "plan", "title": "Schon da", "text": "a", "exercise_id": None}])
    raw = "Hier meine Vorschläge:\n" + _answer(
        {"category": "ernaehrung", "title": "Falsche Kategorie", "text": "x"},
        {"category": "plan", "title": "", "text": "ohne Titel"},
        {"category": "plan", "title": "schon da", "text": "Dublette eines offenen Hinweises"},
        {"category": "progress", "title": "Neu", "text": "ok", "exercise": "Gibt es nicht"},
        {"category": "progress", "title": "Neu", "text": "doppelt in derselben Antwort"},
        "kein Objekt")
    notes = coach.parse(raw, profile_id)
    assert [(n["title"], n["exercise_id"]) for n in notes] == [("Neu", None)]


def test_parse_caps_count(profile_id):
    _plan(profile_id)
    raw = _answer(*({"category": "plan", "title": f"N{i}", "text": "t"} for i in range(6)))
    assert len(coach.parse(raw, profile_id)) == coach.MAX_NOTES


@pytest.mark.parametrize("raw", ["", "Keine Ideen heute.", "[kaputt"])
def test_parse_rejects_non_json(profile_id, raw):
    with pytest.raises(ValueError):
        coach.parse(raw, profile_id)


def test_empty_list_is_fine(profile_id):
    assert coach.parse("[]", profile_id) == []


def test_expire_old_open_notes(profile_id):
    coach.store(profile_id, [{"category": "plan", "title": "Alt", "text": "t", "exercise_id": None}])
    old = (today() - timedelta(days=coach.EXPIRE_DAYS + 1)).isoformat()
    with db.connect() as c:
        c.execute("UPDATE coach_notes SET date=? WHERE profile_id=?", (old, profile_id))
    coach.store(profile_id, [{"category": "plan", "title": "Neu", "text": "t", "exercise_id": None}])
    coach.expire(profile_id)
    assert [n["title"] for n in coach.open_notes(profile_id)] == ["Neu"]


def test_context_leaves_plan_untouched(profile_id):
    _plan(profile_id)
    tables = ("splits", "split_days", "split_exercises", "workouts", "sets", "blocks", "plan_events")

    def snapshot():
        return {t: db.rows(f"SELECT * FROM {t} ORDER BY id") for t in tables}

    before = snapshot()
    ctx = coach.context(profile_id)
    coach.store(profile_id, coach.parse(_answer({"category": "plan", "title": "X", "text": "y"}), profile_id))
    assert ctx["plan"]["tage"][0]["uebungen"][0]["saetze"] == 3
    assert snapshot() == before


def test_context_without_plan(profile_id):
    assert coach.context(profile_id) is None


def test_api_list_and_dismiss(profile_id):
    coach.store(profile_id, [{"category": "progress", "title": "Weg damit", "text": "t", "exercise_id": None}])
    note = main.api_coach_notes(profile_id)[0]
    assert note["title"] == "Weg damit"
    assert main.api_coach_dismiss(note["id"], profile_id) == {"ok": True}
    assert main.api_coach_notes(profile_id) == []


def test_api_dismiss_foreign_profile(profile_id):
    coach.store(profile_id, [{"category": "plan", "title": "Meins", "text": "t", "exercise_id": None}])
    note = coach.open_notes(profile_id)[0]
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    with pytest.raises(HTTPException) as e:
        main.api_coach_dismiss(note["id"], other)
    assert e.value.status_code == 403
    assert coach.open_notes(profile_id)
