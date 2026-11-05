"""Morgen-Coach: hoechstens eine Nachricht pro Lauf, streng geprueft, und der Lauf schreibt nur
in coach_messages – nie in den Plan."""
import json
import os
from datetime import timedelta

import pytest

from app import coach, coach_chat, db
from app.planner import blocks


def _plan(profile_id):
    with db.connect() as c:
        ex = c.execute("INSERT INTO exercises (name, muscle_group, unit, kind) VALUES (?,?,?,?)",
                       (f"Bank-{os.urandom(4).hex()}", "brust", "kg", "compound")).lastrowid
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "Test", db.now())).lastrowid
        day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,?)",
                        (split, "Push")).lastrowid
        c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, target_sets,"
                  " target_reps_min, target_reps_max) VALUES (?,?,0,3,8,12)", (day, ex))
    return ex, db.get_row("exercises", ex)["name"], split


def _answer(message, impuls="Heute UK: Hip Thrust zuerst."):
    body = json.dumps({"impuls": impuls, "message": message}, ensure_ascii=False)
    return f"<analyse>\n- Woche 1 [noch wenig Daten], {{kein JSON}}\n</analyse>\n<nachricht>{body}</nachricht>"


def test_parse_message_and_impulse():
    assert coach.parse(_answer("Stark: 3 von 3 Einheiten diese Woche.")) == \
        {"impuls": "Heute UK: Hip Thrust zuerst.", "message": "Stark: 3 von 3 Einheiten diese Woche.",
         "aktionen": []}


def test_parse_nothing_new():
    assert coach.parse(_answer(None))["message"] is None
    assert coach.parse(_answer("   "))["message"] is None
    assert coach.parse(_answer(None, impuls=None)) == {"impuls": None, "message": None, "aktionen": []}


def test_parse_actions_only_with_message():
    raw = '<nachricht>{"impuls": "i", "message": "Tausch?", "aktionen": [{"typ": "ersetzen", "plan_id": 1, "neu": "X"}]}</nachricht>'
    assert coach.parse(raw)["aktionen"] == [{"typ": "ersetzen", "plan_id": 1, "neu": "X"}]
    raw = '<nachricht>{"impuls": "i", "message": null, "aktionen": [{"typ": "ersetzen", "plan_id": 1, "neu": "X"}]}</nachricht>'
    assert coach.parse(raw)["aktionen"] == []


def test_parse_clips_long_texts():
    r = coach.parse(_answer("wort " * 400, impuls="lang " * 100))
    assert len(r["message"]) == coach.MAX_TEXT and r["message"].endswith("…")
    assert len(r["impuls"]) == coach.MAX_IMPULSE


@pytest.mark.parametrize("raw", ["", "kein json", "<nachricht>[1, 2]</nachricht>",
                                 '<nachricht>{"notes": []}</nachricht>'])
def test_parse_rejects_garbage(raw):
    with pytest.raises(ValueError):
        coach.parse(raw)


def test_parse_ignores_braces_in_analysis():
    # Die Analyse enthaelt "{kein JSON}" – geparst wird nur der <nachricht>-Block.
    assert coach.parse(_answer("ok"))["message"] == "ok"


def test_context_block_and_recent_messages(profile_id):
    _, name, split = _plan(profile_id)
    blocks.create(profile_id, split, "aufbau", "fortgeschritten")
    coach_chat.add_message(profile_id, "user", "Warum stagniert mein Bankdrücken?")
    coach_chat.add_message(profile_id, "coach", "Weil …")
    coach_chat.add_message(profile_id, "coach", "kaputt", status="error")
    ctx = coach.context(profile_id)
    assert ctx["block"]["woche"] == 1 and ctx["block"]["einheiten_seit_start"] == 0
    assert ctx["plan"]["tage"][0]["uebungen"][0]["name"] == name
    assert [m["von"] for m in ctx["letzte_nachrichten"]] == ["person", "du"]
    assert ctx["tage_seit_letzter_einheit"] is None
    assert "Warum stagniert" in coach.prompt(ctx)
    assert ctx["heute_geplant"]["tag"] == "Push" and ctx["heute_geplant"]["heute_schon_trainiert"] is False
    assert ctx["letzte_impulse"] == [] and ctx["weggeklickt"] == []


