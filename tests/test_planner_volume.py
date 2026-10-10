from app.planner import volume_plan as vp


def test_targets_honest_when_capacity_never_reaches_landmarks():
    """2 Einheiten x 30 min reichen nicht einmal fuer die Erhaltungsmengen - das Volumen wird
    gekuerzt, soweit es geht, und der Hinweis sagt es."""
    out, notes, per_session = vp.weekly_targets("erfahren", "aufbau", (), days=2, minutes=30)
    assert notes and "gekürzt" in notes[0] and "mehr Zeit" in notes[0]
    assert sum(out.values()) > 2 * per_session          # ehrlich: es passt nicht ganz


def test_targets_without_shortage_have_no_cut_note():
    out, notes, per_session = vp.weekly_targets("einsteiger", "aufbau", (), days=5, minutes=90)
    assert not notes and sum(out.values()) <= 5 * per_session


def test_rir_ramp_ends_with_deload():
    for weeks in (5, 6):
        rir = vp.rir_by_week(weeks)
        assert len(rir) == weeks and rir[-1] == vp.DELOAD_RIR and rir[0] >= rir[-2]


def test_age_over_50_reduces_volume():
    plain = vp.block_targets("fortgeschritten", "aufbau")
    older = vp.block_targets("fortgeschritten", "aufbau", age=55)
    assert sum(max(v) for v in older.values()) < sum(max(v) for v in plain.values())
