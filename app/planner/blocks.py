"""Trainingsbloecke: Woche X von Y, Anstrengung (RIR) je Woche, Deload.

Die Satzzahl ist in jeder Trainingswoche genau der Plan (split_exercises.target_sets), nur die
Entlastungswoche halbiert sie. Automatisch steigen im Block Gewicht und Wiederholungen
(planner/progression.py), nie die Saetze – ausdruecklich so gewuenscht (2026-10-14): von allen
Uebungen fuenf Saetze ist im Alltag nicht machbar. Mehr Saetze gibt es nur, wenn man den Plan
selbst aendert (Vorschlaege dazu kommen aus analysis.py, angewendet wird nichts automatisch).

Aeltere Bloecke tragen noch params["ratios"]/["adjust"] aus der Zeit mit Satz-Steigerung;
beides wird nicht mehr gelesen.
"""
import json
import math
import threading
from datetime import date, timedelta

from .. import db, stats
from . import volume_plan as vp
from .muscles import PATTERN_GROUP

MAX_EXERCISE_SETS = 6
LOCK = threading.RLock()      # Erstellen, Aktivieren und Wochen-Check nie gleichzeitig


def monday(d):
    return d - timedelta(days=d.weekday())


def _params(block):
    return json.loads(block["params"]) if block and block.get("params") else {}


def _save_params(block_id, params, **fields):
    db.update_row("blocks", block_id, {"params": json.dumps(params), **fields})


DELOAD_RATIO = 0.5


def ratios_for(goal, experience, focus=(), age=None, weeks=None):
    """Saetze je Blockwoche relativ zum Plan, je Muskelgruppe: in allen Trainingswochen 1, in der
    Entlastung die Haelfte. Dieselbe Form wie frueher, damit Plan-Check und -Vorschlaege
    (analysis.py, validate.py) unveraendert rechnen koennen."""
    n = weeks or vp.BLOCK_WEEKS.get(experience, 5)
    return {g: [1.0] * (n - 1) + [DELOAD_RATIO] for g in PATTERN_GROUP.values()}


def create(profile_id, split_id, goal=None, experience=None, focus=(), age=None, variation=0, start=None, cycle=1.0):
    """Neuen Block fuer einen Split anlegen; die aktuellen Plan-Saetze werden Woche 1."""
    with LOCK:
        return _create(profile_id, split_id, goal, experience, focus, age, variation, start, cycle)


def _create(profile_id, split_id, goal, experience, focus, age, variation, start, cycle=1.0):
    goal = goal if goal in vp.GOAL_FACTOR else "aufbau"
    experience = experience if experience in vp.BLOCK_WEEKS else "einsteiger"
    weeks = vp.BLOCK_WEEKS[experience]
    with db.connect() as c:
        c.execute("UPDATE blocks SET active=0 WHERE profile_id=?", (profile_id,))
        c.execute("UPDATE split_exercises SET base_sets = target_sets WHERE split_day_id IN"
                  " (SELECT id FROM split_days WHERE split_id=?)", (split_id,))
    params = {"rir": vp.rir_by_week(weeks), "reviewed": 0, "variation": variation, "cycle": cycle}
    return db.insert_row("blocks", {
        "profile_id": profile_id, "split_id": split_id, "start_date": (start or monday(stats.today())).isoformat(),
        "weeks": weeks, "goal": goal, "experience": experience, "params": json.dumps(params), "active": 1})


def active(profile_id):
    return db.one("SELECT * FROM blocks WHERE profile_id=? AND active=1 ORDER BY id DESC LIMIT 1", (profile_id,))


def for_split(split_id):
    return db.one("SELECT * FROM blocks WHERE split_id=? ORDER BY id DESC LIMIT 1", (split_id,))


def ensure(profile_id, split=None):
    """Aktiver Block fuer den aktiven Split. Wechselt man zu einem Split mit frueherem Block, laeuft dieser
    weiter (Woche, Anpassungen, Basis-Saetze bleiben); nur ein Split ohne Block bekommt einen neuen."""
    with LOCK:
        split = split or db.active_split(profile_id)
        if not split:
            return None
        block = active(profile_id)
        if block and block["split_id"] == split["id"]:
            return block
        earlier = for_split(split["id"])
        if earlier:
            with db.connect() as c:
                c.execute("UPDATE blocks SET active=0 WHERE profile_id=?", (profile_id,))
                c.execute("UPDATE blocks SET active=1 WHERE id=?", (earlier["id"],))
            return db.get_row("blocks", earlier["id"])
        profile = db.get_row("profiles", profile_id) or {}
        age = (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None
        return create(profile_id, split["id"], profile.get("goal"), profile.get("experience"),
                      db.parse_focus(profile.get("focus")), age)


def week_no(block, on_date=None):
    on_date = on_date or stats.today()
    start = date.fromisoformat(block["start_date"])
    return max(1, (monday(on_date) - monday(start)).days // 7 + 1)


def state(block, on_date=None):
    """{week, weeks, deload, finished, rir}"""
    if not block:
        return None
    n = week_no(block, on_date)
    p = _params(block)
    weeks = block["weeks"]
    rir = p.get("rir") or vp.rir_by_week(weeks)
    rir = (rir + [rir[-1]] * weeks)[:weeks]
    return {"id": block["id"], "week": min(n, weeks), "weeks": weeks, "finished": n > weeks,
            "deload": min(n, weeks) == weeks, "rir": rir[min(n, weeks) - 1], "goal": block["goal"],
            "experience": block["experience"], "start_date": block["start_date"]}


def sets_for(block, exercise_row, on_date=None):
    """Satzzahl dieser Uebung in der Blockwoche: der Plan, in der Entlastung die Haelfte."""
    base = exercise_row.get("base_sets") or exercise_row["target_sets"]
    st = state(block, on_date)
    if st and st["deload"]:
        return max(1, math.ceil(base / 2))
    return base
