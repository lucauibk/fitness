"""Lesehelfer ohne N+1: Einheiten-Liste und Split-Detail laden ihre Kinder in einer
Abfrage. Ergebnis muss Zeile fuer Zeile dem frueheren Laden je Eltern-Zeile entsprechen."""
import os
import sqlite3

import pytest

from app import db


def _exercise(c):
    return c.execute("INSERT INTO exercises (name, muscle_group, unit, kind) VALUES (?,?,?,?)",
                     (f"Test-{os.urandom(4).hex()}", "brust", "kg", "compound")).lastrowid


@pytest.fixture
def zaehler(monkeypatch):
    """Zaehlt geoeffnete Verbindungen und SELECT-Abfragen waehrend des Tests."""
    stand = {"verbindungen": 0, "selects": 0}
    echt = sqlite3.connect

    def connect(*a, **kw):
        conn = echt(*a, **kw)
        stand["verbindungen"] += 1
        conn.set_trace_callback(
            lambda sql: stand.__setitem__("selects", stand["selects"] + sql.lstrip().upper().startswith("SELECT")))
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", connect)
    stand["reset"] = lambda: stand.update(verbindungen=0, selects=0)
    return stand


def _split_mit_tagen(profile_id, tage=5, je_tag=6):
    with db.connect() as c:
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "Test", db.now())).lastrowid
        exs = [_exercise(c) for _ in range(je_tag)]
        for t in range(tage):
            # Position absichtlich rueckwaerts, damit die Sortierung geprueft wird
            day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,?,?)",
                            (split, tage - t, f"Tag {t}")).lastrowid
            for i, ex in enumerate(exs):
                c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position)"
                          " VALUES (?,?,?)", (day, ex, je_tag - i))
    # Ein leerer Tag darf nicht fehlen und bekommt eine leere Liste
    with db.connect() as c:
        c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,99,'Leer')", (split,))
    return split


def _einheiten(profile_id, anzahl=40, saetze=5):
    with db.connect() as c:
        ex = _exercise(c)
    for i in range(anzahl):
        db.save_workout({"profile_id": profile_id, "title": f"E{i}",
                         "started_at": f"2026-01-{1 + i % 28:02d}T10:00:00+00:00",
                         "sets": [{"exercise_id": ex, "set_no": n + 1, "reps": 8, "weight": 50 + n}
                                  for n in range(saetze)] if i % 7 else []})


def _alt_split_detail(split_id):
    """Fruehere Umsetzung (eine Abfrage je Tag) als Referenz."""
    split = db.get_row("splits", split_id)
    split["days"] = [db.day_out(d) for d in db.list_rows_where("split_days", "split_id", split_id, "position, id")]
    for day in split["days"]:
        day["exercises"] = db.rows(
            "SELECT se.*, e.name, e.muscle_group, e.unit, e.kind, e.pattern FROM split_exercises se"
            " JOIN exercises e ON e.id = se.exercise_id"
            " WHERE se.split_day_id=? ORDER BY se.position, se.id", (day["id"],))
    return split


def _alt_list_workouts(profile_id, limit):
    """Fruehere Umsetzung (eine Abfrage je Einheit) als Referenz."""
    out = db.rows("SELECT w.*, d.label AS day_label FROM workouts w"
                  " LEFT JOIN split_days d ON d.id = w.split_day_id WHERE w.profile_id=?"
                  " ORDER BY COALESCE(w.started_at, w.finished_at) DESC, w.id DESC LIMIT ?",
                  (profile_id, limit))
    for w in out:
        w["sets"] = db.rows("SELECT s.*, e.name AS exercise, e.unit FROM sets s"
                            " JOIN exercises e ON e.id = s.exercise_id"
                            " WHERE s.workout_id=? ORDER BY s.id", (w["id"],))
        if not w["day_label"]:
            w["day_label"] = w.get("title")
    return out


def test_split_detail_gleiches_ergebnis_mit_drei_abfragen(profile_id, zaehler):
    split = _split_mit_tagen(profile_id)
    zaehler["reset"]()
    neu = db.split_detail(split)
    assert zaehler["verbindungen"] == 1
    assert zaehler["selects"] == 3
    assert neu == _alt_split_detail(split)
    assert [d["label"] for d in neu["days"]][-1] == "Leer"
    assert neu["days"][-1]["exercises"] == []


def test_split_detail_unbekannt():
    assert db.split_detail(10 ** 9) is None


def test_list_workouts_gleiches_ergebnis_mit_zwei_abfragen(profile_id, zaehler):
    _einheiten(profile_id)
    for limit in (1, 30, 200):
        zaehler["reset"]()
        neu = db.list_workouts(profile_id, limit)
        assert zaehler["verbindungen"] == 1
        assert zaehler["selects"] == 2
        assert neu == _alt_list_workouts(profile_id, limit)
    assert any(w["sets"] == [] for w in neu)


def test_list_workouts_ohne_einheiten(profile_id, zaehler):
    zaehler["reset"]()
    assert db.list_workouts(profile_id) == []
    assert zaehler["selects"] == 1
