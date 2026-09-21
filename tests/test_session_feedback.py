"""Urteil ueber eine Einheit. Der Aufbau (Uebungen, Trainingstag, Vor-Einheit) ist aufwendiger
als bei den anderen Rechen-Tests, deshalb die Helfer hier lokal statt in conftest.py."""
import os
from datetime import datetime, timedelta, timezone

from app import db, session_feedback

START = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)


def _exercise(name, kind="isolation", group="brust", unit="kg"):
    with db.connect() as c:
        cur = c.execute("INSERT INTO exercises (name, muscle_group, unit, kind) VALUES (?,?,?,?)",
                        (f"{name}-{os.urandom(4).hex()}", group, unit, kind))
        return cur.lastrowid


def _day(profile_id, items):
    """items: (exercise_id, target_sets, reps_min, reps_max) je geplanter Uebung."""
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
    """Dauer standardmaessig so, dass das Dauer-Kriterium zufrieden ist und die anderen
    Tests nicht verrauscht."""
    arbeit = [s for s in sets if not s.get("warmup")]
    if minutes is None:
        minutes = 8 + 3.2 * max(1, len(arbeit))
    workout, _ = db.save_workout({
        "profile_id": profile_id, "split_day_id": day_id,
        "started_at": start.isoformat(),
        "finished_at": (start + timedelta(minutes=minutes)).isoformat(),
        "sets": sets})
    return workout


def _texts(result):
    return " | ".join(m["text"] for m in result["missing"])


def test_plan_erfuellt_und_gesteigert_gibt_hohen_score(profile_id):
    ex = _exercise("Bankdruecken", kind="compound")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    _workout(profile_id, _sets(ex, 3, reps=10, weight=50), day, start=START - timedelta(days=7))
    jetzt = _workout(profile_id, _sets(ex, 1, weight=30, warmup=True) + _sets(ex, 3, reps=10, weight=55),
                     day)

    res = session_feedback.evaluate(profile_id, jetzt)
    assert res["missing"] == [], _texts(res)
    assert res["score"] >= 85
    assert res["label"] == "stark"


def test_ausgelassene_saetze_werden_benannt(profile_id):
    ex = _exercise("Beinpresse")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 2), day)

    res = session_feedback.evaluate(profile_id, w)
    assert "2 von 3 Sätzen" in _texts(res)
    assert res["score"] < 100


def test_ganz_ausgelassene_uebung_wird_benannt(profile_id):
    a, b = _exercise("Rudern"), _exercise("Curls")
    day = _day(profile_id, [(a, 3, 8, 12), (b, 3, 8, 12)])
    w = _workout(profile_id, _sets(a, 3), day)

    res = session_feedback.evaluate(profile_id, w)
    assert "ganz ausgelassen" in _texts(res)


def test_zu_wenig_wiederholungen_meldet_zu_hohes_gewicht(profile_id):
    ex = _exercise("Kniebeuge")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3, reps=5), day)

    res = session_feedback.evaluate(profile_id, w)
    assert "Gewicht zu hoch" in _texts(res)


def test_gleiche_leistung_wie_vorwoche_meldet_fehlenden_fortschritt(profile_id):
    ex = _exercise("Schulterdruecken")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    _workout(profile_id, _sets(ex, 3, reps=10, weight=40), day, start=START - timedelta(days=7))
    jetzt = _workout(profile_id, _sets(ex, 3, reps=10, weight=40), day)

    res = session_feedback.evaluate(profile_id, jetzt)
    assert "kein Fortschritt" in _texts(res)


def test_aeltere_einheit_wird_gegen_ihre_vorgaengerin_gemessen(profile_id):
    """Regressionstest: stats.last_sets() wuerde immer die *neueste* Einheit liefern - die
    mittlere Einheit wuerde dann an der spaeteren gemessen und faelschlich als Rueckschritt
    dastehen, obwohl sie gegenueber ihrer Vorgaengerin gesteigert hat."""
    ex = _exercise("Kreuzheben")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    _workout(profile_id, _sets(ex, 3, weight=100), day, start=START - timedelta(days=14))
    mitte = _workout(profile_id, _sets(ex, 3, weight=105), day, start=START - timedelta(days=7))
    _workout(profile_id, _sets(ex, 3, weight=110), day, start=START)

    res = session_feedback.evaluate(profile_id, mitte)
    assert "kein Fortschritt" not in _texts(res)


