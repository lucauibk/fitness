"""Koerpermessungen: manuell oder aus einer Studio-Koerperanalyse (ViCoach)."""
import math
from datetime import date

from . import db

# Plausible Bereiche – alles ausserhalb wird verworfen (vor allem beim Einlesen per KI)
RANGES = {
    "weight_kg": (25, 350), "body_fat_pct": (2, 70), "muscle_mass_kg": (10, 120),
    "water_pct": (20, 80), "visceral_fat": (1, 60), "bmr_kcal": (700, 5000),
    "bio_age": (10, 110), "waist_cm": (40, 200), "hip_cm": (50, 200),
    "chest_cm": (50, 200), "arm_cm": (15, 70), "thigh_cm": (25, 110), "neck_cm": (20, 70),
}
LABELS = {
    "weight_kg": "Gewicht", "body_fat_pct": "Körperfett", "muscle_mass_kg": "Muskelmasse",
    "water_pct": "Körperwasser", "visceral_fat": "Viszerales Fett", "bmr_kcal": "Grundumsatz",
    "bio_age": "Biologisches Alter", "waist_cm": "Taille", "hip_cm": "Hüfte",
    "chest_cm": "Brust", "arm_cm": "Oberarm", "thigh_cm": "Oberschenkel", "neck_cm": "Hals",
}


def clean(values):
    """Nur bekannte, plausible Zahlen behalten. Rueckgabe: (werte, verworfen)."""
    out, dropped = {}, []
    for key, (lo, hi) in RANGES.items():
        v = values.get(key)
        if v in (None, ""):
            continue
        try:
            v = round(float(v), 1)
        except (TypeError, ValueError):
            dropped.append(LABELS[key])
            continue
        if lo <= v <= hi:
            out[key] = v
        else:
            dropped.append(LABELS[key])
    return out, dropped


def navy_body_fat(sex, height_cm, waist_cm, neck_cm, hip_cm=None):
    """US-Navy-Formel (metrisch). Nur eine grobe Schaetzung."""
    try:
        if sex == "männlich":
            if waist_cm <= neck_cm:
                return None
            v = 495 / (1.0324 - 0.19077 * math.log10(waist_cm - neck_cm)
                       + 0.15456 * math.log10(height_cm)) - 450
        elif sex == "weiblich":
            if not hip_cm or waist_cm + hip_cm <= neck_cm:
                return None
            v = 495 / (1.29579 - 0.35004 * math.log10(waist_cm + hip_cm - neck_cm)
                       + 0.22100 * math.log10(height_cm)) - 450
        else:
            return None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(v, 1) if 2 <= v <= 70 else None


def save(profile_id, data):
    """Messung speichern. Fehlt Koerperfett, wird es – wenn moeglich – per Navy-Formel
    geschaetzt. Ein Gewicht landet zusaetzlich im Koerpergewicht-Verlauf."""
    values, _ = clean(data)
    profile = db.get_row("profiles", profile_id) or {}
    estimated = 0
    if "body_fat_pct" not in values and values.get("waist_cm") and values.get("neck_cm") \
            and profile.get("height_cm"):
        bf = navy_body_fat(profile.get("sex"), profile["height_cm"], values["waist_cm"],
                           values["neck_cm"], values.get("hip_cm"))
        if bf is not None:
            values["body_fat_pct"] = bf
            estimated = 1
    day = data.get("date") or date.today().isoformat()
    row = {"profile_id": profile_id, "date": day,
           "source": data.get("source") if data.get("source") in ("manuell", "vicoach") else "manuell",
           "notes": (data.get("notes") or "").strip()[:300] or None,
           "client_id": data.get("client_id"), "body_fat_estimated": estimated, **values}
    if data.get("client_id"):
        have = db.one("SELECT id FROM body_measurements WHERE client_id=?", (data["client_id"],))
        if have:
            return db.get_row("body_measurements", have["id"]), False
    saved = db.insert_row("body_measurements", row)
    if values.get("weight_kg"):
        db.set_bodyweight(profile_id, day, values["weight_kg"])
    return saved, True


def delete(profile_id, mid):
    """Messung loeschen. Hat sie das Koerpergewicht ihres Tages gesetzt, wird das
    zurueckgenommen: auf das Gewicht einer anderen Messung desselben Tages, sonst ganz
    entfernt. Ein Gewicht, das inzwischen anders gesetzt wurde, bleibt unangetastet."""
    row = db.get_row("body_measurements", mid)
    if not row:
        return
    with db.connect() as c:
        c.execute("DELETE FROM body_measurements WHERE id=?", (mid,))
        if row.get("weight_kg") is None:
            return
        bw = c.execute("SELECT kg FROM bodyweight WHERE profile_id=? AND date=?",
                       (profile_id, row["date"])).fetchone()
        if not bw or bw[0] != row["weight_kg"]:
            return
        other = c.execute("SELECT weight_kg FROM body_measurements WHERE profile_id=? AND date=?"
                          " AND weight_kg IS NOT NULL ORDER BY id DESC LIMIT 1",
                          (profile_id, row["date"])).fetchone()
        if other:
            c.execute("UPDATE bodyweight SET kg=? WHERE profile_id=? AND date=?",
                      (other[0], profile_id, row["date"]))
        else:
            c.execute("DELETE FROM bodyweight WHERE profile_id=? AND date=?",
                      (profile_id, row["date"]))


def history(profile_id):
    rows = db.rows("SELECT * FROM body_measurements WHERE profile_id=? ORDER BY date, id",
                   (profile_id,))
    latest = {}
    for r in rows:
        for key in RANGES:
            if r.get(key) is not None:
                latest[key] = {"value": r[key], "date": r["date"], "source": r["source"],
                               "estimated": bool(r["body_fat_estimated"]) if key == "body_fat_pct" else False}
    series = {key: [{"date": r["date"], "value": r[key]} for r in rows if r.get(key) is not None]
              for key in RANGES}
    return {"entries": list(reversed(rows)), "latest": latest,
            "series": {k: v for k, v in series.items() if v}, "labels": LABELS}
