from app.planner import progression as pg


def sess(*sets, date="2026-09-01"):
    return {"date": date, "sets": [{"reps": r, "weight": w, "rpe": rpe} for r, w, *rest in sets for rpe in [rest[0] if rest else None]]}


def rec(history, **kw):
    return pg.recommend(history, kw.pop("lo", 8), kw.pop("hi", 12), kw.pop("target_sets", 3),
                        kw.pop("equipment", "machine"), kw.pop("pattern", "h_push"), **kw)


def test_first_time_has_no_weight():
    r = rec([])
    assert r["status"] == "start" and r["weight"] is None and r["reps"] == 8


def test_raise_weight_when_top_reached_in_all_sets():
    r = rec([sess((12, 50), (12, 50), (12, 50))])
    assert r["raise_weight"] and r["weight"] == 55 and r["reps"] == 8       # Maschine: 5-kg-Sprung


def test_dumbbell_step_is_smaller_for_light_weights():
    r = rec([sess((12, 10), (12, 10), (12, 10))], equipment="dumbbell", pattern="lateral")
    assert r["weight"] == 11


def test_hold_when_top_reached_but_at_failure():
    r = rec([sess((12, 50, 10), (12, 50, 10), (12, 50, 9.5))], rir=2)
    assert not r["raise_weight"] and r["status"] == "hold" and r["weight"] == 50


def test_double_step_when_very_easy():
    r = rec([sess((12, 50, 6), (12, 50, 6), (12, 50, 6))])
    assert r["weight"] == 60


def test_add_a_rep_inside_the_range():
    r = rec([sess((10, 50), (9, 50), (8, 50))])
    assert r["weight"] == 50 and r["reps"] == 11 and not r["raise_weight"]


def test_below_range_holds_weight_then_drops_after_two_sessions():
    one = rec([sess((7, 50), (6, 50), (6, 50))])
    assert one["status"] == "hold" and one["weight"] == 50
    two = rec([sess((7, 50), (6, 50), (6, 50)), sess((7, 50), (6, 50), (5, 50), date="2026-08-25")])
    assert two["status"] == "drop" and two["weight"] < 50


def test_plateau_after_three_sessions_without_progress():
    h = [sess((9, 50), (8, 50), (8, 50)), sess((9, 50), (8, 50), (8, 50)), sess((9, 50), (9, 50), (8, 50))]
    r = rec(h)
    assert r["status"] == "plateau" and r["weight"] == 45


def test_deload_week_is_lighter():
    r = rec([sess((12, 100), (12, 100), (12, 100))], deload=True)
    assert r["status"] == "deload" and r["weight"] == 90 and not r["raise_weight"]


def test_bodyweight_adds_reps():
    r = rec([sess((12, None), (12, None), (12, None))], equipment="bodyweight", pattern="v_pull")
    assert r["weight"] is None and r["reps"] == 13


def test_e1rm_trend_and_rpe_drift():
    old, new = sess((10, 50)), sess((10, 55))
    assert pg.e1rm_trend([new, old]) > 0
    drifting = [sess((10, 50, 9)), sess((10, 50, 8)), sess((10, 50, 7.5))]
    assert pg.rpe_drift(drifting)
    assert not pg.rpe_drift([sess((10, 50, 8)), sess((10, 50, 8)), sess((10, 50, 8))])
