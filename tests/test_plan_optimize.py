"""Plan verbessern (analysis.py + plan_structure.py) an Plaenen, wie sie wirklich gebaut werden:
selbst gebaute OK/UK-Plaene, ein Tag an zwei Wochentagen, Uebungen ohne Bewegungsmuster, doppelt belegte Tage."""
import pytest
from fastapi.testclient import TestClient

from app import analysis, db
from app.main import app
from app.planner import blocks

client = TestClient(app)

LUCA = [("OK", "2,4", [("Bankdrücken", 3), ("Latzug", 2), ("Schrägbankdrücken Kurzhantel", 3), ("Rudern Kabel", 3),
                       ("Butterfly", 2), ("Klimmzüge", 2), ("Seitheben", 2), ("SZ-Curls", 2),
                       ("Trizepsdrücken Kabel", 2), ("Hammercurls", 2), ("Trizeps über Kopf", 2),
                       ("Reverse Butterfly", 2)]),
        ("UK", "3", [("Beinpresse", 3), ("Rumänisches Kreuzheben", 3), ("Beinstrecker", 2), ("Beinbeuger", 2),
                     ("Wadenheben", 3), ("Beinheben hängend", 3), ("Crunches", 3)])]
OKUK2 = [("OK", "0", [("Bankdrücken", 3), ("Latzug", 3), ("Rudern Kabel", 3), ("Seitheben", 3), ("SZ-Curls", 2),
                      ("Trizepsdrücken Kabel", 2)]),
         ("UK", "3", [("Beinpresse", 3), ("Rumänisches Kreuzheben", 3), ("Beinbeuger", 2), ("Wadenheben", 3),
                      ("Crunches", 2)])]
TINA = [("UK", "1,4", [("Hip Thrust", 3), ("Rumänisches Kreuzheben", 3), ("Adduktoren (eigene)", 6),
                       ("Beinbeuger", 3)]),
        ("OK", "4", [("Bankdrücken", 3), ("Latzug", 3), ("Schrägbankdrücken Kurzhantel", 3), ("Rudern Kabel", 3),
                     ("Face Pulls", 3), ("Seitheben", 3), ("Hammercurls", 3), ("Trizepsdrücken Kabel", 3)])]


def _exercise(name):
    row = db.one("SELECT id FROM exercises WHERE name=? AND archived=0", (name,))
    if row:
        return row["id"]
    return db.insert_row("exercises", {"name": name, "muscle_group": "beine", "unit": "kg"})["id"]   # ohne Muster


def build(profile_id, days, goal="aufbau", experience="fortgeschritten", focus=None, available=None):
    ids = {n: _exercise(n) for _, _, exs in days for n, _ in exs}
    db.update_row("profiles", profile_id, {"goal": goal, "experience": experience, "focus": focus,
                                           "available_weekdays": available})
    with db.connect() as c:
        split_id = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                             (profile_id, "Eigener Plan", db.now())).lastrowid
        for pos, (label, wds, exs) in enumerate(days):
            day_id = c.execute("INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                               (split_id, pos, label, wds)).lastrowid
            for i, (name, sets) in enumerate(exs):
                c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, target_sets,"
                          " target_reps_min, target_reps_max) VALUES (?,?,?,?,8,12)", (day_id, ids[name], i, sets))
    blocks.ensure(profile_id)
    return split_id


def apply(profile_id, action):
    return client.post("/api/plan/apply", params={"profile": profile_id}, json={"action": action})


def week1(profile_id):
    return {v["group"]: v["actual"] for v in analysis.analyze(profile_id)["volume"]}


# ------------------------------------------------------------------ Volumen und Kapazitaet
def test_volume_matches_block_week_one(profile_id):
    """Der Plan ist Woche 1 und wird mit Woche 1 der Block-Ziele verglichen – nicht mit der Spitzenwoche."""
    build(profile_id, OKUK2, focus="brust")
    res = analysis.analyze(profile_id)
    brust = next(v for v in res["volume"] if v["group"] == "brust")
    assert brust["actual"] == 3 and brust["target"] < brust["peak_target"]
    sets = next(s for s in res["suggestions"] if s["action"] and s["action"].get("type") == "sets"
                and "Bankdrücken" in s["text"])
    assert apply(profile_id, sets["action"]).status_code == 200
    assert week1(profile_id)["brust"] == sets["action"]["target_sets"]


