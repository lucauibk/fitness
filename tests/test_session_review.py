"""Auswertung einer Einheit: Ersatzübungen, Muskelbilanz, Vergleich mit Reihenfolge.
Der Aufbau (Übungen mit Muster, Trainingstag, Vor-Einheit) ist aufwendiger als bei den anderen
Rechen-Tests, deshalb die Helfer hier lokal statt in conftest.py."""
import os
from datetime import datetime, timedelta, timezone

from app import db, session_review

START = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)


def _exercise(name, pattern, kind="compound", group="brust", unit="kg"):
    with db.connect() as c:
        return c.execute("INSERT INTO exercises (name, muscle_group, unit, kind, pattern)"
                         " VALUES (?,?,?,?,?)",
                         (f"{name}-{os.urandom(4).hex()}", group, unit, kind, pattern)).lastrowid


def _name(ex):
    return db.get_row("exercises", ex)["name"]


def _day(profile_id, items):
    """items: (exercise_id, target_sets, reps_min, reps_max) je geplanter Übung."""
    with db.connect() as c:
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at)"
                          " VALUES (?,?,1,?)", (profile_id, "Test", db.now())).lastrowid
        day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,?)",
                        (split, "Push")).lastrowid
        for pos, (ex, sets, lo, hi) in enumerate(items):
            c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position,"
                      " target_sets, target_reps_min, target_reps_max) VALUES (?,?,?,?,?,?)",
                      (day, ex, pos, sets, lo, hi))
        return day


def _sets(exercise_id, count, reps=10, weight=50.0, rpe=8.0, warmup=False):
    return [{"exercise_id": exercise_id, "set_no": i + 1, "reps": reps, "weight": weight,
             "rpe": rpe, "warmup": warmup} for i in range(count)]


def _workout(profile_id, sets, day_id=None, start=START, minutes=None):
    work = [s for s in sets if not s.get("warmup")]
    minutes = minutes if minutes is not None else 8 + 3.2 * max(1, len(work))
    workout, _ = db.save_workout({
        "profile_id": profile_id, "split_day_id": day_id, "started_at": start.isoformat(),
        "finished_at": (start + timedelta(minutes=minutes)).isoformat(), "sets": sets})
    return workout


def _texts(facts):
    return " | ".join(h["text"] for h in facts["hints"])


def _ex(facts, ex):
    return next(e for e in facts["exercises"] if e["exercise_id"] == ex)


def test_ersatz_mit_gleichem_muster_ist_kein_auslassen(profile_id):
    plan = _exercise("Brustpresse schraeg", "h_push")
    instead = _exercise("Schraegbank LH", "h_push")
    day = _day(profile_id, [(plan, 3, 8, 12)])
    f = session_review.facts(profile_id, _workout(profile_id, _sets(instead, 3), day))
    assert _ex(f, instead)["substitute_for"] == _name(plan)
    assert f["skipped"] == []
    assert "Ausgelassen" not in _texts(f)


def test_ersatz_ueber_muskelgruppe_wenn_kein_gleiches_muster(profile_id):
    latzug = _exercise("Latzug", "v_pull", group="ruecken")
    rudern = _exercise("Rudern brustgestuetzt", "h_pull", group="ruecken")
    day = _day(profile_id, [(latzug, 3, 8, 12)])
    f = session_review.facts(profile_id, _workout(profile_id, _sets(rudern, 3), day))
    assert _ex(f, rudern)["substitute_for"] == _name(latzug)


def test_exakte_uebung_geht_vor_ersatz(profile_id):
    """Steht die geplante Übung selbst in der Einheit, wird sie nicht als Ersatz für eine
    andere geplante Übung desselben Musters verbraucht."""
    a, b = _exercise("Bank", "h_push"), _exercise("Presse", "h_push")
    day = _day(profile_id, [(a, 3, 8, 12), (b, 3, 8, 12)])
    f = session_review.facts(profile_id, _workout(profile_id, _sets(b, 3) + _sets(a, 3), day))
    assert _ex(f, a)["substitute_for"] is None and _ex(f, b)["substitute_for"] is None


def test_wirklich_ausgelassen_und_muskel_ohne_saetze(profile_id):
    bank = _exercise("Bank", "h_push")
    seit = _exercise("Seitheben", "lateral", kind="isolation", group="schultern")
    day = _day(profile_id, [(bank, 3, 8, 12), (seit, 3, 12, 20)])
    f = session_review.facts(profile_id, _workout(profile_id, _sets(bank, 3), day))
    assert [s["name"] for s in f["skipped"]] == [_name(seit)]
    assert f["hints"][0]["key"] == "plan" and "Schultern" in f["hints"][0]["text"]
    schultern = next(m for m in f["muscles"] if m["group"] == "schultern")
    assert (schultern["done"], schultern["planned"]) == (0, 3)
    assert f["headline"].startswith("3 von 6 Sätzen")


def test_ungeplante_uebung_wird_gezaehlt_aber_nicht_als_ersatz(profile_id):
    bank = _exercise("Bank", "h_push")
    curl = _exercise("Curl", "biceps", kind="isolation", group="arme")
    day = _day(profile_id, [(bank, 3, 8, 12)])
    f = session_review.facts(profile_id, _workout(profile_id, _sets(bank, 3) + _sets(curl, 2), day))
    assert _ex(f, curl)["planned"] is False
    assert next(m for m in f["muscles"] if m["group"] == "bizeps")["done"] == 2


