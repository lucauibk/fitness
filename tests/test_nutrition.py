from datetime import date

from app import db, nutrition


def test_computed_goals_reports_missing_data(profile_id):
    profile = db.get_row("profiles", profile_id)
    calc, missing = nutrition.computed_goals(profile)
    assert calc is None
    assert "Körpergewicht" in missing


def test_computed_goals_with_full_profile(profile_id):
    db.update_row("profiles", profile_id, {
        "birth_year": 1990, "sex": "männlich", "height_cm": 180,
        "goal": "aufbau", "activity_level": "mittel"})
    db.set_bodyweight(profile_id, date.today().isoformat(), 80)
    profile = db.get_row("profiles", profile_id)
    calc, missing = nutrition.computed_goals(profile)
    assert missing == []
    assert calc["kcal"] > 0
    assert calc["protein"] > 0
    assert calc["carbs"] >= 0


def test_water_goal_scales_with_weight(profile_id):
    db.update_row("profiles", profile_id, {"sex": "männlich"})
    db.set_bodyweight(profile_id, date.today().isoformat(), 60)
    light = nutrition.water_goals(db.get_row("profiles", profile_id))["goal"]
    db.set_bodyweight(profile_id, date.today().isoformat(), 100)
    heavy = nutrition.water_goals(db.get_row("profiles", profile_id))["goal"]
    assert heavy > light


def test_water_goal_manual_override_wins(profile_id):
    db.update_row("profiles", profile_id, {"water_goal_ml": 3000})
    result = nutrition.water_goals(db.get_row("profiles", profile_id))
    assert result["auto"] is False
    assert result["goal"] == 3000
