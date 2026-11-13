"""Kaltstart gebuendelt: Startgewichte und Einstufung laden ihre Daten mit einer festen Zahl an
Abfragen statt je Uebung. Ergebnis muss dem frueheren Rechnen je Uebung entsprechen."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.planner import cold_start, progression


@pytest.fixture
def zaehler(monkeypatch):
    """Zaehlt SELECT-Abfragen waehrend des Tests."""
    stand = {"selects": 0}
    echt = sqlite3.connect

    def connect(*a, **kw):
        conn = echt(*a, **kw)
        conn.set_trace_callback(
            lambda sql: stand.__setitem__("selects", stand["selects"] + sql.lstrip().upper().startswith("SELECT")))
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", connect)
    stand["reset"] = lambda: stand.update(selects=0)
    return stand


def _alt_starting_weight(profile_id, exercise_id):
    """Die fruehere Berechnung je Uebung (Abfragen je Uebung), als Massstab fuer das Ergebnis."""
    ex = db.one("SELECT pattern, equipment_type FROM exercises WHERE id=?", (exercise_id,))
    profile = db.get_row("profiles", profile_id) or {}
    if not ex or ex["equipment_type"] == "bodyweight":
        return None, None
    estimates = []
    for o in db.rows("SELECT id, equipment_type FROM exercises WHERE pattern=? AND id<>? AND archived=0",
                     (ex["pattern"], exercise_id)):
        est = progression.estimate(progression.sessions(profile_id, o["id"], limit=12))
        if est and est[0]:
            estimates.append(est[0] * cold_start.EQUIPMENT_FACTOR.get(ex["equipment_type"], 1.0)
                             / cold_start.EQUIPMENT_FACTOR.get(o["equipment_type"], 1.0))
    e1rm, source = (max(estimates), "aus deiner ähnlichen Übung") if estimates else (None, None)
    if not e1rm:
        e1rm = cold_start.prior_e1rm(profile, cold_start.bodyweight_of(profile_id, profile),
                                     ex["pattern"], ex["equipment_type"])
        source = "Schätzung aus Körpergewicht"
    if not e1rm:
        return None, None
    novice = (profile.get("experience") or "einsteiger") == "einsteiger"
    raw = e1rm * (0.5 if novice else 0.6)
    step = progression.step_for(ex["equipment_type"], ex["pattern"], raw)
    kg = max(progression.floor_for(ex["equipment_type"], step, ex["pattern"]),
             progression._round_to(raw, step if step <= 2.5 else 2.5))
    return kg, f"{source} – wird nach zwei Einheiten genauer (±25 %)"


def _ids(sql, args=()):
    return [r["id"] for r in db.rows(sql, args)]


def _trainiere(profile_id, exercise_id, kg, einheiten=4):
    start = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(einheiten):
        at = (start + timedelta(days=7 * i)).isoformat()
        db.save_workout({"profile_id": profile_id, "title": "T", "started_at": at, "finished_at": at,
                         "sets": [{"exercise_id": exercise_id, "set_no": n + 1, "reps": 8, "weight": kg + i,
                                   "rpe": 8} for n in range(3)]})


def test_gebuendelt_gleich_wie_je_uebung(profile_id):
    """Alle Katalog-Uebungen: mit trainierten Uebungen einzelner Muster, Koerpergewicht und Erfahrung."""
    db.update_row("profiles", profile_id, {"sex": "weiblich", "birth_year": 1980, "experience": "fortgeschritten"})
    db.set_bodyweight(profile_id, "2026-01-01", 64.0)
    bank = _ids("SELECT id FROM exercises WHERE pattern='h_push' AND equipment_type='barbell' AND archived=0")[0]
    lat = _ids("SELECT id FROM exercises WHERE pattern='v_pull' AND equipment_type='cable' AND archived=0")[0]
    _trainiere(profile_id, bank, 40)
    _trainiere(profile_id, lat, 35)

    alle = _ids("SELECT id FROM exercises ORDER BY id")
    neu = cold_start.starting_weights(profile_id, alle + [999999])
    assert neu[999999] == (None, None)                                   # unbekannte Uebung
    for ex_id in alle:
        assert neu[ex_id] == _alt_starting_weight(profile_id, ex_id), ex_id
        assert cold_start.starting_weight(profile_id, ex_id) == neu[ex_id]
    # die trainierte Bank zaehlt fuer andere Druecken-Uebungen, nicht fuer sich selbst
    andere = _ids("SELECT id FROM exercises WHERE pattern='h_push' AND id<>? AND archived=0"
                  " AND equipment_type<>'bodyweight'", (bank,))
    assert all("ähnlichen" in neu[i][1] for i in andere)
    assert "Körpergewicht" in neu[bank][1]


def test_feste_zahl_an_abfragen_fuer_frischen_plan(profile_id, zaehler):
    """Frischer Plan ohne Verlauf: der Kontext fuer 20 Uebungen kostet nicht mehr als fuer 2."""
    db.set_bodyweight(profile_id, "2026-01-01", 80.0)
    _trainiere(profile_id, _ids("SELECT id FROM exercises WHERE pattern='squat' AND archived=0")[0], 60)
    ids = _ids("SELECT id FROM exercises WHERE archived=0 AND equipment_type<>'bodyweight'"
               " AND pattern IS NOT NULL ORDER BY id")[:20]

    zaehler["reset"]()
    progression.context(profile_id, ids[:2])
    wenige = zaehler["selects"]
    zaehler["reset"]()
    ctx = progression.context(profile_id, ids)
    viele = zaehler["selects"]
    assert viele == wenige <= 9, (wenige, viele)

    # for_exercise mit Kontext laedt nichts mehr nach und nutzt das gebuendelte Startgewicht
    zaehler["reset"]()
    for ex_id in ids:
        rec = progression.for_exercise(profile_id, ex_id, 8, 12, 3, ctx=ctx)
        kg = ctx["starts"][ex_id][0] if not ctx["history"][ex_id] else None
        if kg:
            assert rec["weight"] == kg
    assert zaehler["selects"] == 0


def test_einstufung_mit_fester_zahl_an_abfragen(profile_id, zaehler):
    """infer_experience laedt den Verlauf aller Grunduebungen in einer Abfrage, Ergebnis unveraendert."""
    db.set_bodyweight(profile_id, "2026-01-01", 80.0)
    for pattern, kg in (("squat", 140), ("hinge", 160), ("h_push", 110)):
        ex = _ids("SELECT id FROM exercises WHERE pattern=? AND equipment_type='barbell' AND archived=0",
                  (pattern,))[0]
        _trainiere(profile_id, ex, kg, einheiten=5)
    zaehler["reset"]()
    level, why = cold_start.infer_experience(profile_id)
    assert level == "erfahren" and why
    assert zaehler["selects"] <= 6, zaehler["selects"]
