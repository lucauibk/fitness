"""Trainingsbloecke: Woche X von Y, Saetze je Woche, Deload.

Der Plan (split_exercises.target_sets) ist Woche 1. Alle weiteren Wochen werden aus dem
Wochenverlauf des Blocks berechnet (`ratios` je Muskelgruppe) – der Plan selbst bleibt unveraendert
und reproduzierbar. Anpassungen aus dem Wochen-Check stehen in params["adjust"].
"""
import json
import math
import threading
from datetime import date, timedelta

from .. import db
from . import volume_plan as vp
from .generate import FOCUS_GROUPS
from .muscles import PATTERN_GROUP

MAX_EXERCISE_SETS = 6
LOCK = threading.RLock()      # Erstellen, Aktivieren und Wochen-Check nie gleichzeitig


def monday(d):
    return d - timedelta(days=d.weekday())


def _params(block):
    return json.loads(block["params"]) if block and block.get("params") else {}


def _save_params(block_id, params, **fields):
    db.update_row("blocks", block_id, {"params": json.dumps(params), **fields})


def ratios_for(goal, experience, focus=(), age=None, weeks=None):
    groups = {g for f in focus for g in FOCUS_GROUPS.get(f, [])}
    t = vp.block_targets(experience, goal, groups, age, weeks)
    return {g: [(x / v[0]) if v[0] else 1.0 for x in v] for g, v in t.items()}


def create(profile_id, split_id, goal=None, experience=None, focus=(), age=None, variation=0, start=None):
    """Neuen Block fuer einen Split anlegen; die aktuellen Plan-Saetze werden Woche 1."""
    with LOCK:
        return _create(profile_id, split_id, goal, experience, focus, age, variation, start)


def _create(profile_id, split_id, goal, experience, focus, age, variation, start):
    goal = goal if goal in vp.GOAL_FACTOR else "aufbau"
    experience = experience if experience in vp.BLOCK_WEEKS else "einsteiger"
    weeks = vp.BLOCK_WEEKS[experience]
    with db.connect() as c:
        c.execute("UPDATE blocks SET active=0 WHERE profile_id=?", (profile_id,))
        c.execute("UPDATE split_exercises SET base_sets = target_sets WHERE split_day_id IN"
                  " (SELECT id FROM split_days WHERE split_id=?)", (split_id,))
    params = {"rir": vp.rir_by_week(weeks), "ratios": ratios_for(goal, experience, focus, age, weeks),
              "adjust": {}, "reviewed": 0, "variation": variation}
    return db.insert_row("blocks", {
        "profile_id": profile_id, "split_id": split_id, "start_date": (start or monday(date.today())).isoformat(),
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
    on_date = on_date or date.today()
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
    """Satzzahl dieser Uebung in der aktuellen Blockwoche (Plan = Woche 1)."""
    st = state(block, on_date)
    base = exercise_row.get("base_sets") or exercise_row["target_sets"]
    if not st:
        return base
    if st["deload"]:
        return max(1, math.ceil(base / 2))
    p = _params(block)
    group = PATTERN_GROUP.get(exercise_row.get("pattern"))
    ratio = (p.get("ratios", {}).get(group) or [1.0])
    ratio = ratio[min(st["week"], len(ratio)) - 1]
    cap = exercise_row.get("peak_sets") or MAX_EXERCISE_SETS      # Zeitbudget der Einheit
    sets = max(2, min(MAX_EXERCISE_SETS, round(base * ratio), base + 3, max(cap, base)))
    extra = p.get("adjust", {}).get(str(st["week"]), {}).get(str(exercise_row["id"]), 0)
    return min(MAX_EXERCISE_SETS, sets + extra) if base >= 2 else base
