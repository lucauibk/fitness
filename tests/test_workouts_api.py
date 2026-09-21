"""Speichern aus dem Ausgang: Eine Einheit kann Stunden unterwegs sein. Was sich in der
Zwischenzeit am Plan aendert, darf sie nicht mehr kosten – ein fremdes Profil aber schon."""
import os
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app import db, main
from app.routes import daily
from app.stats import today


def _exercise():
    with db.connect() as c:
        return c.execute("INSERT INTO exercises (name, muscle_group, unit, kind) VALUES (?,?,?,?)",
                         (f"Test-{os.urandom(4).hex()}", "brust", "kg", "compound")).lastrowid


def _day(profile_id):
    with db.connect() as c:
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at)"
                          " VALUES (?,?,1,?)", (profile_id, "Test", db.now())).lastrowid
        return c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,?)",
                         (split, "Push")).lastrowid


def _body(profile_id, sets, day_id=None, client_id=None):
    return main.WorkoutIn(profile_id=profile_id, split_day_id=day_id,
                          client_id=client_id or os.urandom(8).hex(), sets=sets)


def _set(exercise_id, no=1):
    return {"exercise_id": exercise_id, "set_no": no, "reps": 8, "weight": 60.0}


def test_geloeschter_tag_wird_ohne_bezug_gespeichert(profile_id):
    ex = _exercise()
    day = _day(profile_id)
    with db.connect() as c:
        c.execute("DELETE FROM split_days WHERE id=?", (day,))
    res = main.api_add_workout(_body(profile_id, [_set(ex)], day_id=day))
    assert res["created"]
    assert res["workout"]["split_day_id"] is None
    assert len(res["workout"]["sets"]) == 1


def test_fremder_tag_bleibt_verboten(profile_id):
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                          (f"Fremd-{os.urandom(4).hex()}", db.now())).lastrowid
    day = _day(other)
    with pytest.raises(HTTPException):
        main.api_add_workout(_body(profile_id, [_set(_exercise())], day_id=day))


def test_saetze_geloeschter_uebungen_fallen_weg(profile_id):
    keep, gone = _exercise(), _exercise()
    with db.connect() as c:
        c.execute("DELETE FROM exercises WHERE id=?", (gone,))
    res = main.api_add_workout(_body(profile_id, [_set(keep, 1), _set(gone, 2)]))
    assert [s["exercise_id"] for s in res["workout"]["sets"]] == [keep]


def test_nur_geloeschte_uebungen_wird_abgelehnt(profile_id):
    gone = _exercise()
    with db.connect() as c:
        c.execute("DELETE FROM exercises WHERE id=?", (gone,))
    with pytest.raises(HTTPException):
        main.api_add_workout(_body(profile_id, [_set(gone)]))


def test_gleiche_client_id_nur_einmal(profile_id):
    ex = _exercise()
    first = main.api_add_workout(_body(profile_id, [_set(ex)], client_id="doppelt-1"))
    again = main.api_add_workout(_body(profile_id, [_set(ex)], client_id="doppelt-1"))
    assert first["created"] and not again["created"]
    assert first["workout"]["id"] == again["workout"]["id"]


def test_nachgesendeter_checkin_landet_am_eigenen_tag(profile_id):
    gestern = (today() - timedelta(days=1)).isoformat()
    body = daily.CheckinIn(profile_id=profile_id, date=gestern, sleep=2, energy=2, stress=4)
    daily.post_checkin(body)
    assert daily.get_checkin(profile_id, gestern)["checkin"]["sleep"] == 2
    assert daily.get_checkin(profile_id, date.isoformat(today()))["checkin"] is None
