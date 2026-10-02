from datetime import date, datetime, timedelta

from app import db
from app.planner import autoregulate, cold_start, progression


def _ex(name):
    return db.one("SELECT id, pattern, equipment_type FROM exercises WHERE name=?", (name,))


def test_prior_scales_with_bodyweight_sex_and_equipment():
    male = {"sex": "männlich"}
    female = {"sex": "weiblich"}
    a = cold_start.prior_e1rm(male, 80, "squat", "barbell")
    assert a == 80 * 0.75
    assert cold_start.prior_e1rm(female, 80, "squat", "barbell") < a
    assert cold_start.prior_e1rm(male, 80, "squat", "machine") > a
    assert cold_start.prior_e1rm(male, 80, "squat", "bodyweight") is None


def test_start_weight_is_conservative_and_marked_as_estimate(profile_id):
    db.set_bodyweight(profile_id, date.today().isoformat(), 80)
    kg, why = cold_start.starting_weight(profile_id, _ex("Beinpresse")["id"])
    assert kg and 20 <= kg <= 80 and "Schätzung" in why
    rec = progression.for_exercise(profile_id, _ex("Beinpresse")["id"], 8, 12)
    assert rec["estimate"] and rec["weight"] == kg
    assert cold_start.starting_weight(profile_id, _ex("Klimmzüge")["id"]) == (None, None)


def test_known_similar_exercise_beats_the_population_prior(profile_id):
    bench = _ex("Bankdrücken")
    at = datetime.now().isoformat()
    w = db.insert_row("workouts", {"profile_id": profile_id, "started_at": at, "finished_at": at})
    with db.connect() as c:
        for n in range(3):
            c.execute("INSERT INTO sets (workout_id, exercise_id, set_no, reps, weight, warmup) VALUES (?,?,?,?,?,0)",
                      (w["id"], bench["id"], n + 1, 8, 100))
    kg, why = cold_start.starting_weight(profile_id, _ex("Brustpresse")["id"])
    assert "ähnlichen Übung" in why and kg > 50


def test_kalman_update_moves_toward_observation_and_shrinks_uncertainty():
    m, s = cold_start.update_estimate(100, 20, 120, 5)
    assert 100 < m <= 120 and s < 20
    m2, _ = cold_start.update_estimate(100, 20, 120, 5, novice=True)
    assert m2 < m          # Anfaenger-Messungen zaehlen weniger


def test_experience_only_rises_automatically_and_manual_choice_wins(profile_id):
    db.update_row("profiles", profile_id, {"experience": "einsteiger", "experience_auto": None})
    autoregulate.reclassify(profile_id)           # manuell gesetzt -> unveraendert
    assert db.get_row("profiles", profile_id)["experience"] == "einsteiger"
