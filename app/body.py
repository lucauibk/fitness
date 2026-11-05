"""Koerpermessungen: manuell oder aus einer Studio-Koerperanalyse (ViCoach)."""
import math
from datetime import date, timedelta

from . import db, stats

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
    day = data.get("date") or stats.today().isoformat()
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


# ---------------------------------------------------------------- Trend & Einordnung
# Gemessen wird hoechstens einmal pro Woche, oft seltener. Darum beruht jede Aussage auf einer
# Ausgleichsgeraden durch den Verlauf (bei wenigen, weit auseinanderliegenden Punkten unverzerrt,
# anders als ein gleitender Mittelwert) und braucht mehrere Messungen ueber mehrere Wochen.
TREND_KEYS = ("weight_kg", "body_fat_pct", "muscle_mass_kg", "waist_cm", "hip_cm",
              "chest_cm", "arm_cm", "thigh_cm")
NOISY_KEYS = ("body_fat_pct", "muscle_mass_kg")     # BIA: nur als Tendenz lesen
WINDOW_DAYS = 84            # Verlauf, aus dem Veraenderung und Urteil entstehen (12 Wochen)
MIN_POINTS = 3              # Messtage im Fenster
MIN_SPAN_DAYS = 21          # so weit muessen erste und letzte Messung auseinanderliegen
STALE_DAYS = 56             # aelter: keine aktuelle Einordnung
MUSCLE_STABLE_KG = 1.0      # BIA-Genauigkeit: kleinere Aenderungen sind Rauschen
# "passt zum Ziel": Gewichtsaenderung in % Koerpergewicht pro Woche (von, bis)
GOAL_RATE = {"abnehmen": (-1.0, -0.25), "aufbau": (0.1, 0.5), "kraft": (0.0, 0.5),
             "fitness": (-0.25, 0.25)}
GOAL_LABELS = {"abnehmen": "Abnehmen", "aufbau": "Aufbau", "kraft": "Kraft", "fitness": "Halten"}


def _daily(points):
    """[(Datum, Wert)] chronologisch, mehrere Messungen eines Tages gemittelt."""
    by_day = {}
    for p in points:
        by_day.setdefault(date.fromisoformat(p["date"]), []).append(p["value"])
    return [(d, sum(v) / len(v)) for d, v in sorted(by_day.items())]


def _window(daily):
    """Messtage der letzten WINDOW_DAYS vor der juengsten Messung."""
    if not daily:
        return []
    start = daily[-1][0] - timedelta(days=WINDOW_DAYS)
    return [(d, v) for d, v in daily if d >= start]


def _enough(window):
    return len(window) >= MIN_POINTS and (window[-1][0] - window[0][0]).days >= MIN_SPAN_DAYS


def _fit(window):
    """Ausgleichsgerade (kleinste Quadrate): (Steigung pro Tag, Wert der Geraden am letzten Tag)."""
    n = len(window)
    xs = [(d - window[0][0]).days for d, _ in window]
    mx, my = sum(xs) / n, sum(v for _, v in window) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (v - my) for x, (_, v) in zip(xs, window)) / var if var else 0.0
    return slope, my + slope * (xs[-1] - mx)


def _classify(goal, pct):
    """(Status, Satzende) fuer eine Gewichtsaenderung von `pct` % pro Woche."""
    label = GOAL_LABELS[goal]
    lo, hi = GOAL_RATE[goal]
    if lo <= pct <= hi:
        return "passt", f"passt zum Ziel {label}"
    if goal == "abnehmen":
        if pct < lo:
            return "neutral", "schneller als empfohlen, das kann Muskelmasse kosten"
        if pct > 0.1:
            return "gegenlaeufig", "das Gewicht steigt – gegenläufig zum Ziel Abnehmen"
        return "neutral", "langsamer als für das Ziel Abnehmen üblich"
    if goal in ("aufbau", "kraft"):
        if pct > hi:
            return "neutral", f"schneller als für das Ziel {label} nötig, der Fettanteil kann mitwachsen"
        if pct < -0.1:
            return "gegenlaeufig", f"das Gewicht sinkt – gegenläufig zum Ziel {label}"
        return "neutral", f"stagniert – für das Ziel {label} darf es etwas mehr werden"
    return "neutral", "das Gewicht steigt etwas" if pct > hi else "das Gewicht sinkt etwas"


def _verdict(goal, weight_window, muscle_window):
    """Urteil zum Gewichtstrend aus den Messtagen im Fenster."""
    label = GOAL_LABELS[goal]
    if not _enough(weight_window):
        return {"status": "zu_wenig", "goal": label, "rate_kg_week": None,
                "text": f"Für eine Einordnung braucht es mindestens {MIN_POINTS} Messungen "
                        f"über {MIN_SPAN_DAYS // 7} Wochen."}
    slope, end = _fit(weight_window)
    rate = slope * 7
    sign = "+" if rate > 0 else "−"
    head = f"{sign}{abs(rate):.1f} kg/Woche".replace(".", ",")
    muscle = ""
    if len(muscle_window) >= 2:
        dm = _fit(muscle_window)[0] * (muscle_window[-1][0] - muscle_window[0][0]).days
        muscle = (" bei etwa gleicher Muskelmasse" if abs(dm) < MUSCLE_STABLE_KG else
                  " – die Muskelmasse sinkt dabei tendenziell (BIA-Werte sind ungenau)" if dm < 0
                  else " – die Muskelmasse steigt dabei tendenziell")
    status, tail = _classify(goal, rate / end * 100)
    return {"status": status, "goal": label, "rate_kg_week": round(rate, 2),
            "text": f"{head}{muscle}: {tail}."}


def trend(series, goal, today=None):
    """Einordnung der Verlaeufe (`history()["series"]`) gegenueber dem Profilziel.
    Rueckgabe: {"metrics": {key: {...}}, "verdict": {...} | None}
    metrics: value (Gerade am letzten Tag), since_last {delta, days}, change {delta, weeks} ueber das Fenster, stale, noisy."""
    today = today or stats.today()
    goal = goal if goal in GOAL_RATE else "fitness"
    metrics, windows = {}, {}
    for key in TREND_KEYS:
        if not series.get(key):
            continue
        daily = _daily(series[key])
        win = windows[key] = _window(daily)
        last = daily[-1]
        since_last = None
        if len(daily) > 1:
            since_last = {"delta": round(last[1] - daily[-2][1], 1), "days": (last[0] - daily[-2][0]).days}
        change, value = None, last[1]
        if _enough(win):
            slope, value = _fit(win)
            span = (win[-1][0] - win[0][0]).days
            change = {"delta": round(slope * span, 1), "weeks": round(span / 7)}
        metrics[key] = {"value": round(value, 1), "since_last": since_last, "change": change,
                        "stale": (today - last[0]).days > STALE_DAYS, "noisy": key in NOISY_KEYS}
    verdict = None
    if "weight_kg" in metrics:
        if metrics["weight_kg"]["stale"]:
            verdict = {"status": "veraltet", "goal": GOAL_LABELS[goal], "rate_kg_week": None,
                       "text": "Die letzte Gewichtsmessung ist über acht Wochen her – "
                               "für eine Einordnung bitte wieder messen."}
        else:
            verdict = _verdict(goal, windows["weight_kg"], windows.get("muscle_mass_kg", []))
    return {"metrics": metrics, "verdict": verdict}
