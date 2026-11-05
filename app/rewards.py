"""Shopping-Topf: Jede Einheit legt einen festen Betrag in einen Topf, davon wird die
Wunschliste "gekauft". Belohnt wird das Training, nicht die Waage – die schwankt und
nimmt genau dann die Motivation, wenn das Training eigentlich stimmt.

Der Topfstand wird nicht mitgeschrieben, sondern aus den Einheiten gerechnet: Geloeschte
oder spaeter aus dem Ausgang nachgereichte Einheiten stimmen so automatisch. Damit eine
Betragsaenderung nicht rueckwirkend gilt, wird beim Aendern der bisherige Stand in
reward_carry eingefroren und ab der letzten Einheit (reward_after_id) neu gezaehlt."""
import math

from . import db
from .stats import _local_date, today

# Zaehlt nur, wenn mindestens ein echter Arbeitssatz drin ist – ein leer gespeicherter
# Entwurf soll kein Geld bringen.
_COUNTED = """FROM workouts w WHERE w.profile_id=? AND w.id>?
    AND EXISTS (SELECT 1 FROM sets s WHERE s.workout_id=w.id AND COALESCE(s.warmup,0)=0
                AND COALESCE(s.reps,0)>0)"""


def _counted(profile_id, after_id):
    return db.one(f"SELECT COUNT(*) AS n {_COUNTED}", (profile_id, after_id or 0))["n"]


def earned(profile):
    rate = profile.get("reward_per_workout") or 0
    carry = profile.get("reward_carry") or 0
    return round(carry + rate * _counted(profile["id"], profile.get("reward_after_id")), 2)


def _spent(profile_id):
    row = db.one("SELECT COALESCE(SUM(price),0) AS s FROM wishlist WHERE profile_id=?"
                 " AND bought_at IS NOT NULL", (profile_id,))
    return round(row["s"], 2)


def balance(profile):
    return round(earned(profile) - _spent(profile["id"]), 2)


def set_rate(profile_id, rate):
    """Neuer Betrag je Einheit (None = Topf aus). Bisher Verdientes bleibt im Topf."""
    profile = db.get_row("profiles", profile_id)
    carry = earned(profile)
    last = db.one("SELECT COALESCE(MAX(id),0) AS m FROM workouts WHERE profile_id=?",
                  (profile_id,))["m"]
    with db.connect() as c:
        c.execute("UPDATE profiles SET reward_per_workout=?, reward_carry=?, reward_after_id=?"
                  " WHERE id=?", (rate, carry, last, profile_id))


def _item_out(item, bal, rate):
    missing = max(0.0, round(item["price"] - bal, 2))
    return {**item, "missing": missing, "affordable": missing == 0,
            "workouts_left": math.ceil(missing / rate) if rate and missing else 0}


def items(profile_id):
    return db.rows("SELECT * FROM wishlist WHERE profile_id=? ORDER BY bought_at IS NOT NULL,"
                   " position, id", (profile_id,))


def status(profile_id):
    """Topfstand, Wunschliste und das naechste Ziel (erstes offenes Teil der Liste)."""
    profile = db.get_row("profiles", profile_id)
    rate = profile.get("reward_per_workout")
    bal = balance(profile)
    rows = items(profile_id)
    open_items = [_item_out(r, bal, rate) for r in rows if not r["bought_at"]]
    # Ortszeit wie ueberall in der App: eine Einheit um 00:30 gehoert zum neuen Tag
    today_n = sum(1 for r in db.rows(f"SELECT COALESCE(w.started_at, w.finished_at) AS at {_COUNTED}",
                                     (profile_id, profile.get("reward_after_id") or 0))
                  if _local_date(r["at"]) == today())
    return {
        "enabled": bool(rate),
        "rate": rate,
        "balance": bal,
        "earned_today": round((rate or 0) * today_n, 2),
        "goal": open_items[0] if open_items else None,
        "open": open_items,
        "bought": [r for r in rows if r["bought_at"]],
    }


def card(profile_id):
    """Kurzfassung fuer "Heute": nur wenn der Topf an ist."""
    profile = db.get_row("profiles", profile_id)
    if not (profile or {}).get("reward_per_workout"):
        return None
    full = status(profile_id)
    return {k: full[k] for k in ("rate", "balance", "earned_today", "goal")} | {"count": len(full["open"])}
