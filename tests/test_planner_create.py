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