def test_context_leaves_plan_untouched(profile_id):
    _, _, split = _plan(profile_id)
    blocks.create(profile_id, split, "aufbau", "fortgeschritten")
    tables = ("splits", "split_days", "split_exercises", "workouts", "sets", "blocks", "plan_events")

    def snapshot():
        return {t: db.rows(f"SELECT * FROM {t} ORDER BY id") for t in tables}

    before = snapshot()
    coach.context(profile_id)
    coach_chat.add_message(profile_id, "coach", coach.parse(_answer("x"))["message"], source="daily")
    coach_chat.set_impulse(profile_id, coach.parse(_answer("x"))["impuls"])
    assert snapshot() == before


def test_context_without_plan(profile_id):
    assert coach.context(profile_id) is None


def test_absaetze_bleiben_in_der_nachricht():
    r = coach.parse(_answer("Erster Absatz.\n\n\n**Zweiter**   Absatz\nmit Zeile.\n\n- Punkt"))
    assert r["message"] == "Erster Absatz.\n\n**Zweiter** Absatz\nmit Zeile.\n\n- Punkt"
    # Der Impuls bleibt einzeilig
    assert "\n" not in coach.parse(_answer("x", impuls="a\n\nb"))["impuls"]


def _day_plan(profile_id, weekdays):
    with db.connect() as c:
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "W", db.now())).lastrowid
        ids = []
        for i, (label, wd) in enumerate(weekdays):
            ids.append(c.execute("INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                                 (split, i, label, wd)).lastrowid)
        ex = c.execute("INSERT INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                       (f"X-{os.urandom(3).hex()}", "beine", "kg")).lastrowid
        for d in ids:
            c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position) VALUES (?,?,0)", (d, ex))
    return ids, ex


def test_heute_wie_startseite_und_woche(profile_id):
    from app import stats
    today = stats.today()
    # B liegt nach heute: ein schon vergangener B-Tag waere "nachgeholt" und stuende heute vorne
    days = [("A", str(today.weekday()))] + ([("B", str(today.weekday() + 1))] if today.weekday() < 6 else [])
    _day_plan(profile_id, days)
    ctx = coach.context(profile_id)
    day, fixed, _ = stats.schedule_day(profile_id, db.active_split(profile_id), today)
    assert ctx["heute_geplant"]["tag"] == day["label"] == "A" and ctx["heute_geplant"]["wie_geplant"] is fixed
    assert ctx["diese_woche"]["geplant"] == len(days) and ctx["diese_woche"]["erledigt"] == 0


def test_woche_zaehlt_erledigte_und_pause(profile_id):
    from datetime import datetime, time as dtime, timezone
    from app import stats
    today = stats.today()
    ids, ex = _day_plan(profile_id, [("A", str(today.weekday()))])
    for days_ago in (10, 0):
        at = datetime.combine(today - timedelta(days=days_ago), dtime(10), tzinfo=timezone.utc).isoformat()
        db.save_workout({"profile_id": profile_id, "split_day_id": ids[0], "started_at": at,
                         "sets": [{"exercise_id": ex, "reps": 10, "weight": 20}]})
    ctx = coach.context(profile_id)
    assert ctx["diese_woche"] == {"geplant": 1, "erledigt": 1, "offen": []}
    assert ctx["pause_vor_letzter_einheit_tage"] == 10
    assert ctx["heute_geplant"]["heute_schon_trainiert"] is True


def test_weggeklickt_und_motivation_im_kontext(profile_id):
    _plan(profile_id)
    with db.connect() as c:
        c.execute("INSERT INTO coach_notes (profile_id, date, category, title, text, status)"
                  " VALUES (?,?,?,?,?,?)", (profile_id, today_iso(), "progress", "Wadenheben RPE", "t", "dismissed"))
    db.update_row("profiles", profile_id, {"motivation": "Ziel-Outfit"})
    ctx = coach.context(profile_id)
    assert ctx["weggeklickt"] == ["Wadenheben RPE"] and ctx["profil"]["motivation"] == "Ziel-Outfit"


def today_iso():
    from app import stats
    return stats.today().isoformat()


def test_datum_in_ortszeit(monkeypatch):
    from zoneinfo import ZoneInfo
    from app import stats
    monkeypatch.setattr(stats, "tz", lambda: ZoneInfo("Europe/Vienna"))
    assert coach._date("2026-10-14T23:30:00+00:00") == "2026-10-15"
