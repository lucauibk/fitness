"""Blockwochen und Wochen-Check. Die Satzzahl bleibt dabei immer der Plan."""
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


def test_block_keeps_planned_sets_and_halves_them_in_deload(profile_id):
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    row = db.active_split(profile_id)["days"][0]["exercises"][0]          # Bankdrücken: 3 Sätze im Plan
    sets = [blocks.sets_for(block, row, MONDAY + timedelta(weeks=w)) for w in range(block["weeks"])]
    assert sets == [3] * (block["weeks"] - 1) + [2]                      # nie mehr als der Plan
    st = blocks.state(block, MONDAY + timedelta(weeks=block["weeks"] - 1))
    assert st["deload"] and st["rir"] == 4
    assert blocks.state(block, MONDAY + timedelta(weeks=block["weeks"]))["finished"]


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
    with db.connect() as c:                  # Seitheben hat der Plan-Ersteller gewaehlt, den Curl die Person selbst
        c.execute("UPDATE split_exercises SET generated=1 WHERE id IN (?, ?, ?)",
                  (ids["Bankdrücken"][0], ids["Latzug"][0], ids["Seitheben"][0]))
    db.update_row("profiles", profile_id, {"experience": "erfahren"})        # inzwischen hoeher eingestuft
    old = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    later = MONDAY + timedelta(weeks=old["weeks"] + 1)
    res = autoregulate.review(profile_id, later)
    new = blocks.active(profile_id)
    assert new["id"] != old["id"] and blocks.state(new, later)["week"] == 1
    assert new["experience"] == "erfahren"                                   # neuer Block nach dem Profil
    names = {e["name"] for e in db.active_split(profile_id)["days"][0]["exercises"]}
    assert {"Bankdrücken", "Latzug"} <= names                                # Grundübungen bleiben
    assert "Seitheben" not in names                                          # gewählte Isolation gewechselt
    assert "Bizepscurls Kurzhantel" in names                                 # selbst eingetragene bleibt
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


def test_switching_back_to_a_plan_resumes_its_block_instead_of_restarting(profile_id):
    sp, day, ids = _setup(profile_id)
    first = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    other = db.insert_row("splits", {"profile_id": profile_id, "name": "Zweiter", "active": 0})
    db.insert_row("split_days", {"split_id": other["id"], "position": 0, "label": "X", "weekdays": "1"})
    db.activate_split(other["id"], profile_id)
    second = blocks.ensure(profile_id)
    assert second["id"] != first["id"] and second["split_id"] == other["id"]
    db.activate_split(sp["id"], profile_id)
    back = blocks.ensure(profile_id)
    assert back["id"] == first["id"] and back["start_date"] == first["start_date"]
    assert db.one("SELECT COUNT(*) AS n FROM blocks WHERE profile_id=? AND active=1", (profile_id,))["n"] == 1
    db.activate_split(other["id"], profile_id)
    assert blocks.ensure(profile_id)["id"] == second["id"]


def test_early_deload_survives_a_second_review_in_the_same_week(profile_id, monkeypatch):
    """Vorgezogene Entlastung: der zweite Aufruf derselben Woche (weniger Signale) darf sie nicht aufheben."""
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    week3 = MONDAY + timedelta(weeks=2)
    monkeypatch.setattr(autoregulate, "deload_signals", lambda *a: ["A", "B"])
    autoregulate.review(profile_id, week3)
    monkeypatch.setattr(autoregulate, "deload_signals", lambda *a: [])
    autoregulate.review(profile_id, week3)
    st = blocks.state(blocks.active(profile_id), week3)
    assert st["deload"] and st["weeks"] == 3 and st["rir"] == 4


def _bulk(profile_id, ex_id, day, sets):
    at = datetime(day.year, day.month, day.day, 17).isoformat()
    with db.connect() as c:
        w = c.execute("INSERT INTO workouts (profile_id, started_at) VALUES (?, ?)", (profile_id, at)).lastrowid
        c.executemany("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight, warmup) VALUES (?,?,?,10,50,0)",
                      [(w, ex_id, k + 1) for k in range(sets)])


