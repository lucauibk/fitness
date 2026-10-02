from datetime import date

from app.planner import progression as pg


def sess(*sets, date="2026-09-01"):
    return {"date": date, "sets": [{"reps": r, "weight": w, "rpe": rpe} for r, w, *rest in sets for rpe in [rest[0] if rest else None]]}


def rec(history, **kw):
    kw.setdefault("today", date(2026, 9, 3))
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


def test_novice_ignores_rpe_and_plateaus_after_two_sessions():
    top = [sess((12, 50, 10), (12, 50, 10), (12, 50, 10))]
    assert rec(top, experience="einsteiger")["raise_weight"]              # kein Halten wegen RPE
    assert not rec(top, experience="fortgeschritten", rir=2)["raise_weight"]
    stalled = [sess((9, 50), (8, 50), (8, 50)), sess((9, 50), (8, 50), (8, 50))]
    assert rec(stalled, experience="einsteiger")["status"] == "plateau"
    assert rec(stalled, experience="fortgeschritten")["status"] != "plateau"


def test_layoff_resumes_lighter():
    h = [sess((10, 100), (10, 100), (10, 100), date="2026-09-01")]
    short = rec(h, today=date(2026, 9, 22))      # 21 Tage
    long = rec(h, today=date(2026, 10, 15))      # 44 Tage
    assert (short["status"], short["weight"]) == ("return", 95.0)          # 93 % von 100 kg, auf 5 kg gerundet
    assert (long["status"], long["weight"]) == ("return", 85.0)            # 87 %
    assert rec(h, today=date(2026, 9, 8))["status"] != "return"


def test_layoff_counts_the_last_workout_overall_not_the_exercise():
    h = [sess((10, 100), (10, 100), (10, 100), date="2026-08-01")]        # Uebung lange nicht gemacht
    assert rec(h, today=date(2026, 9, 3), last_workout="2026-09-01")["status"] != "return"
    assert rec(h, today=date(2026, 9, 3))["status"] == "return"


def test_dates_follow_vienna_not_utc(profile_id):
    """Eine Einheit um 00:30 Wiener Zeit liegt in UTC noch am Vortag – zaehlen muss der Wiener Tag."""
    from datetime import date as d
    from app import db
    from app.planner import autoregulate, progression
    with db.connect() as c:
        ex = c.execute("INSERT INTO exercises (name, muscle_group, pattern) VALUES (?, 'brust', 'h_push')",
                       (f"TZ-Test {profile_id}",)).lastrowid
        w = c.execute("INSERT INTO workouts (profile_id, started_at) VALUES (?, '2026-09-27T22:30:00.000Z')",
                      (profile_id,)).lastrowid                                      # Montag 00:30 in Wien
        c.execute("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight) VALUES (?, ?, 1, 8, 50)", (w, ex))
    assert progression.sessions(profile_id, ex)[0]["date"] == "2026-09-28"
    week_of_sunday, _ = autoregulate.actual_sets(profile_id, d(2026, 9, 21), d(2026, 9, 27))
    week_of_monday, _ = autoregulate.actual_sets(profile_id, d(2026, 9, 28), d(2026, 10, 4))
    assert week_of_sunday["brust"] == 0 and week_of_monday["brust"] == 1


def test_top_of_range_after_a_hold_raises_instead_of_plateau():
    """Oben angekommen, aber am Limit -> einmal halten; wieder oben -> steigern, nicht als Plateau 10 % runter."""
    at_failure = sess((12, 50, 10), (12, 50, 10), (12, 50, 10))
    r = rec([at_failure, at_failure, at_failure], experience="fortgeschritten")
    assert r["status"] != "plateau" and r["raise_weight"] and r["weight"] > 50


def test_lighter_is_at_least_one_step_and_never_below_the_empty_bar():
    stuck = [sess((8, 4), (8, 4), (8, 4))] * 3                      # Kurzhantel 4 kg: 0,9 x 4 rundet auf 4
    r = rec(stuck, equipment="dumbbell", pattern="lateral", experience="fortgeschritten")
    assert r["status"] == "plateau" and r["weight"] == 3
    bar = [sess((8, 20), (8, 20), (8, 20))] * 3
    assert rec(bar, equipment="barbell", pattern="h_push", deload=True)["weight"] == 20
    assert rec(bar, equipment="barbell", pattern="h_push", experience="fortgeschritten")["weight"] == 20
    assert pg.lighter(22.5, 0.9, 2.5, "barbell", "h_push") == 20
    at_bar = rec(bar, equipment="barbell", pattern="h_push", deload=True)
    assert "leichter geht hier nicht" in at_bar["reason"]                   # kein falsches „10 % leichter“
    light_bar = [sess((8, 10), (8, 10), (8, 10))] * 3                       # Kreuzheben mit 10 kg geloggt
    assert rec(light_bar, equipment="barbell", pattern="hinge", deload=True)["weight"] == 10
    sz = [sess((10, 15), (10, 15), (10, 15))] * 3                           # SZ-Curls: keine 20-kg-Grenze
    assert rec(sz, equipment="barbell", pattern="biceps", deload=True)["weight"] < 15


def test_barbell_start_weight_is_at_least_the_empty_bar(profile_id):
    from app import db
    from app.planner import cold_start
    db.update_row("profiles", profile_id, {"sex": "weiblich", "experience": "einsteiger"})
    ex = db.one("SELECT id FROM exercises WHERE equipment_type='barbell' AND pattern='v_push' AND archived=0")
    kg, _ = cold_start.starting_weight(profile_id, ex["id"])
    assert kg >= 20


def test_kalman_estimate_smooths_an_outlier_but_follows_real_progress():
    steady = [sess((8, 100), date=f"2026-09-{d:02d}") for d in (22, 15, 8, 1)]       # neueste zuerst
    m, sigma = pg.estimate(steady)
    assert abs(m - 100 * (1 + 8 / 30)) < 0.5 and sigma < 0.1 * m
    outlier = [sess((8, 120), date="2026-09-29")] + steady                            # ein Ausreisser-Tag
    assert m < pg.estimate(outlier)[0] < 120 * (1 + 8 / 30)
    climbing = [sess((8, w), date=f"2026-0{9 if d < 10 else 8}-{d:02d}")
                for w, d in ((110, 1), (107.5, 25), (105, 18), (102.5, 11), (100, 4))]
    assert pg.estimate(climbing)[0] > 105 * (1 + 8 / 30)                              # folgt dem Anstieg
    rpe8 = [sess((8, 100, 8))]
    assert pg.estimate(rpe8)[0] == 100 * (1 + 10 / 30)                                # 2 Wdh. in Reserve
