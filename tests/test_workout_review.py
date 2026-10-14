"""Coach-Text nach dem Training: Antwort prüfen, Ablauf pending -> done/error, Neustart nach
Korrektur, Aufräumen beim Löschen. Claude selbst wird nie aufgerufen (gestubbt)."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import claude_cli, db, workout_review
from app.main import app

client = TestClient(app)
GOOD = ('<auswertung>{"kurzurteil": "Kurz, aber stark.", "stark": ["Brustpresse 70×11"], '
        '"vergleiche": ["a", "b", "c", "d", "e"], "naechstes_mal": ["Arme am Freitag nachholen"]}</auswertung>')


@pytest.fixture
def claude(monkeypatch):
    """Ersetzt den Claude-Aufruf; `calls` sammelt die Prompts, `answer` steuert die Antwort."""
    state = {"calls": [], "answer": GOOD}

    def fake(text, system, model, timeout=300, attempts=2, pause=30):
        state["calls"].append(text)
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]
    monkeypatch.setattr(claude_cli, "ask_with_retry", fake)
    return state


def _exercise():
    with db.connect() as c:
        return c.execute("INSERT INTO exercises (name, muscle_group, unit, kind, pattern)"
                         " VALUES (?, 'brust', 'kg', 'compound', 'h_push')",
                         (f"Bank-{os.urandom(4).hex()}",)).lastrowid


def _body(profile_id, ex):
    start = datetime(2026, 4, 1, 17, 0, tzinfo=timezone.utc)
    return {"profile_id": profile_id, "client_id": os.urandom(8).hex(),
            "started_at": start.isoformat(), "finished_at": (start + timedelta(minutes=30)).isoformat(),
            "sets": [{"exercise_id": ex, "set_no": i, "reps": 10, "weight": 50, "rpe": 8} for i in (1, 2, 3)]}


def test_parse_kuerzt_und_begrenzt():
    out = workout_review.parse("Vorrede " + GOOD)
    assert out["kurzurteil"] == "Kurz, aber stark."
    assert len(out["vergleiche"]) == 4
    long = workout_review.parse(json.dumps({"kurzurteil": "x" * 500, "stark": [1, "ok"]}))
    assert len(long["kurzurteil"]) == workout_review.LIMITS["kurzurteil"] and long["stark"] == ["ok"]


@pytest.mark.parametrize("raw", ["kein json", "<auswertung>[]</auswertung>", '{"kurzurteil": "", "stark": []}'])
def test_parse_lehnt_unbrauchbares_ab(raw):
    with pytest.raises(ValueError):
        workout_review.parse(raw)


def test_speichern_startet_coach_im_hintergrund(profile_id, claude):
    res = client.post("/api/workouts", json=_body(profile_id, _exercise()))
    assert res.status_code == 200
    data = res.json()
    # Die Antwort kommt vor dem Hintergrundlauf: Zahlen da, Text noch ausstehend
    assert data["review"]["coach"]["status"] == "pending"
    assert data["review"]["facts"]["sets_done"] == 3
    wid = data["workout"]["id"]
    review = client.get(f"/api/workouts/{wid}/review", params={"profile": profile_id}).json()
    assert review["coach"]["status"] == "done"
    assert review["coach"]["body"]["naechstes_mal"] == ["Arme am Freitag nachholen"]
    assert len(claude["calls"]) == 1 and '"sets_done": 3' in claude["calls"][0]


def test_doppelt_gesendet_nur_ein_lauf(profile_id, claude):
    body = _body(profile_id, _exercise())
    client.post("/api/workouts", json=body)
    again = client.post("/api/workouts", json=body).json()
    assert again["created"] is False and again["review"] is None
    assert len(claude["calls"]) == 1


def test_fehler_landet_als_error(profile_id, claude):
    claude["answer"] = RuntimeError("claude Exit 1: overloaded")
    wid = client.post("/api/workouts", json=_body(profile_id, _exercise())).json()["workout"]["id"]
    review = workout_review.get(wid)
    assert review["status"] == "error" and "overloaded" in review["error"]


def test_korrektur_schreibt_neu(profile_id, claude):
    wid = client.post("/api/workouts", json=_body(profile_id, _exercise())).json()["workout"]["id"]
    set_id = db.get_workout(wid)["sets"][0]["id"]
    res = client.patch(f"/api/workouts/{wid}", params={"profile": profile_id},
                       json={"sets": [{"id": set_id, "reps": 12, "weight": 50}]})
    assert res.status_code == 200 and res.json()["review"]["coach"]["status"] == "pending"
    assert len(claude["calls"]) == 2 and workout_review.get(wid)["status"] == "done"


def test_alte_einheit_bekommt_text_beim_oeffnen_und_haengende_laeufe_starten_neu(profile_id, claude):
    ex = _exercise()
    workout, _ = db.save_workout(_body(profile_id, ex))       # ohne API: noch keine Rückmeldung
    client.get(f"/api/workouts/{workout['id']}", params={"profile": profile_id})
    assert workout_review.get(workout["id"])["status"] == "done"
    old = (datetime.now(timezone.utc) - timedelta(minutes=workout_review.STALE_MINUTES + 1)).isoformat()
    with db.connect() as c:
        c.execute("UPDATE workout_reviews SET status='pending', updated_at=? WHERE workout_id=?",
                  (old, workout["id"]))
    client.get(f"/api/workouts/{workout['id']}/review", params={"profile": profile_id})
    assert len(claude["calls"]) == 2 and workout_review.get(workout["id"])["status"] == "done"


def test_frischer_pending_lauf_wird_nicht_doppelt_gestartet(profile_id, claude):
    workout, _ = db.save_workout(_body(profile_id, _exercise()))
    workout_review.schedule(profile_id, workout["id"], None)
    client.get(f"/api/workouts/{workout['id']}/review", params={"profile": profile_id})
    assert claude["calls"] == []


def test_loeschen_raeumt_auf(profile_id, claude):
    wid = client.post("/api/workouts", json=_body(profile_id, _exercise())).json()["workout"]["id"]
    client.delete(f"/api/workouts/{wid}", params={"profile": profile_id})
    assert workout_review.get(wid) is None


def test_fremdes_profil_sieht_keine_auswertung(profile_id, claude):
    wid = client.post("/api/workouts", json=_body(profile_id, _exercise())).json()["workout"]["id"]
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    assert client.get(f"/api/workouts/{wid}/review", params={"profile": other}).status_code == 403


def test_prompt_enthaelt_ersatz_reihenfolge_und_woche(profile_id):
    ex = _exercise()
    workout, _ = db.save_workout(_body(profile_id, ex))
    ctx = workout_review.context(profile_id, db.get_workout(workout["id"]))
    text = workout_review.prompt(ctx)
    assert "substitute_for" in text and "position" in text and '"einheit"' in text
