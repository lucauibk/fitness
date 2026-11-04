"""Übungs-Details (Bibliothek und „Anleitung“ in der laufenden Einheit): das Sheet zeigt
Anleitung, Fehler und die eigene Bestleistung – nie die eines anderen Profils."""
import os

from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)


def _exercise():
    with db.connect() as c:
        return c.execute(
            "INSERT INTO exercises (name, muscle_group, unit, kind, instructions, mistakes, muscles_primary)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"Test-{os.urandom(4).hex()}", "brust", "kg", "compound",
             "Schulterblätter zusammen\n\nStange zur Brust", "Hohlkreuz", "chest,triceps")).lastrowid


def _workout(profile_id, exercise_id, weight):
    with db.connect() as c:
        w = c.execute("INSERT INTO workouts (profile_id, title, started_at, finished_at) VALUES (?,?,?,?)",
                      (profile_id, "Push", db.now(), db.now())).lastrowid
        c.execute("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight) VALUES (?,?,1,8,?)",
                  (w, exercise_id, weight))


def _other_profile():
    with db.connect() as c:
        return c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                         (f"Test-{os.urandom(4).hex()}", db.now())).lastrowid


def test_details_mit_anleitung_und_eigener_bestleistung(profile_id):
    ex = _exercise()
    _workout(profile_id, ex, 60.0)
    _workout(_other_profile(), ex, 120.0)

    r = client.get(f"/api/exercises/{ex}", params={"profile": profile_id})
    assert r.status_code == 200
    d = r.json()
    # Leerzeilen fallen weg, jede Zeile ist ein Schritt
    assert d["instructions"] == ["Schulterblätter zusammen", "Stange zur Brust"]
    assert d["mistakes"] == ["Hohlkreuz"]
    assert d["muscles_primary"] == ["chest", "triceps"]
    assert d["video_url"].startswith("https://www.youtube.com/results?search_query=")
    assert d["best"]["weight"] == 60.0          # nicht die 120 kg des anderen Profils


def test_ohne_eigene_saetze_keine_bestleistung(profile_id):
    ex = _exercise()
    _workout(_other_profile(), ex, 100.0)
    d = client.get(f"/api/exercises/{ex}", params={"profile": profile_id}).json()
    assert d["best"] is None
    assert d["history"] == []


def test_unbekannte_uebung_404(profile_id):
    assert client.get("/api/exercises/999999", params={"profile": profile_id}).status_code == 404
