"""Taeglicher Check-in: aus Schlaf, Energie, Stress und Muskelkater wird ein
Readiness-Score (0–100), und das heutige Training wird daran angepasst."""
import json

from . import db

LEVELS = (  # (ab Score, Schluessel, Titel, Empfehlung)
    (60, "voll", "Bereit für volles Training",
     # Ab 60 zaehlt schon ein ganz normaler Tag (alles 3) – "gut erholt" waere zu viel versprochen.
     "Nichts spricht dagegen – zieh den Plan wie vorgesehen durch."),
    (40, "leichter", "Heute etwas leichter",
     "Ein Satz weniger je Übung und etwa 5 % weniger Gewicht. Technik vor Last."),
    (0, "erholung", "Erholung empfohlen",
     "Heute lieber Mobility oder lockeres Cardio. Trainieren geht, aber hör auf deinen Körper."),
)
GROUPS = ("brust", "ruecken", "beine", "schultern", "arme", "rumpf")


def score(sleep, energy, stress, soreness):
    """Schlaf und Energie (1–5) zaehlen positiv, Stress (1–5) negativ, der staerkste
    Muskelkater (0–3) zieht zusaetzlich ab."""
    def norm(v):
        return (min(max(int(v or 3), 1), 5) - 1) / 4
    base = 0.35 * norm(sleep) + 0.35 * norm(energy) + 0.30 * (1 - norm(stress))
    worst = max((int(v) for v in (soreness or {}).values()), default=0)
    penalty = {0: 0, 1: 0.05, 2: 0.15, 3: 0.25}[min(max(worst, 0), 3)]
    # Ein durchschnittlicher Tag (alles 3) ergibt 60 – also volles Training.
    return max(0, min(100, round((0.2 + 0.8 * base - penalty) * 100)))


def level(value):
    for threshold, key, title, text in LEVELS:
        if value >= threshold:
            return {"key": key, "title": title, "text": text}
    return {"key": "erholung", "title": LEVELS[-1][2], "text": LEVELS[-1][3]}


def clean_soreness(raw):
    out = {}
    for g, v in (raw or {}).items():
        if g in GROUPS:
            v = int(v)
            if 0 <= v <= 3 and v:
                out[g] = v
    return out


def get(profile_id, day):
    row = db.one("SELECT * FROM checkins WHERE profile_id=? AND date=?", (profile_id, day))
    if not row:
        return None
    row["soreness"] = json.loads(row["soreness"] or "{}")
    row["level"] = level(row["readiness"])
    return row


def save(profile_id, day, sleep, energy, stress, soreness, client_id=None):
    """Ein Check-in pro Tag – ein zweiter ueberschreibt den ersten."""
    soreness = clean_soreness(soreness)
    value = score(sleep, energy, stress, soreness)
    with db.connect() as c:
        c.execute(
            "INSERT INTO checkins (profile_id, date, sleep, stress, energy, soreness, readiness, client_id)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(profile_id, date) DO UPDATE SET sleep=excluded.sleep, stress=excluded.stress,"
            " energy=excluded.energy, soreness=excluded.soreness, readiness=excluded.readiness",
            (profile_id, day, sleep, stress, energy, json.dumps(soreness), value, client_id))
    return get(profile_id, day)


def _round_half(x):
    return round(x * 2) / 2


def adjust(exercises, checkin):
    """Uebungen eines Trainingstags an den Check-in anpassen. Aendert Saetze und
    Gewichtsvorschlag und haengt einen Grund an (adjusted)."""
    if not checkin:
        return exercises
    lvl = checkin["level"]["key"]
    sore = {g for g, v in (checkin.get("soreness") or {}).items() if v >= 2}
    out = []
    for ex in exercises:
        ex = dict(ex)
        reasons = []
        sets = ex.get("target_sets") or 3
        weight = (ex.get("suggest") or {}).get("weight")
        if lvl in ("leichter", "erholung"):
            sets -= 1
            if weight:
                weight = _round_half(weight * 0.95)
            reasons.append("heute leichter")
        if ex.get("muscle_group") in sore:
            sets -= 1
            reasons.append("Muskelkater")
        if reasons:
            ex["target_sets"] = max(1, sets)
            if ex.get("suggest"):
                ex["suggest"] = {**ex["suggest"], "weight": weight}
            ex["adjusted"] = " · ".join(reasons)
        out.append(ex)
    return out


def sore_groups_in(day, checkin):
    if not checkin or not day:
        return []
    sore = {g for g, v in (checkin.get("soreness") or {}).items() if v >= 2}
    return sorted({e.get("muscle_group") for e in day["exercises"]} & sore)
