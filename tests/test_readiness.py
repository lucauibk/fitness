from app import readiness


def test_score_average_day_allows_full_training():
    # Alles auf 3 (Mitte der 1-5-Skala, kein Muskelkater) -> volles Training (Bucket 0/Analyse).
    assert readiness.score(3, 3, 3, {}) == 60


def test_score_soreness_reduces_readiness():
    base = readiness.score(3, 3, 3, {})
    sore = readiness.score(3, 3, 3, {"beine": 3})
    assert sore < base


def test_score_bad_sleep_and_high_stress_reduces_readiness():
    base = readiness.score(3, 3, 3, {})
    tired = readiness.score(1, 2, 5, {})
    assert tired < base


def test_save_keeps_sleep_stress_energy_apart(profile_id):
    """readiness.save() nimmt die Argumente in der Reihenfolge (sleep, energy, stress),
    die Spalten in der DB stehen aber als (sleep, stress, energy) - Regressionstest fuer
    genau diese Vertauschung."""
    saved = readiness.save(profile_id, "2026-01-01", sleep=5, energy=2, stress=1, soreness={})
    assert saved["sleep"] == 5
    assert saved["energy"] == 2
    assert saved["stress"] == 1

    fetched = readiness.get(profile_id, "2026-01-01")
    assert fetched["sleep"] == 5
    assert fetched["energy"] == 2
    assert fetched["stress"] == 1
