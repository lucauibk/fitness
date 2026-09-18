from app.body import navy_body_fat


def test_navy_body_fat_male_plausible_range():
    v = navy_body_fat("männlich", 180, waist_cm=85, neck_cm=38)
    assert v is not None and 5 < v < 25


def test_navy_body_fat_female_needs_hip():
    assert navy_body_fat("weiblich", 165, waist_cm=75, neck_cm=32, hip_cm=None) is None
    v = navy_body_fat("weiblich", 165, waist_cm=75, neck_cm=32, hip_cm=95)
    assert v is not None and 5 < v < 45


def test_navy_body_fat_impossible_measurements_return_none():
    # Taille kleiner als Hals ergibt bei Maennern eine negative/undefinierte Formel.
    assert navy_body_fat("männlich", 180, waist_cm=30, neck_cm=38) is None


def test_navy_body_fat_unknown_sex_returns_none():
    assert navy_body_fat("divers", 180, waist_cm=85, neck_cm=38) is None
