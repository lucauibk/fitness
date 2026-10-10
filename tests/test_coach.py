"""Coach-Hinweise: Claude-Antworten werden streng geprueft, bevor sie in der App landen,
und der Lauf schreibt nur in coach_notes – nie in den Plan."""
import json
import os
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app import coach, db, main
from app.planner import blocks
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
    return ex, db.get_row("exercises", ex)["name"], split


def _note(profile_id, title, status="open", days_ago=0):
    with db.connect() as c:
        return c.execute("INSERT INTO coach_notes (profile_id, date, category, title, text, status)"
                         " VALUES (?,?,?,?,?,?)",
                         (profile_id, (today() - timedelta(days=days_ago)).isoformat(), "plan",
                          title, "t", status)).lastrowid


def _answer(*items, erledigt=()):
    body = json.dumps({"notes": list(items), "erledigt": list(erledigt)}, ensure_ascii=False)
    return f"<analyse>\n- Woche 1 [noch wenig Daten], {{kein JSON}}\n</analyse>\n<hinweise>{body}</hinweise>"


def test_parse_maps_exercise_and_clips(profile_id):
    ex, name, _ = _plan(profile_id)
    r = coach.parse(_answer({"category": "plan", "title": "T" * 100, "text": "x " * 300,
                             "exercise": name.upper()}), profile_id)
    assert len(r["notes"]) == 1
    assert r["notes"][0]["exercise_id"] == ex
    assert len(r["notes"][0]["title"]) <= coach.MAX_TITLE
    assert len(r["notes"][0]["text"]) <= coach.MAX_TEXT


def test_parse_drops_invalid_unknown_and_duplicates(profile_id):
    _plan(profile_id)
    _note(profile_id, "Schon da")
    r = coach.parse(_answer(
        {"category": "ernaehrung", "title": "Falsche Kategorie", "text": "x"},
        {"category": "plan", "title": "", "text": "ohne Titel"},
        {"category": "plan", "title": "schon da", "text": "Dublette eines offenen Hinweises"},
        {"category": "progress", "title": "Neu", "text": "ok", "exercise": "Gibt es nicht"},
        {"category": "progress", "title": "Neu", "text": "doppelt in derselben Antwort"},
        "kein Objekt"), profile_id)
    assert [(n["title"], n["exercise_id"]) for n in r["notes"]] == [("Neu", None)]


def test_parse_caps_count(profile_id):
    _plan(profile_id)
    r = coach.parse(_answer(*({"category": "plan", "title": f"N{i}", "text": "t"} for i in range(6))), profile_id)
    assert len(r["notes"]) == coach.MAX_NOTES


def test_parse_resolved_only_own_open_notes(profile_id):
    mine = _note(profile_id, "Alt")
    done = _note(profile_id, "Schon weg", status="dismissed")
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    foreign = _note(other, "Fremd")
    r = coach.parse(_answer(erledigt=[mine, done, foreign, 999999, "x"]), profile_id)
    assert r["resolved"] == [mine]
    coach.resolve(profile_id, r["resolved"])
    assert coach.open_notes(profile_id) == []
    assert db.get_row("coach_notes", mine)["status"] == "resolved"
    assert db.get_row("coach_notes", foreign)["status"] == "open"


def test_resolved_title_may_be_reused(profile_id):
    old = _note(profile_id, "Rumpf ergänzen")
    r = coach.parse(_answer({"category": "plan", "title": "Rumpf ergänzen", "text": "neu formuliert"},
                            erledigt=[old]), profile_id)
    assert [n["title"] for n in r["notes"]] == ["Rumpf ergänzen"]


def test_parse_accepts_plain_list_and_empty(profile_id):
    r = coach.parse('[{"category": "plan", "title": "A", "text": "b"}]', profile_id)
    assert [n["title"] for n in r["notes"]] == ["A"]
    assert coach.parse('<hinweise>{"notes": [], "erledigt": []}</hinweise>', profile_id) == \
        {"notes": [], "resolved": []}


@pytest.mark.parametrize("raw", ["", "Keine Ideen heute.", "<hinweise>kaputt</hinweise>",
                                 '<hinweise>{"notes": "x"}</hinweise>'])
def test_parse_rejects_non_json(profile_id, raw):
    with pytest.raises(ValueError):
        coach.parse(raw, profile_id)


def test_expire_old_open_notes(profile_id):
    old = _note(profile_id, "Alt", days_ago=coach.EXPIRE_DAYS + 1)
    _note(profile_id, "Neu")
    coach.expire(profile_id)
    assert [n["title"] for n in coach.open_notes(profile_id)] == ["Neu"]
    assert db.get_row("coach_notes", old)["status"] == "expired"


def test_context_block_and_history(profile_id):
    _, name, split = _plan(profile_id)
    blocks.create(profile_id, split, "aufbau", "fortgeschritten")
    _note(profile_id, "Offen")
    _note(profile_id, "Weggeklickt", status="dismissed")
    _note(profile_id, "Abgelaufen", status="expired")
    _note(profile_id, "Erledigt", status="resolved")
    ctx = coach.context(profile_id)
    assert ctx["block"]["woche"] == 1 and ctx["block"]["einheiten_seit_start"] == 0
    assert ctx["plan"]["tage"][0]["uebungen"][0]["name"] == name
    assert ctx["plan"]["tage"][0]["uebungen"][0]["saetze_diese_woche"] >= 2
    assert [n["title"] for n in ctx["offene_hinweise"]] == ["Offen"]
    assert ctx["weggeklickt"] == ["Weggeklickt"]
    assert ctx["tage_seit_letzter_einheit"] is None
    assert "Offen" in coach.prompt(ctx)


def test_context_leaves_plan_untouched(profile_id):
    _, _, split = _plan(profile_id)
    blocks.create(profile_id, split, "aufbau", "fortgeschritten")
    tables = ("splits", "split_days", "split_exercises", "workouts", "sets", "blocks", "plan_events")

    def snapshot():
        return {t: db.rows(f"SELECT * FROM {t} ORDER BY id") for t in tables}

    before = snapshot()
    coach.context(profile_id)
    r = coach.parse(_answer({"category": "plan", "title": "X", "text": "y"}), profile_id)
    coach.store(profile_id, r["notes"])
    assert snapshot() == before


def test_context_without_plan(profile_id):
    assert coach.context(profile_id) is None


def test_api_list_and_dismiss(profile_id):
    _note(profile_id, "Weg damit")
    note = main.api_coach_notes(profile_id)[0]
    assert note["title"] == "Weg damit"
    assert main.api_coach_dismiss(note["id"], profile_id) == {"ok": True}
    assert main.api_coach_notes(profile_id) == []


def test_api_dismiss_foreign_profile(profile_id):
    note = _note(profile_id, "Meins")
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    with pytest.raises(HTTPException) as e:
        main.api_coach_dismiss(note, other)
    assert e.value.status_code == 403
    assert coach.open_notes(profile_id)