def test_full_days_get_no_more_sets(profile_id):
    """Lucas OK-Tag (12 Uebungen, im Block bis 40 Saetze): dort wird nichts mehr erhoeht oder ergaenzt."""
    build(profile_id, LUCA, focus="brust,arme", available="2,3,4,5,6")
    res = analysis.analyze(profile_id)
    ok_items = {e["id"] for d in db.active_split(profile_id)["days"] if d["label"] == "OK" for e in d["exercises"]}
    for s in res["suggestions"]:
        a = s["action"] or {}
        if a.get("type") == "sets":
            old = db.get_row("split_exercises", a["item_id"])
            assert not (a["item_id"] in ok_items and a["target_sets"] > old["target_sets"]), s["text"]
        assert a.get("type") != "add" or a["day_id"] not in {d["id"] for d in db.active_split(profile_id)["days"]
                                                              if d["label"] == "OK"}


def test_push_pull_counts_rear_delts_as_pull(profile_id):
    build(profile_id, LUCA, focus="brust,arme", available="2,3,4,5,6")
    texts = [s["text"] for s in analysis.analyze(profile_id)["suggestions"]]
    assert not any("Zugarbeit" in t for t in texts)


def test_double_booked_weekday_and_unpatterned_exercise(profile_id):
    build(profile_id, TINA, goal="abnehmen", focus="po,rumpf")
    res = analysis.analyze(profile_id)
    kinds = [(s["action"] or {}).get("type") for s in res["suggestions"]]
    assert "open_day" in kinds and "open_exercise" in kinds
    # Uebungen ohne Muster zaehlen nicht mit und werden nicht gekuerzt
    assert not any("Adduktoren" in s["text"] and "reduzieren" in s["text"] for s in res["suggestions"])


# ------------------------------------------------------------------ Struktur: Tag ergaenzen
@pytest.mark.parametrize("focus,new_kind", [("brust", "OK"), ("po", "UK")])
def test_two_day_okuk_gets_third_day(profile_id, focus, new_kind):
    """OK/UK mit zwei Einheiten: OK A · UK · OK B bei Oberkoerper-Fokus, UK A · OK · UK B bei Po-Fokus."""
    build(profile_id, OKUK2, focus=focus)
    before = week1(profile_id)
    st = analysis.analyze(profile_id)["structure"]
    assert st and st["type"] == "add_day"
    new = next(d for d in st["days"] if d["new"])
    assert new["label"] == f"{new_kind} B"
    if focus == "po":
        assert any("Hip Thrust" in e["name"] or "Kickback" in e["name"] for e in new["exercises"]), new
    assert apply(profile_id, st["action"]).status_code == 200
    split = db.active_split(profile_id)
    labels = [d["label"] for d in split["days"]]
    assert f"{new_kind} A" in labels and f"{new_kind} B" in labels and len(labels) == 3
    after = week1(profile_id)
    for g, v in after.items():             # umverteilt, nicht verdoppelt: nie ueber Erholungsgrenze, kaum ueber Ziel
        assert v <= max(before.get(g, 0), analysis.context(db.profile_out(db.get_row("profiles", profile_id)),
                                                           split)["target"][g]) + 2, g
    prof = db.get_row("profiles", profile_id)
    assert prof["days_per_week"] == 3      # Profil kennt die neuen Trainingstage