def test_grunduebung_ohne_aufwaermsatz_wird_gemeldet(profile_id):
    """Sichert ab, dass das fehlende `kind` in db.get_workout() das Kriterium nicht
    stillschweigend aushebelt - der Befund muss wirklich erscheinen."""
    ex = _exercise("Frontkniebeuge", kind="compound")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3), day)

    res = session_feedback.evaluate(profile_id, w)
    assert "kein Aufwärmsatz" in _texts(res)


def test_isolationsuebung_braucht_keinen_aufwaermsatz(profile_id):
    ex = _exercise("Seitheben", kind="isolation")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3), day)

    assert "Aufwärmsatz" not in _texts(session_feedback.evaluate(profile_id, w))


def test_freies_training_ohne_plan_wird_bewertet_statt_bestraft(profile_id):
    ex = _exercise("Klimmzuege", unit="koerper")
    w = _workout(profile_id, _sets(ex, 3, weight=None))

    res = session_feedback.evaluate(profile_id, w)
    assert res["score"] > 0
    assert all(m["key"] not in ("plan", "reps") for m in res["missing"])


def test_ohne_rpe_gibt_es_einen_hinweis_aber_keinen_totalabzug(profile_id):
    ex = _exercise("Latzug")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3, rpe=None), day)

    res = session_feedback.evaluate(profile_id, w)
    assert "Anstrengung" in _texts(res)
    assert res["score"] >= 50


def test_einheit_ohne_arbeitssaetze_faellt_durch(profile_id):
    ex = _exercise("Dips")
    w = _workout(profile_id, _sets(ex, 2, warmup=True))

    res = session_feedback.evaluate(profile_id, w)
    assert res["score"] == 0
    assert res["label"] == "mager"


def test_freies_training_wird_nicht_durch_die_normierung_abgestraft(profile_id):
    """Ohne Plan fallen zwei Kriterien heraus. Ohne die Untergrenze BASIS wuerden die
    verbliebenen das Dreifache wiegen und ein ordentliches Training auf "mager" druecken."""
    ex = _exercise("Rudern Langhantel", kind="compound")
    w = _workout(profile_id, _sets(ex, 3, reps=10, weight=60, rpe=None))

    res = session_feedback.evaluate(profile_id, w)
    assert res["score"] >= 50, _texts(res)
    assert res["label"] != "mager"


def test_kurze_einheit_wird_nicht_an_einer_hochrechnung_gemessen(profile_id):
    """Bei drei geloggten Saetzen taugt die Schaetzung aus der Satzzahl nicht als Massstab -
    sonst gilt jede normale Trainingsdauer als 'zu lang'."""
    ex = _exercise("Beinbeuger")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3), day, minutes=40)

    assert "Minuten" not in _texts(session_feedback.evaluate(profile_id, w))


def test_mit_hinterlegter_wunschdauer_wird_die_laenge_beurteilt(profile_id):
    ex = _exercise("Beinstrecker")
    with db.connect() as c:
        c.execute("UPDATE profiles SET session_minutes=60 WHERE id=?", (profile_id,))
    day = _day(profile_id, [(ex, 3, 8, 12)])
    w = _workout(profile_id, _sets(ex, 3), day, minutes=15)

    assert "statt rund 60 Minuten" in _texts(session_feedback.evaluate(profile_id, w))


def test_sofort_eingetippte_einheit_meldet_keine_null_minuten(profile_id):
    """"nur 0 statt rund 40 Minuten" liest sich wie ein Fehler - ist aber nur eine
    nachtraeglich eingetippte Einheit."""
    ex = _exercise("Butterfly")
    with db.connect() as c:
        c.execute("UPDATE profiles SET session_minutes=60 WHERE id=?", (profile_id,))
    w = _workout(profile_id, _sets(ex, 3), minutes=0.2)

    texte = _texts(session_feedback.evaluate(profile_id, w))
    assert "keine ganze Minute" in texte
    assert "nur 0" not in texte


