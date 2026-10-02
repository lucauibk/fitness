"""Ein-Tipp-Erstellung: nur die Tageszahl, alles andere kommt aus Voreinstellungen."""
import pytest

from app import db
from app.planner import blocks, validate
from app.routes import planner as pr


@pytest.mark.parametrize("days", [2, 3, 4, 5, 6])
def test_create_from_days_only_gives_active_valid_plan_with_block(profile_id, days):
    res = pr.create_plan(pr.CreateIn(days=days), profile_id)
    split = db.active_split(profile_id)
    assert split["id"] == res["split"]["id"] and len(split["days"]) >= 2
    assert all(len(d["exercises"]) >= 3 for d in split["days"])
    assert all(e["base_sets"] == e["target_sets"] for d in split["days"] for e in d["exercises"])
    block = blocks.active(profile_id)
    assert block["split_id"] == split["id"] and blocks.state(block)["week"] == 1
    assert db.get_row("profiles", profile_id)["days_per_week"] == days
    findings = validate.analyze({"days": split["days"]}, minutes=90)["findings"]
    assert not [f for f in findings if f["group"] in ("plan", "verletzung")]


def test_creating_again_replaces_active_plan_but_keeps_the_old_one(profile_id):
    first = pr.create_plan(pr.CreateIn(days=3), profile_id)["split"]["id"]
    second = pr.create_plan(pr.CreateIn(days=4), profile_id)["split"]["id"]
    assert first != second
    assert db.active_split(profile_id)["id"] == second
    assert db.get_row("splits", first) is not None
    assert blocks.active(profile_id)["split_id"] == second


def test_saved_plan_stays_within_the_session_cap_in_any_block_week(profile_id):
    """Ohne Zeitfrage richtet sich der Plan nach dem Volumen – eine Einheit bleibt trotzdem unter der Obergrenze."""
    from datetime import timedelta
    from app.planner import volume_plan as vp
    pr.create_plan(pr.CreateIn(days=3), profile_id)
    split, block = db.active_split(profile_id), blocks.active(profile_id)
    start = blocks.monday(__import__("datetime").date.fromisoformat(block["start_date"]))
    for w in range(block["weeks"]):
        for d in split["days"]:
            total = sum(blocks.sets_for(block, e, start + timedelta(weeks=w)) for e in d["exercises"])
            assert total <= vp.MAX_SETS_PER_SESSION, (w, d["label"], total)


def _weekdays(profile_id):
    return sorted({wd for d in db.active_split(profile_id)["days"] for wd in d["weekdays"]})


def test_available_days_are_not_training_days(profile_id):
    """Mittwoch bis Sonntag frei heisst nicht fuenf Einheiten."""
    pr.create_plan(pr.CreateIn(days=3, available=[2, 3, 4, 5, 6]), profile_id)
    assert _weekdays(profile_id) == [2, 4, 6]                           # Mi, Fr, So
    prof = db.get_row("profiles", profile_id)
    assert db.parse_weekdays(prof["available_weekdays"]) == [2, 3, 4, 5, 6]     # freie Tage bleiben erhalten
    assert db.parse_weekdays(prof["train_weekdays"]) == [2, 4, 6]               # geplante Tage getrennt
    pr.create_plan(pr.CreateIn(days=4, available=[2, 3, 4, 5, 6]), profile_id)
    assert _weekdays(profile_id) == [2, 3, 5, 6]                        # nie drei Tage am Stueck


def test_sessions_never_exceed_available_days(profile_id):
    pr.create_plan(pr.CreateIn(days=5, available=[5, 6]), profile_id)    # nur Wochenende
    assert _weekdays(profile_id) == [5, 6]


def test_suggestion_recommends_by_level_and_caps_at_available(profile_id):
    from app.planner import generate
    assert generate.recommended_days("einsteiger", "aufbau", 5) == 3
    assert generate.recommended_days("fortgeschritten", "aufbau", 7) == 4
    assert generate.recommended_days("fitness", "fitness", 7) == 3
    assert generate.recommended_days("erfahren", "aufbau", 3) == 3
    res = pr.suggest(available="2,3,4,5,6", goal="aufbau", experience="einsteiger", days=None, focus="",
                     profile_id=profile_id)
    assert res["recommended"] == 3 and res["weekdays"] == [2, 4, 6] and res["max"] == 5
    assert pr.suggest(available="", goal=None, experience="einsteiger", days=4, focus="",
                      profile_id=profile_id)["weekdays"] == [0, 1, 3, 5]


def test_pick_days_prefers_even_spacing_and_avoids_runs():
    from app.planner.generate import pick_days
    assert pick_days([0, 1, 2, 3, 4], 3) == [0, 2, 4]
    assert pick_days([0, 1, 2, 3, 4], 4) == [0, 1, 3, 4]
    assert pick_days([2, 3, 4, 5, 6], 2) == [2, 5]
    assert pick_days([], 3) == [0, 2, 4]


def test_suggest_offers_splits_with_a_recommendation_that_depends_on_experience(profile_id):
    novice = pr.suggest(available="", goal="aufbau", experience="einsteiger", days=3, focus="", profile_id=profile_id)
    adv = pr.suggest(available="", goal="aufbau", experience="fortgeschritten", days=3, focus="", profile_id=profile_id)
    rec = lambda r: next(o["key"] for o in r["splits"] if o["recommended"])      # noqa: E731
    assert rec(novice) == "gk3" and rec(adv) != "gk3"
    assert len(novice["splits"]) >= 3


def test_chosen_split_is_used_and_new_plans_vary_isolation_exercises(profile_id):
    pr.create_plan(pr.CreateIn(days=3, split="ppl3"), profile_id)
    assert [d["label"] for d in db.active_split(profile_id)["days"]] == ["Push", "Pull", "Beine"]

    def isolation(pid):
        return {e["name"] for d in db.active_split(pid)["days"] for e in d["exercises"] if e["kind"] == "isolation"}
    pr.create_plan(pr.CreateIn(days=3, split="gk3"), profile_id)
    first = isolation(profile_id)
    pr.create_plan(pr.CreateIn(days=3, split="gk3"), profile_id)
    assert isolation(profile_id) != first
