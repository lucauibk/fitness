from app import volume


def test_targets_honest_when_capacity_never_reaches_landmarks():
    """2 Einheiten x 30 min reichen von vornherein nicht fuer die Erhaltungs-Untergrenzen
    (MV) aller Muskelgruppen - der Hinweistext darf dann nicht behaupten, es sei jetzt
    gekuerzt genug, um in die Zeit zu passen (Bucket 0.9 aus der Roadmap-Analyse)."""
    out, notes, per_session = volume.targets("erfahren", "aufbau", focus=(), days=2, minutes=30)
    total = sum(out.values())
    cap = 2 * per_session
    assert total > cap
    assert any("Selbst mit Kürzung" in n for n in notes)


def test_targets_cut_to_fit_when_possible():
    """Realistischer Fall, in dem das Kuerzen tatsaechlich unter das Zeitbudget kommt -
    dann muss der frueher genutzte Text stehen, nicht die Warnung."""
    out, notes, per_session = volume.targets("einsteiger", "aufbau", focus=(), days=2, minutes=85)
    total = sum(out.values())
    cap = 2 * per_session
    assert total <= cap
    assert any("mehr passt nicht in deine Zeit" in n for n in notes)
    assert not any("Selbst mit Kürzung" in n for n in notes)


def test_targets_without_shortage_has_no_cut_note():
    out, notes, per_session = volume.targets("einsteiger", "aufbau", focus=(), days=5, minutes=90)
    assert sum(out.values()) <= 5 * per_session
    assert not any("Kürzung" in n or "passt nicht" in n for n in notes)
