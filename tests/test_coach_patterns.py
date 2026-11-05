"""Gewohnheiten im Training: wiederholt ersetzte Planuebungen erkennt die App selbst und gibt
sie dem Coach – er entscheidet, ob er einen dauerhaften Tausch anbietet."""
import os
from datetime import timedelta

from app import coach, coach_chat, coach_patterns, db
from app.stats import today


def _ex(name, pattern, group="ruecken"):
    with db.connect() as c:
        # equipment_type/sfr wie echte Katalog-Uebungen (test_planner_catalog prueft das fuer alle)
        return c.execute("INSERT INTO exercises (name, muscle_group, unit, pattern, equipment_type, sfr)"
                         " VALUES (?,?,?,?,?,?)",
                         (f"{name}-{os.urandom(3).hex()}", group, "kg", pattern, "kabel", 3)).lastrowid


def _setup(profile_id):
    row, lat, alt, curl = _ex("Rudern", "row"), _ex("Latzug", "vpull"), _ex("KH-Rudern", "row"), _ex("Curl", "curl", "arme")
    with db.connect() as c:
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "T", db.now())).lastrowid
        day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,'OK')", (split,)).lastrowid
        item = c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position) VALUES (?,?,0)", (day, row)).lastrowid
        c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position) VALUES (?,?,1)", (day, lat))
    return day, item, {"row": row, "lat": lat, "alt": alt, "curl": curl}


def _session(profile_id, day, exercises, days_ago):
    at = (today() - timedelta(days=days_ago)).isoformat() + "T10:00:00+00:00"
    db.save_workout({"profile_id": profile_id, "split_day_id": day, "started_at": at,
                     "sets": [{"exercise_id": e, "reps": 10, "weight": 20} for e in exercises]})


def test_wiederholter_ersatz_wird_erkannt(profile_id):
    day, item, ex = _setup(profile_id)
    _session(profile_id, day, [ex["row"], ex["lat"]], 20)
    _session(profile_id, day, [ex["alt"], ex["lat"]], 13)
    _session(profile_id, day, [ex["alt"], ex["lat"], ex["curl"]], 6)
    found = coach_patterns.substitutions(profile_id)
    assert len(found) == 1
    f = found[0]
    assert f["plan_id"] == item and f["ersatz"].startswith("KH-Rudern") and f["mal"] == 2 and f["einheiten"] == 3
    assert coach.context(profile_id)["muster"]["ersetzt_im_training"] == found


def test_einmal_oder_nicht_mehr_aktuell_zaehlt_nicht(profile_id):
    day, _, ex = _setup(profile_id)
    _session(profile_id, day, [ex["alt"], ex["lat"]], 20)
    _session(profile_id, day, [ex["row"], ex["lat"]], 6)        # zuletzt wieder das Original
    assert coach_patterns.substitutions(profile_id) == []
    _session(profile_id, day, [ex["alt"], ex["lat"]], 3)          # 2x, auch zuletzt
    assert len(coach_patterns.substitutions(profile_id)) == 1


def test_andere_muskelgruppe_ist_kein_ersatz(profile_id):
    day, _, ex = _setup(profile_id)
    _session(profile_id, day, [ex["curl"], ex["lat"]], 13)
    _session(profile_id, day, [ex["curl"], ex["lat"]], 6)
    assert coach_patterns.substitutions(profile_id) == []


def test_schon_vorgeschlagen_bleibt_ruhig(profile_id):
    day, item, ex = _setup(profile_id)
    _session(profile_id, day, [ex["alt"], ex["lat"]], 13)
    _session(profile_id, day, [ex["alt"], ex["lat"]], 6)
    name = db.get_row("exercises", ex["alt"])["name"]
    actions = coach_chat.validate_actions([{"typ": "ersetzen", "plan_id": item, "neu": name}], profile_id)
    coach_chat.add_message(profile_id, "coach", "Dauerhaft tauschen?", source="daily", actions=actions)
    assert coach_patterns.substitutions(profile_id) == []
