"""Katalog-Integritaet: jede Uebung ist fuer die Auswahl brauchbar."""
from app import db
from app.planner import muscles
from app.planner.catalog_data import EQUIPMENT, EXERCISES


def test_every_pattern_has_at_least_two_options_and_a_group():
    by_pattern = {}
    for e in EXERCISES:
        by_pattern.setdefault(e["pattern"], []).append(e)
    assert set(by_pattern) == set(muscles.PATTERNS)
    for pattern, items in by_pattern.items():
        assert len(items) >= 2, pattern
        assert muscles.group_of(pattern) in muscles.GROUPS


def test_catalog_fields_are_valid():
    seen = set()
    for e in EXERCISES:
        assert e["name"] not in seen
        seen.add(e["name"])
        assert e["equipment_type"] in EQUIPMENT
        assert e["difficulty"] in (1, 2, 3) and 1 <= e["sfr"] <= 5
        for tag in (e["contraindications"] or "").split(","):
            assert not tag or tag in muscles.INJURY_AREAS, (e["name"], tag)


def test_every_injury_area_still_leaves_options_for_each_muscle_group():
    """Einzelne Muster duerfen bei Beschwerden wegfallen (Kniebeuge bei Knie), die Muskelgruppe
    aber nie ohne Uebung bleiben."""
    for area in muscles.INJURY_AREAS:
        for group in muscles.GROUPS:
            ok = [e for e in EXERCISES if muscles.group_of(e["pattern"]) == group
                  and area not in (e["contraindications"] or "").split(",")]
            assert ok, (area, group)


def test_seeded_into_database_without_losing_existing_ids():
    rows = db.rows("SELECT name, pattern, equipment_type, sfr FROM exercises WHERE pattern IS NOT NULL")
    names = {r["name"] for r in rows}
    assert {e["name"] for e in EXERCISES} <= names
    assert all(r["equipment_type"] and r["sfr"] for r in rows)
    bench = db.one("SELECT id FROM exercises WHERE name='Bankdrücken'")
    db.init()   # erneuter Start aendert nichts
    assert db.one("SELECT id FROM exercises WHERE name='Bankdrücken'")["id"] == bench["id"]


def test_restart_does_not_duplicate_or_resurrect_exercises():
    before = db.one("SELECT COUNT(*) AS n FROM exercises")["n"]
    db.init()
    db.init()
    assert db.one("SELECT COUNT(*) AS n FROM exercises")["n"] == before
    # eine bewusst geloeschte Uebung bleibt weg
    ex = db.one("SELECT id FROM exercises WHERE name='Arnold Press'")
    db.delete_row("exercises", ex["id"])
    db.init()
    assert db.one("SELECT id FROM exercises WHERE name='Arnold Press'") is None
