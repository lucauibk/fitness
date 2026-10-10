"""HTTP-Ebene des Plan-Erstellers: Validierung, Fehlercodes und dass der angezeigte Plan der erzeugte ist."""
import itertools
import threading

import pytest
from fastapi.testclient import TestClient

from app import db, splits
from app.main import app

client = TestClient(app)


def post(pid, body):
    return client.post("/api/planner/create", params={"profile": pid}, json=body)


def get(pid, **params):
    return client.get("/api/planner/suggest", params={"profile": pid, **params})


def labels(pid):
    return [d["label"] for d in db.active_split(pid)["days"]]


def key_labels(key):
    return list(dict.fromkeys(splits.find(key)["sequence"]))


def test_validation_returns_proper_errors(profile_id):
    """Die App meldet Validierungsfehler einheitlich als 400 (siehe RequestValidationError-Handler)."""
    assert post(profile_id, {"days": 3, "goal": "bogus"}).status_code == 400
    assert post(profile_id, {"days": 3, "experience": "bogus"}).status_code == 400
    assert post(profile_id, {"days": 7}).status_code == 400
    assert post(profile_id, {"days": 3, "available": [9]}).status_code == 400
    assert post(profile_id, {"days": 3, "focus": ["brust", "po", "arme", "rumpf"]}).status_code == 400
    assert post(profile_id, {"days": 3, "available": [3]}).status_code == 400            # ein freier Tag reicht nicht
    assert post(profile_id, {"days": 3, "split": "ppl6"}).status_code == 400             # passt nicht zu 3 Einheiten
    assert get(profile_id, days=9).status_code == 400
    assert get(profile_id, available="0,1,2,3,4,5,6", days=3).status_code == 200         # sieben freie Tage: kein 500
    assert client.post("/api/planner/create", json={"days": 3}).status_code == 400       # kein Profil


def test_failed_create_leaves_profile_and_plans_untouched(profile_id):
    db.update_row("profiles", profile_id, {"goal": "kraft", "limitations": "Knie"})
    before = db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"]
    assert post(profile_id, {"days": 3, "goal": "aufbau", "limitations": "Schulter", "split": "ppl6"}).status_code == 400
    prof = db.get_row("profiles", profile_id)
    assert prof["goal"] == "kraft" and prof["limitations"] == "Knie"
    assert db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"] == before


@pytest.mark.parametrize("days", [2, 3, 4, 5, 6])
def test_what_is_shown_as_recommended_is_what_gets_created(profile_id, days):
    """Fuer jede Kombination: der vorgewaehlte (✓) Split ist der erzeugte – auch ohne Tipp auf einen Chip."""
    for exp, goal, focus in itertools.product(("einsteiger", "fortgeschritten", "erfahren"),
                                              ("aufbau", "kraft"), ("", "po", "brust")):
        sug = get(profile_id, days=days, experience=exp, goal=goal, focus=focus).json()
        shown = next(o["key"] for o in sug["splits"] if o["recommended"])
        body = {"days": days, "experience": exp, "goal": goal, "focus": [focus] if focus else []}
        assert post(profile_id, body).status_code == 200
        assert labels(profile_id) == key_labels(shown), (days, exp, goal, focus, shown)


def test_chosen_split_is_created_exactly(profile_id):
    for key in splits.CATALOG[3]:
        if splits.find(key).get("min_experience"):
            continue
        assert post(profile_id, {"days": 3, "experience": "erfahren", "split": key}).status_code == 200
        assert labels(profile_id) == key_labels(key), key


def test_same_client_id_creates_only_one_plan_even_in_parallel(profile_id):
    before = db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"]
    results = []
    body = {"days": 3, "client_id": "tap-1"}
    threads = [threading.Thread(target=lambda: results.append(post(profile_id, body).json())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({r["split"]["id"] for r in results}) == 1
    assert db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"] == before + 1
    active_blocks = db.one("SELECT COUNT(*) AS n FROM blocks WHERE profile_id=? AND active=1", (profile_id,))["n"]
    assert active_blocks == 1


def test_choices_are_saved_to_the_profile_in_one_step(profile_id):
    res = post(profile_id, {"days": 3, "available": [2, 3, 4, 5, 6], "goal": "kraft", "experience": "fortgeschritten",
                            "focus": ["po"], "limitations": "Knie"})
    assert res.status_code == 200
    p = db.profile_out(db.get_row("profiles", profile_id))
    assert (p["goal"], p["experience"], p["focus"], p["limitations"]) == ("kraft", "fortgeschritten", ["po"], "Knie")
    assert p["available_weekdays"] == [2, 3, 4, 5, 6] and p["train_weekdays"] == [2, 4, 6]


def test_swapping_an_exercise_by_hand_clears_the_generated_mark(profile_id):
    """Selbst gewaehlte Uebungen tauscht die App am Blockende nicht zurueck."""
    assert post(profile_id, {"days": 3}).status_code == 200
    item = db.active_split(profile_id)["days"][0]["exercises"][-1]
    assert db.get_row("split_exercises", item["id"])["generated"] == 1
    other = db.one("SELECT id FROM exercises WHERE pattern=? AND id<>? AND archived=0",
                   (item["pattern"], item["exercise_id"]))
    r = client.patch(f"/api/split-exercises/{item['id']}", params={"profile": profile_id},
                     json={"exercise_id": other["id"]})
    assert r.status_code == 200
    assert db.get_row("split_exercises", item["id"])["generated"] is None
    r = client.patch(f"/api/split-exercises/{item['id']}", params={"profile": profile_id}, json={"notes": "x"})
    assert r.status_code == 200 and db.get_row("split_exercises", item["id"])["generated"] is None
