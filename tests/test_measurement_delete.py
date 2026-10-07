"""Wird eine Messung geloescht, darf ihr Gewicht nicht im Koerpergewicht-Verlauf
stehen bleiben – sonst rechnen Kalorienziel, Trinkziel und Cardio-kcal weiter damit."""
from app import activity, body, db
from app.routes.body import delete_measurement


def _bw(profile_id):
    return [(r["date"], r["kg"]) for r in
            db.rows("SELECT date, kg FROM bodyweight WHERE profile_id=? ORDER BY date", (profile_id,))]


def test_delete_removes_weight_of_that_measurement(profile_id):
    body.save(profile_id, {"date": "2026-09-01", "weight_kg": 85})
    wrong, _ = body.save(profile_id, {"date": "2026-09-03", "weight_kg": 58})  # Tippfehler
    assert activity.latest_weight(profile_id) == 58
    delete_measurement(wrong["id"], profile_id=profile_id)
    assert _bw(profile_id) == [("2026-09-01", 85)]
    assert activity.latest_weight(profile_id) == 85


def test_delete_falls_back_to_other_measurement_same_day(profile_id):
    body.save(profile_id, {"date": "2026-09-05", "weight_kg": 80})
    later, _ = body.save(profile_id, {"date": "2026-09-05", "weight_kg": 81})
    delete_measurement(later["id"], profile_id=profile_id)
    assert _bw(profile_id) == [("2026-09-05", 80)]


def test_delete_keeps_weight_set_by_other_entry(profile_id):
    first, _ = body.save(profile_id, {"date": "2026-09-07", "weight_kg": 80})
    body.save(profile_id, {"date": "2026-09-07", "weight_kg": 81})
    delete_measurement(first["id"], profile_id=profile_id)
    assert _bw(profile_id) == [("2026-09-07", 81)]


def test_delete_without_weight_leaves_bodyweight(profile_id):
    body.save(profile_id, {"date": "2026-09-09", "weight_kg": 80})
    waist, _ = body.save(profile_id, {"date": "2026-09-09", "waist_cm": 85})
    delete_measurement(waist["id"], profile_id=profile_id)
    assert _bw(profile_id) == [("2026-09-09", 80)]
