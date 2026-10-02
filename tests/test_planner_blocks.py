"""Blockwochen, Wochen-Check und Nachholen verpasster Saetze."""
from datetime import date, datetime, timedelta

from app import db
from app.planner import autoregulate, blocks, explain

MONDAY = date(2026, 9, 7)


def _setup(profile_id):
    sp = db.insert_row("splits", {"profile_id": profile_id, "name": "T", "active": 1})
    day = db.insert_row("split_days", {"split_id": sp["id"], "position": 0, "label": "OK", "weekdays": "0,3"})
    ids = {}
    for pos, name in enumerate(["Bankdrücken", "Latzug", "Seitheben", "Bizepscurls Kurzhantel"]):
        ex = db.one("SELECT id FROM exercises WHERE name=?", (name,))
        row = db.insert_row("split_exercises", {"split_day_id": day["id"], "exercise_id": ex["id"], "position": pos,
                                                "target_sets": 3, "target_reps_min": 8, "target_reps_max": 12})
        ids[name] = (row["id"], ex["id"])
    db.update_row("profiles", profile_id, {"goal": "aufbau", "experience": "fortgeschritten"})
    return sp, day, ids


def _log(profile_id, day_id, ex_id, on, sets=3, reps=10, weight=50, rpe=None):
    at = datetime(on.year, on.month, on.day, 18).isoformat()
    w = db.insert_row("workouts", {"profile_id": profile_id, "split_day_id": day_id, "started_at": at, "finished_at": at})
    with db.connect() as c:
        for n in range(sets):
            c.execute("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight, rpe, warmup) VALUES (?,?,?,?,?,?,0)",
                      (w["id"], ex_id, n + 1, reps, weight, rpe))


def test_block_weeks_ramp_and_deload(profile_id):
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    row = db.active_split(profile_id)["days"][0]["exercises"][0]          # Bankdrücken, Woche 1 = 3 Sätze
    sets = [blocks.sets_for(block, row, MONDAY + timedelta(weeks=w)) for w in range(block["weeks"])]
    assert sets[0] == 3 and sets == sorted(sets[:-1]) + [sets[-1]]
    assert sets[-1] < sets[0] and max(sets[:-1]) <= 6                   # Deload leichter
    st = blocks.state(block, MONDAY + timedelta(weeks=block["weeks"] - 1))
    assert st["deload"] and st["rir"] == 4
    assert blocks.state(block, MONDAY + timedelta(weeks=block["weeks"]))["finished"]


def test_missed_sets_are_made_up_next_week(profile_id):
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    # Woche 1: nur Brust trainiert, Rücken (Latzug) komplett ausgelassen
    _log(profile_id, day["id"], ids["Bankdrücken"][1], MONDAY)
    res = autoregulate.review(profile_id, MONDAY + timedelta(weeks=1))
    texts = [e["text"] for e in res["events"]]
    assert any("Latzug" in t and "+1 Satz" in t for t in texts), texts
    st = blocks.state(blocks.active(profile_id), MONDAY + timedelta(weeks=1))
    row = next(e for e in db.active_split(profile_id)["days"][0]["exercises"] if e["name"] == "Latzug")
    plain = blocks.sets_for({**blocks.active(profile_id), "params": '{"ratios": {}}'}, row, MONDAY + timedelta(weeks=1))
    assert blocks.sets_for(blocks.active(profile_id), row, MONDAY + timedelta(weeks=1)) > plain - 1
    # zweiter Aufruf in derselben Woche aendert nichts mehr
    again = autoregulate.review(profile_id, MONDAY + timedelta(weeks=1))
    assert len(again["events"]) == len(res["events"])


def test_rpe_drift_pulls_deload_forward(profile_id):
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    for ex_name, rpes in (("Bankdrücken", (7, 8, 9)), ("Latzug", (7, 8, 9))):
        for i, rpe in enumerate(rpes):
            _log(profile_id, day["id"], ids[ex_name][1], MONDAY + timedelta(weeks=i, days=1), rpe=rpe)
    res = autoregulate.review(profile_id, MONDAY + timedelta(weeks=2))      # Woche 3
    st = blocks.state(blocks.active(profile_id), MONDAY + timedelta(weeks=2))
    assert st["deload"] and any("Entlastungswoche vorgezogen" in e["text"] for e in res["events"])


def test_rollover_starts_new_block_and_swaps_isolation_only(profile_id):
    sp, day, ids = _setup(profile_id)
    old = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    later = MONDAY + timedelta(weeks=old["weeks"] + 1)
    res = autoregulate.review(profile_id, later)
    new = blocks.active(profile_id)
    assert new["id"] != old["id"] and blocks.state(new, later)["week"] == 1
    names = {e["name"] for e in db.active_split(profile_id)["days"][0]["exercises"]}
    assert {"Bankdrücken", "Latzug"} <= names                                # Grundübungen bleiben
    assert not {"Seitheben", "Bizepscurls Kurzhantel"} <= names              # Isolation gewechselt
    assert any(e["kind"] == "swap" for e in res["events"])


