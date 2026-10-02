"""Eigenschaften des Generators ueber viele Profile: jeder Plan muss brauchbar sein."""
import itertools

import pytest

from app import db
from app.planner import generate, validate, volume_plan as vp
from app.planner.muscles import BIG, GROUPS, INJURY_AREAS, PATTERN_GROUP
from app.planner.profile_model import injuries_from

DAYS = {2: "0,3", 3: "0,2,4", 4: "0,1,3,4", 5: "0,1,2,3,4", 6: "0,1,2,3,4,5"}


def _profile(profile_id, **kw):
    db.update_row("profiles", profile_id, {"goal": "aufbau", "experience": "fortgeschritten", "session_minutes": 75, **kw})
    return db.get_row("profiles", profile_id)


def _all_plans(profile_id, **extra):
    for days, exp in itertools.product((3, 4, 5), ("einsteiger", "erfahren")):
        p = _profile(profile_id, experience=exp, train_weekdays=DAYS[days], days_per_week=days, **extra)
        for opt in generate.split_options(p):
            yield p, opt["key"], generate.generate(p, opt["key"])


def test_block_targets_ramp_and_deload():
    t = vp.block_targets("fortgeschritten", "aufbau")
    for g, weeks in t.items():
        train, deload = weeks[:-1], weeks[-1]
        assert train == sorted(train), g                       # nie weniger als die Vorwoche
        assert deload <= train[0] or train[0] == 0, g          # Deload unter Woche 1
        assert GROUPS[g][2] - 1 <= max(train) <= GROUPS[g][4], g   # zwischen MEV und MRV


def test_focus_group_gets_more_and_others_maintain():
    t = vp.block_targets("fortgeschritten", "aufbau", focus_groups={"po"})
    assert max(t["po"]) > max(vp.block_targets("fortgeschritten", "aufbau")["po"])
    assert max(t["brust"]) <= GROUPS["brust"][2]


def test_generated_plans_are_valid(profile_id):
    count = 0
    for p, key, plan in _all_plans(profile_id):
        count += 1
        for d in plan["days"]:
            assert len(d["exercises"]) >= 3, (key, d["label"])
            ids = [e["exercise_id"] for e in d["exercises"]]
            assert len(ids) == len(set(ids))
            for e in d["exercises"]:
                assert 1 <= e["target_sets"] <= 6 and e["target_reps_min"] <= e["target_reps_max"]
                assert e["week_sets"][-1] <= e["week_sets"][0]          # Deload leichter
                assert len(e["week_sets"]) == plan["block"]["weeks"]
        res = validate.analyze(plan, injuries=set(), minutes=p["session_minutes"], goal="aufbau",
                               rotate=plan["rotate"])
        assert not [f for f in res["findings"] if f["level"] == "warn" and f["group"] in ("plan", "verletzung")], key
    assert count >= 20


def test_big_muscles_are_trained_twice_when_possible(profile_id):
    p = _profile(profile_id, train_weekdays=DAYS[4], days_per_week=4)
    key = generate.split_options(p)[0]["key"]
    plan = generate.generate(p, key)
    res = validate.analyze(plan, minutes=75)
    assert not [f for f in res["findings"] if "nur an" in f["text"]]


@pytest.mark.parametrize("area", sorted(INJURY_AREAS))
def test_injuries_exclude_contraindicated_exercises(profile_id, area):
    p = _profile(profile_id, train_weekdays=DAYS[4], days_per_week=4, limitations=INJURY_AREAS[area])
    assert area in injuries_from(p["limitations"])
    plan = generate.generate(p, generate.split_options(p)[0]["key"])
    res = validate.analyze(plan, injuries={area})
    assert not [f for f in res["findings"] if f["group"] == "verletzung"], area


def test_variation_changes_isolation_but_not_main_lifts(profile_id):
    p = _profile(profile_id, train_weekdays=DAYS[4], days_per_week=4)
    key = generate.split_options(p)[0]["key"]
    a, b = generate.generate(p, key, variation=0), generate.generate(p, key, variation=1)
    names = lambda plan, comp: {e["name"] for d in plan["days"] for e in d["exercises"] if (e["pattern"] in generate.COMPOUND) == comp}  # noqa: E731
    assert names(a, True) == names(b, True)
    assert names(a, False) != names(b, False)


def test_time_budget_is_respected(profile_id):
    p = _profile(profile_id, train_weekdays=DAYS[3], days_per_week=3, session_minutes=45)
    plan = generate.generate(p, generate.split_options(p)[0]["key"])
    per_session = vp.capacity(3, 45, "aufbau")[1]
    for d in plan["days"]:
        assert sum(e["peak_sets"] for e in d["exercises"]) <= per_session + 2


def test_every_block_week_fits_the_time_budget(profile_id):
    for minutes in (45, 60, 90):
        p = _profile(profile_id, train_weekdays=DAYS[3], days_per_week=3, session_minutes=minutes)
        plan = generate.generate(p, generate.split_options(p)[0]["key"])
        per_session = vp.capacity(3, minutes, "aufbau")[1]
        for w in range(plan["block"]["weeks"]):
            for d in plan["days"]:
                assert sum(e["week_sets"][w] for e in d["exercises"]) <= per_session + 2, (minutes, w, d["label"])


def test_recommended_plans_pass_the_validator_for_normal_budgets(profile_id):
    """Bei ausreichender Zeit darf der empfohlene Plan keine Luecke unter Erhaltung haben."""
    for days in (3, 4, 5):
        p = _profile(profile_id, train_weekdays=DAYS[days], days_per_week=days, session_minutes=90,
                     experience="fortgeschritten")
        plan = generate.generate(p, generate.split_options(p)[0]["key"])
        res = validate.analyze(plan, minutes=90, goal="aufbau", rotate=plan["rotate"], experience="fortgeschritten")
        low = [f for f in res["findings"] if "nicht einmal" in f["text"] or f["level"] == "warn"]
        assert not low, (days, low)


def test_alternatives_respect_injuries():
    names = {e["name"] for e in generate.alternatives("squat", injuries={"knee"})}
    assert "Kniebeuge" not in names and "Beinpresse" in names


def test_default_split_per_day_count_and_session_cap(profile_id):
    from app.planner.generate import DEFAULT_SPLIT, MAX_SESSION_SETS_PER_MUSCLE
    from app.planner.muscles import group_of
    for days, key in DEFAULT_SPLIT.items():
        p = _profile(profile_id, train_weekdays=None, days_per_week=days, session_minutes=90)
        plan = generate.generate(p, key, {"days": days, "weekdays": []})
        assert len(plan["days"]) >= 2
        for d in plan["days"]:
            per_group = {}
            for e in d["exercises"]:
                per_group[group_of(e["pattern"])] = per_group.get(group_of(e["pattern"]), 0) + e["peak_sets"]
            assert max(per_group.values()) <= MAX_SESSION_SETS_PER_MUSCLE, (key, d["label"], per_group)


def test_lengthened_exercises_get_a_bonus():
    from app.planner.generate import score
    base = {"name": "X", "sfr": 4, "difficulty": 1, "equipment_type": "machine", "pattern": "leg_iso_back"}
    s = {"experience": "fortgeschritten", "goal": "aufbau", "age": None}
    assert score({**base, "lengthened": 1}, s) > score({**base, "lengthened": None}, s)
