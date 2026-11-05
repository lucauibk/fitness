"""Muster im Training, die der Coach ansprechen soll – deterministisch erkannt, damit er sie
nicht aus Rohdaten erraten muss.

Ersetzt im Training: Fehlt in einer Einheit eine Planuebung und steht stattdessen eine Uebung
mit gleichem Bewegungsmuster (sonst gleicher Muskelgruppe) drin, gilt sie als ersetzt. Passiert
das mehrfach – und auch beim letzten Mal an diesem Tag –, ist es eine Gewohnheit, und ein
dauerhafter Tausch im Plan liegt nahe. Ob er sinnvoll ist, entscheidet der Coach."""
import json
from datetime import timedelta

from . import db, stats

WEEKS = 8
MIN_TIMES = 2
# Schon vorgeschlagen (egal ob uebernommen, abgelehnt oder offen): so lange nicht erneut melden
QUIET_DAYS = 21


def _proposed(profile_id):
    """(plan_id, neue exercise_id) aller Tausch-Vorschlaege der letzten QUIET_DAYS Tage."""
    since = (stats.today() - timedelta(days=QUIET_DAYS)).isoformat()
    out = set()
    for r in db.rows("SELECT actions FROM coach_messages WHERE profile_id=? AND actions IS NOT NULL"
                     " AND created_at >= ?", (profile_id, since)):
        for a in json.loads(r["actions"]):
            if a.get("type") == "swap":     # nur Plan-Tausch; "nur heute" (Training) zaehlt nicht
                out.add((a["item_id"], a["to_id"]))
    return out


def _pair(missing, extra):
    """Fehlende Planuebungen mit Ersatz-Uebungen paaren: erst gleiches Muster, dann gleiche Gruppe."""
    pairs, free = [], list(extra)
    for key in ("pattern", "muscle_group"):
        for m in [m for m in missing if m["id"] not in {p[0]["id"] for p in pairs}]:
            hit = next((e for e in free if m.get(key) and e.get(key) == m[key]), None)
            if hit:
                pairs.append((m, hit))
                free.remove(hit)
    return pairs


def substitutions(profile_id, today=None):
    split = db.active_split(profile_id)
    if not split:
        return []
    today = today or stats.today()
    since = (today - timedelta(weeks=WEEKS)).isoformat()
    in_plan = {e["exercise_id"] for d in split["days"] for e in d["exercises"]}
    info = {r["id"]: r for r in db.rows("SELECT id, name, pattern, muscle_group FROM exercises")}
    proposed = _proposed(profile_id)
    out = []
    for day in split["days"]:
        sessions = [w for w in db.rows(
            "SELECT id, COALESCE(started_at, finished_at) AS at FROM workouts WHERE profile_id=?"
            " AND split_day_id=? AND COALESCE(started_at, finished_at) >= ? ORDER BY at",
            (profile_id, day["id"], since))]
        if len(sessions) < MIN_TIMES:
            continue
        counts, last_pairs = {}, set()
        for i, w in enumerate(sessions):
            done = {r["exercise_id"] for r in db.rows(
                "SELECT DISTINCT exercise_id FROM sets WHERE workout_id=? AND COALESCE(warmup,0)=0", (w["id"],))}
            missing = [e for e in day["exercises"] if e["exercise_id"] not in done]
            # Ersatz nur, was nirgends im Plan steht – sonst ist es eher ein umgebauter Plan
            extra = [info[x] for x in done - in_plan if x in info]
            for m, e in _pair(missing, extra):
                key = (m["id"], e["id"])
                c = counts.setdefault(key, {"mal": 0, "zuletzt": None, "item": m, "neu": e})
                c["mal"] += 1
                c["zuletzt"] = w["at"][:10]
                if i == len(sessions) - 1:
                    last_pairs.add(key)
        for key, c in counts.items():
            if c["mal"] >= MIN_TIMES and key in last_pairs and key not in proposed:
                out.append({"tag": day["label"], "plan_id": c["item"]["id"], "plan_uebung": c["item"]["name"],
                            "ersatz": c["neu"]["name"], "mal": c["mal"], "einheiten": len(sessions),
                            "zuletzt": c["zuletzt"]})
    return out