def test_long_day_moves_exercises_to_new_day(profile_id):
    """Lucas Plan: der OK-Tag waechst auf 40 Saetze – ein vierter Tag uebernimmt Uebungen, das Volumen bleibt."""
    build(profile_id, LUCA, focus="brust,arme", available="2,3,4,5,6")
    before = week1(profile_id)
    ctx = analysis.context(db.profile_out(db.get_row("profiles", profile_id)), db.active_split(profile_id))
    goal4 = analysis.week1_targets(ctx, 4)
    st = analysis.analyze(profile_id)["structure"]
    assert st and st["type"] == "add_day"
    assert st["days"][0]["weekdays"] == [6]                 # Sonntag: kein Brust/Ruecken-Tag direkt davor
    assert apply(profile_id, st["action"]).status_code == 200
    after = week1(profile_id)
    for g in after:                                          # der Plan waechst hoechstens bis zum Ziel
        assert after[g] <= max(before.get(g, 0), goal4[g]), g
    res = analysis.analyze(profile_id)
    loads = analysis.measure(db.active_split(profile_id)["days"], analysis.context(
        db.profile_out(db.get_row("profiles", profile_id)), db.active_split(profile_id))["ratios"])["days"]
    assert max(load["peak"] for load in loads) <= analysis.SESSION_CAP
    assert not res["structure"] or res["structure"]["type"] != "add_day"


def test_no_extra_day_without_need(profile_id):
    """Ein ausgewogener Plan ohne Luecken bekommt keinen zusaetzlichen Tag, nur weil mehr Tage moeglich waeren."""
    build(profile_id, [("GK A", "0", [("Bankdrücken", 3), ("Latzug", 3), ("Beinpresse", 3),
                                      ("Rumänisches Kreuzheben", 3)]),
                       ("GK B", "3", [("Schrägbankdrücken Kurzhantel", 3), ("Rudern Kabel", 3), ("Beinpresse", 3),
                                      ("Beinbeuger", 3)])], experience="einsteiger", goal="fitness")
    st = analysis.analyze(profile_id)["structure"]
    assert not st or st["type"] != "add_day", st and st["title"]


# ------------------------------------------------------------------ Struktur: A/B
def test_repeated_day_split_into_ab(profile_id):
    build(profile_id, LUCA, focus="brust,arme", available="2,3,4")          # kein freier Tag: A/B statt Zusatztag
    before = week1(profile_id)
    st = analysis.analyze(profile_id)["structure"]
    assert st and st["type"] == "split_ab"
    assert apply(profile_id, st["action"]).status_code == 200
    days = {d["label"]: d for d in db.active_split(profile_id)["days"]}
    assert days["OK A"]["weekdays"] == [2] and days["OK B"]["weekdays"] == [4]
    assert len(days["OK A"]["exercises"]) < 12 and len(days["OK B"]["exercises"]) < 12
    after = week1(profile_id)
    assert all(abs(after[g] - before[g]) <= 1 for g in before)             # gleiches Volumen


# ------------------------------------------------------------------ Umsetzen
def test_restructure_is_atomic_and_checks_state(profile_id):
    build(profile_id, OKUK2, focus="brust")
    st = analysis.analyze(profile_id)["structure"]
    snapshot = db.active_split(profile_id)
    broken = {**st["action"], "ops": st["action"]["ops"] + [{"op": "sets", "item_id": 999999, "target_sets": 3}]}
    assert apply(profile_id, broken).status_code == 400
    assert analysis.fingerprint(db.active_split(profile_id)) == analysis.fingerprint(snapshot)   # nichts geaendert

    item = snapshot["days"][0]["exercises"][0]
    db.update_row("split_exercises", item["id"], {"target_sets": 4})
    res = apply(profile_id, st["action"])
    assert res.status_code == 400 and "geändert" in res.json()["detail"]


def test_restructure_rejects_other_profile_and_taken_weekday(profile_id):
    build(profile_id, OKUK2, focus="brust")
    st = analysis.analyze(profile_id)["structure"]
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES ('Fremd', ?)", (db.now(),)).lastrowid
    assert apply(other, st["action"]).status_code == 400
    ops = [dict(op, weekdays=[0]) if op["op"] == "add_day" else op for op in st["action"]["ops"]]
    assert apply(profile_id, {**st["action"], "ops": ops}).status_code == 400              # Montag ist schon OK