def test_fatigue_compares_against_the_own_baseline(profile_id):
    sp, day, ids = _setup(profile_id)
    bench = ids["Bankdrücken"][1]
    start = MONDAY
    assert autoregulate.fatigue(profile_id, start) is None                         # noch kein Verlauf
    for week in range(6):                                                          # 6 Wochen gleichmaessig
        for k in (0, 2, 4):
            _bulk(profile_id, bench, start + timedelta(weeks=week, days=k), 12)
    steady_day = start + timedelta(weeks=6)
    now, base = autoregulate.fatigue(profile_id, steady_day)
    assert 0.8 * base < now < 1.2 * base
    assert not any("Trainingslast" in s for s in autoregulate.deload_signals(profile_id, db.active_split(profile_id), steady_day))
    for k in range(5):                                                             # eine Woche doppelt so viel, taeglich
        _bulk(profile_id, bench, steady_day + timedelta(days=k), 24)
    hard_day = steady_day + timedelta(days=4)
    now, base = autoregulate.fatigue(profile_id, hard_day)
    assert now >= autoregulate.FATIGUE_HIGH * base
    assert any("Trainingslast" in s for s in autoregulate.deload_signals(profile_id, db.active_split(profile_id), hard_day))


def test_fatigue_stays_quiet_after_a_break_and_during_a_planned_ramp(profile_id):
    sp, day, ids = _setup(profile_id)
    bench = ids["Bankdrücken"][1]
    for week, n in enumerate([12, 12, 12, 12, 12, 14, 16, 18]):                  # geplanter Anstieg im Block
        for k in (0, 2, 4):
            _bulk(profile_id, bench, MONDAY + timedelta(weeks=week, days=k), n)
    now, ref = autoregulate.fatigue(profile_id, MONDAY + timedelta(weeks=7, days=6))
    assert now < autoregulate.FATIGUE_HIGH * ref
    back = MONDAY + timedelta(weeks=10)                                          # zwei Wochen Pause, dann zurueck
    for k in (0, 2, 4):
        _bulk(profile_id, bench, back + timedelta(days=k), 18)
    assert autoregulate.fatigue(profile_id, back + timedelta(days=6)) is None


def test_weekly_check_never_adds_sets(profile_id):
    """Ausgelassene Muskeln und sehr leichte Wochen: früher kamen Sätze dazu, jetzt bleibt der Plan."""
    sp, day, ids = _setup(profile_id)
    blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    # Woche 1: Latzug ausgelassen, Bankdrücken überall leicht (RPE 5)
    for d in (0, 3):
        _log(profile_id, day["id"], ids["Bankdrücken"][1], MONDAY + timedelta(days=d), sets=4, rpe=5)
    res = autoregulate.review(profile_id, MONDAY + timedelta(weeks=1))
    assert not [e for e in res["events"] if e["kind"] == "sets_up"]
    block, rows = blocks.active(profile_id), db.active_split(profile_id)["days"][0]["exercises"]
    assert [blocks.sets_for(block, r, MONDAY + timedelta(weeks=1)) for r in rows] == [3, 3, 3, 3]


def test_old_block_params_with_ramp_and_catch_up_are_ignored(profile_id):
    """Bloecke aus der Zeit mit Satz-Steigerung tragen ratios/adjust noch in params."""
    sp, day, ids = _setup(profile_id)
    block = blocks.create(profile_id, sp["id"], "aufbau", "fortgeschritten", start=MONDAY)
    row = db.active_split(profile_id)["days"][0]["exercises"][0]
    old = {**blocks._params(block), "ratios": {"brust": [1.0, 1.5, 2.0, 2.0, 0.5]},
           "adjust": {"2": {str(row["id"]): 2}}}
    blocks._save_params(block["id"], old)
    assert blocks.sets_for(blocks.active(profile_id), row, MONDAY + timedelta(weeks=1)) == 3


def test_old_set_increase_events_are_removed_on_start(profile_id):
    explain.log(profile_id, "sets_up", "Latzug: +1 Satz diese Woche – letzte Woche fehlten 7 Sätze Rücken")
    explain.log(profile_id, "swap", "Butterfly → Kabel-Flys: neuer Block")
    db.init()
    kinds = [e["kind"] for e in explain.recent(profile_id)]
    assert "sets_up" not in kinds and "swap" in kinds


def test_today_shows_only_fresh_plan_events():
    from app import daily_view
    events = [{"date": "2026-10-13", "text": "neu"}, {"date": "2026-10-08", "text": "alt"}]
    assert [e["text"] for e in daily_view.recent_events(events, date(2026, 10, 14))] == ["neu"]
