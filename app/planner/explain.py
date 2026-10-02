"""Jede Anpassung des Plans bekommt einen Satz – gespeichert und in der App sichtbar."""
from .. import db, stats


def log(profile_id, kind, text, exercise_id=None, split_id=None, day=None):
    day = (day or stats.today()).isoformat()
    dup = db.one("SELECT id FROM plan_events WHERE profile_id=? AND date=? AND kind=? AND text=?",
                 (profile_id, day, kind, text))
    if dup:
        return dup
    return db.insert_row("plan_events", {"profile_id": profile_id, "split_id": split_id,
                                         "exercise_id": exercise_id, "date": day, "kind": kind, "text": text})


def recent(profile_id, limit=30):
    return db.rows("SELECT id, date, kind, text FROM plan_events WHERE profile_id=?"
                   " ORDER BY date DESC, id DESC LIMIT ?", (profile_id, limit))


def kg(value):
    return f"{value:g}".replace(".", ",") + " kg"


def after_workout(profile_id, workout):
    """Nach dem Speichern einer Einheit: was bedeutet das fuers naechste Mal?"""
    from . import progression
    split = db.active_split(profile_id)
    if not split:
        return []
    items = {i["exercise_id"]: i for d in split["days"] for i in d["exercises"]}
    out = []
    for ex_id in {s["exercise_id"] for s in workout.get("sets", []) if not s.get("warmup")}:
        item = items.get(ex_id)
        if not item:
            continue
        rec = progression.for_exercise(profile_id, ex_id, item["target_reps_min"], item["target_reps_max"],
                                       item["target_sets"])
        name = item["name"]
        if rec["raise_weight"] and rec["weight"]:
            text = f"{name}: {item['target_reps_max']} Wiederholungen geschafft → nächstes Mal {kg(rec['weight'])}"
            kind = "weight_up"
        elif rec["status"] in ("plateau", "drop"):
            text, kind = f"{name}: {rec['reason']}", rec["status"]
        else:
            continue
        log(profile_id, kind, text, ex_id, split["id"])
        out.append(text)
    return out
