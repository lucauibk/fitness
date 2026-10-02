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