def test_events_are_deduplicated(profile_id):
    explain.log(profile_id, "info", "Test")
    explain.log(profile_id, "info", "Test")
    assert len([e for e in explain.recent(profile_id) if e["text"] == "Test"]) == 1


def test_deleting_a_plan_removes_block_and_events_but_keeps_workouts(profile_id):
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    explain.log(profile_id, "info", "Plan-Hinweis", None, sp["id"])
    _log(profile_id, day["id"], ids["Bankdrücken"][1], MONDAY)
    db.delete_row("splits", sp["id"])
    assert db.one("SELECT 1 AS x FROM blocks WHERE split_id=?", (sp["id"],)) is None
    assert db.one("SELECT 1 AS x FROM plan_events WHERE split_id=?", (sp["id"],)) is None
    w = db.one("SELECT split_day_id FROM workouts WHERE profile_id=?", (profile_id,))
    assert w is not None and w["split_day_id"] is None


def test_block_continues_when_no_fatigue_signals_and_stops_at_eight_weeks(profile_id):
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    weeks0 = block["weeks"]
    deload_week = MONDAY + timedelta(weeks=weeks0 - 1)
    res = autoregulate.review(profile_id, deload_week)
    nb = blocks.active(profile_id)
    assert nb["weeks"] == weeks0 + 1 and not blocks.state(nb, deload_week)["deload"]
    assert blocks.state(nb, MONDAY + timedelta(weeks=weeks0))["deload"]       # die Entlastung ruecke nach hinten
    assert any("Weiter ohne Entlastung" in e["text"] for e in res["events"])
    # bei 8 Wochen ist Schluss: dann kommt die Entlastung
    for _ in range(5):
        cur = blocks.active(profile_id)
        autoregulate.review(profile_id, MONDAY + timedelta(weeks=cur["weeks"] - 1))
    assert blocks.active(profile_id)["weeks"] <= autoregulate.MAX_BLOCK_WEEKS


def test_fatigue_signals_trigger_deload_at_block_end(profile_id):
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    for name in ("Bankdrücken", "Latzug"):
        for i, rpe in enumerate((7, 8, 9)):
            _log(profile_id, day["id"], ids[name][1], MONDAY + timedelta(weeks=i, days=1), rpe=rpe)
    autoregulate.review(profile_id, MONDAY + timedelta(weeks=block["weeks"] - 1))
    assert blocks.active(profile_id)["weeks"] <= block["weeks"]


def test_catch_up_is_capped_to_twenty_percent_and_mrv(profile_id):
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    # Woche 1 komplett ausgelassen -> alles fehlt, Nachholen darf trotzdem nur ein Fuenftel der Woche sein
    autoregulate.review(profile_id, MONDAY + timedelta(weeks=1))
    block = blocks.active(profile_id)
    adj = blocks._params(block)["adjust"].get("2", {})
    this_week = autoregulate.planned_sets(block, db.active_split(profile_id), 2)
    assert 0 < sum(adj.values()) <= max(1, round(0.2 * (sum(this_week.values()) - sum(adj.values()))) + 1)


def test_pain_report_replaces_loading_exercises_and_remembers_the_area(profile_id):
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    bench = db.one("SELECT id FROM exercises WHERE name='Bankdrücken'")
    se = db.one("SELECT id FROM split_exercises WHERE exercise_id=? AND split_day_id=?", (bench["id"], day["id"]))
    res = autoregulate.report_pain(profile_id, "shoulder")
    assert "Schulter" in res["limitations"] and any(s["from"] == "Bankdrücken" for s in res["swapped"])
    now = db.one("SELECT exercise_id FROM split_exercises WHERE id=?", (se["id"],))["exercise_id"]
    assert now != bench["id"]
    assert "Schulter" in db.get_row("profiles", profile_id)["limitations"]
    assert any("Schmerz bei Schulter" in e["text"] for e in explain.recent(profile_id))
    import pytest
    with pytest.raises(ValueError):
        autoregulate.report_pain(profile_id, "kopf")


def _stalled(profile_id, day, ex_id):
    for i in range(3):
        _log(profile_id, day["id"], ex_id, date.today() - timedelta(days=14 - i * 3), reps=9 if i == 0 else 8, weight=50)


def test_first_plateau_does_not_swap_but_a_repeated_one_does(profile_id):
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    ex_id = ids["Latzug"][1]
    _stalled(profile_id, day, ex_id)
    explain.log(profile_id, "plateau", "Latzug: erster Stillstand (heute gemeldet)", ex_id, sp["id"])
    assert not autoregulate.swap_plateaus(profile_id, db.active_split(profile_id))       # erst Reset
    old = (date.today() - timedelta(days=30)).isoformat()
    db.insert_row("plan_events", {"profile_id": profile_id, "split_id": sp["id"], "exercise_id": ex_id,
                                  "date": old, "kind": "plateau", "text": "Latzug: früherer Stillstand"})
    assert autoregulate.swap_plateaus(profile_id, db.active_split(profile_id))            # zweiter Stillstand
    assert db.one("SELECT exercise_id FROM split_exercises WHERE id=?", (ids["Latzug"][0],))["exercise_id"] != ex_id