def test_spaetere_position_erklaert_kleinen_rueckgang(profile_id):
    a, b = _exercise("Schraegbank", "h_push"), _exercise("Presse", "h_push")
    _workout(profile_id, _sets(b, 2, reps=10, weight=70, rpe=None), start=START - timedelta(days=7))
    # heute erst a, dann b mit einer Wiederholung weniger (~2,4 % e1RM)
    jetzt = _workout(profile_id, _sets(a, 3) + _sets(b, 2, reps=9, weight=70, rpe=None))
    c = _ex(session_review.facts(profile_id, jetzt), b)["compare"]
    assert (c["position"], c["verdict"], c["order_note"]) == (1, "hold", True)


def test_gleiche_position_gleicher_rueckgang_ist_rueckschritt(profile_id):
    b = _exercise("Presse", "h_push")
    _workout(profile_id, _sets(b, 2, reps=10, weight=70, rpe=None), start=START - timedelta(days=7))
    jetzt = _workout(profile_id, _sets(b, 2, reps=9, weight=70, rpe=None))
    assert _ex(session_review.facts(profile_id, jetzt), b)["compare"]["verdict"] == "down"


def test_steigerung_und_bestleistung_erst_ab_zwei_frueheren_einheiten(profile_id):
    b = _exercise("Presse", "h_push")
    _workout(profile_id, _sets(b, 2, reps=8, weight=70, rpe=None), start=START - timedelta(days=14))
    zweite = _workout(profile_id, _sets(b, 2, reps=10, weight=70, rpe=None), start=START - timedelta(days=7))
    e = _ex(session_review.facts(profile_id, zweite), b)
    assert e["compare"]["verdict"] == "up" and e["pr"] is False      # nur eine Einheit davor
    dritte = _workout(profile_id, _sets(b, 2, reps=12, weight=70, rpe=None))
    assert _ex(session_review.facts(profile_id, dritte), b)["pr"] is True


def test_aufwaermen_nur_vor_erster_grunduebung_je_muskel(profile_id):
    a, b = _exercise("Schraegbank", "h_push"), _exercise("Presse", "h_push")
    row = _exercise("Rudern", "h_pull", group="ruecken")
    sets = _sets(a, 1, weight=20, warmup=True) + _sets(a, 3) + _sets(b, 2) + _sets(row, 3)
    f = session_review.facts(profile_id, _workout(profile_id, sets))
    warm = [h["text"] for h in f["hints"] if h["key"] == "warmup"]
    assert len(warm) == 1 and _name(row) in warm[0] and _name(b) not in warm[0]


def test_wiederholungen_nur_erster_satz_zaehlt_und_nicht_fuer_ersatz(profile_id):
    bank = _exercise("Bank", "h_push")
    abd = _exercise("Abduktoren", "glutes", kind="isolation", group="beine")
    hip = _exercise("Hip Thrust", "glutes", kind="isolation", group="beine")
    day = _day(profile_id, [(bank, 3, 8, 12), (abd, 3, 12, 20)])
    # Bank: Abfall in späteren Sätzen ist normal; Hip Thrust hat einen eigenen Bereich
    sets = (_sets(bank, 1, reps=10) + _sets(bank, 2, reps=6) + _sets(hip, 3, reps=8))
    f = session_review.facts(profile_id, _workout(profile_id, sets, day))
    assert not [h for h in f["hints"] if h["key"] == "reps"]
    zu_schwer = _workout(profile_id, _sets(bank, 3, reps=6), day, start=START + timedelta(days=2))
    assert "Gewicht zu hoch" in _texts(session_review.facts(profile_id, zu_schwer))


def test_freies_training_ohne_plan(profile_id):
    bank = _exercise("Bank", "h_push")
    f = session_review.facts(profile_id, _workout(profile_id, _sets(bank, 3)))
    assert f["planned"] is False and f["sets_planned"] is None and f["skipped"] == []
    assert f["headline"].startswith("3 Sätze · Brust 3")


def test_ohne_rpe_hinweis_statt_abzug(profile_id):
    bank = _exercise("Bank", "h_push")
    f = session_review.facts(profile_id, _workout(profile_id, _sets(bank, 3, rpe=None)))
    assert any(h["key"] == "rpe" for h in f["hints"]) and f["avg_rpe"] is None


def test_leere_einheit(profile_id):
    f = session_review.facts(profile_id, {"id": 0, "sets": []})
    assert f["sets_done"] == 0 and f["exercises"] == []


def test_slot_sagt_ob_frueher_oder_spaeter(profile_id):
    a, b = _exercise("Schraegbank", "h_push"), _exercise("Presse", "h_push")
    _workout(profile_id, _sets(a, 2) + _sets(b, 2), start=START - timedelta(days=7))
    jetzt = _workout(profile_id, _sets(b, 2) + _sets(a, 2))
    f = session_review.facts(profile_id, jetzt)
    assert _ex(f, b)["compare"]["slot"] == "frueher" and _ex(f, a)["compare"]["slot"] == "spaeter"