def test_nachgesendete_einheit_vergleicht_nach_datum_nicht_nach_reihenfolge(profile_id):
    """Der Ausgang sendet nach: eine Montags-Einheit kann erst Mittwoch im Server landen,
    also *nach* der Dienstags-Einheit. Verglichen werden muss trotzdem nach Datum -
    sonst misst sich die nachgereichte Einheit an einer, die spaeter stattfand."""
    ex = _exercise("Nackendruecken")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    _workout(profile_id, _sets(ex, 3, weight=50), day, start=START - timedelta(days=2))
    # Dienstag - kommt zuerst an
    _workout(profile_id, _sets(ex, 3, weight=70), day, start=START)
    # Montag - wird nachgesendet, hat also die hoehere id bei frueherem Datum
    montag = _workout(profile_id, _sets(ex, 3, weight=60), day, start=START - timedelta(days=1))

    res = session_feedback.evaluate(profile_id, montag)
    # Gegen Sonntag (50 kg) ist 60 kg ein Fortschritt; gegen Dienstag (70 kg) waere es keiner.
    assert "kein Fortschritt" not in _texts(res), _texts(res)


def test_heute_zeigt_die_heutige_einheit_mit_punktzahl(profile_id):
    """Die Erledigt-Karte auf "Heute" braucht Tag, Arbeitssaetze und Punktzahl der Einheit von heute."""
    from app import daily_view, stats
    ex = _exercise("Heute-Bank")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    w = _workout(profile_id, _sets(ex, 1, warmup=True) + _sets(ex, 3), day, start=start)

    tw = daily_view.today_workout(profile_id, stats.today_view(profile_id)["last_workout"])
    assert tw["id"] == w["id"]
    assert tw["sets"] == 3            # Aufwaermsatz zaehlt nicht
    assert tw["day_label"] == "Push"
    assert 0 < tw["score"] <= 100


def test_einheit_von_gestern_ist_nicht_heute_erledigt(profile_id):
    from app import daily_view, stats
    ex = _exercise("Gestern-Bank")
    day = _day(profile_id, [(ex, 3, 8, 12)])
    _workout(profile_id, _sets(ex, 3), day, start=datetime.now(timezone.utc) - timedelta(days=2))

    assert daily_view.today_workout(profile_id, stats.today_view(profile_id)["last_workout"]) is None


def test_gebuendelte_hinweise_stehen_in_einer_zeile(profile_id):
    a = _exercise("Kniebeuge", kind="compound")
    b = _exercise("Kreuzheben", kind="compound")
    w = _workout(profile_id, _sets(a, 3) + _sets(b, 3))

    warm = [m for m in session_feedback.evaluate(profile_id, w)["missing"] if m["key"] == "warmup"]
    assert len(warm) == 1
    assert "kein Aufwärmsatz" in warm[0]["text"]


def test_1rm_wird_ueber_12_wiederholungen_nicht_angezeigt(profile_id):
    """Aus 20 Wdh. hochgerechnet ist die Schaetzung wertlos. Die Bestleistung bleibt trotzdem
    der Satz mit dem hoechsten Wert – die Rangfolge aendert sich nicht."""
    from app import stats
    ex = _exercise("Wadenheben-1rm")
    _workout(profile_id, _sets(ex, 1, reps=20, weight=60))
    best = next(b for b in stats.best_per_exercise(profile_id) if b["exercise_id"] == ex)
    assert best["reps"] == 20
    assert best["one_rm"] is None

    ex2 = _exercise("Bank-1rm")
    _workout(profile_id, _sets(ex2, 1, reps=10, weight=60))
    best2 = next(b for b in stats.best_per_exercise(profile_id) if b["exercise_id"] == ex2)
    assert best2["one_rm"] == 80.0
