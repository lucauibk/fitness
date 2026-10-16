"""Wochen-Check: schaut auf die letzte Woche und passt den Block an.

Passiert automatisch (einmal pro Woche, beim Oeffnen von „Heute“) und wird begruendet:
- steigt bei gleichem Gewicht die Anstrengung (RPE-Drift), wird die laufende Woche zum Deload,
- bei wiederholtem Stillstand wird die Uebung gegen eine Variante desselben Musters getauscht,
- am Blockende startet ein neuer Block mit frischen Isolationsuebungen.

Die Satzzahl fasst der Wochen-Check nie an – weder Nachholen verpasster Saetze noch Zuschlaege
bei leichten Wochen. Sie bleibt, wie sie im Plan steht (siehe planner/blocks.py).
"""
import json
from datetime import date, timedelta

from .. import db, stats
from . import blocks, cold_start, explain, generate, progression, volume_plan as vp
from .muscles import GROUPS, PATTERN_GROUP

def _split_rows(split):
    rows = []
    for d in split["days"]:
        for e in d["exercises"]:
            rows.append(e)
    return rows


def actual_sets(profile_id, start, end):
    """{Gruppe: Saetze} und {Gruppe: [RPE]} im Zeitraum [start, end]."""
    # Zeitstempel sind UTC – gezaehlt wird nach dem Wiener Kalendertag (Einheit kurz nach Mitternacht)
    rows = db.rows(
        "SELECT e.pattern, s.rpe, COALESCE(w.started_at, w.finished_at) AS at FROM sets s"
        " JOIN workouts w ON w.id = s.workout_id JOIN exercises e ON e.id = s.exercise_id"
        " WHERE w.profile_id=? AND s.warmup=0 AND substr(COALESCE(w.started_at, w.finished_at),1,10) BETWEEN ? AND ?",
        (profile_id, (start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat()))
    sets, rpes = {g: 0 for g in GROUPS}, {g: [] for g in GROUPS}
    for r in rows:
        day = stats._local_date(r["at"])
        if not day or not start <= day <= end:
            continue
        g = PATTERN_GROUP.get(r["pattern"])
        if g:
            sets[g] += 1
            if r["rpe"] is not None:
                rpes[g].append(r["rpe"])
    return sets, rpes


def planned_sets(block, split, week):
    """Geplante Saetze je Gruppe in einer Blockwoche."""
    day = date.fromisoformat(block["start_date"]) + timedelta(weeks=week - 1)
    out = {g: 0 for g in GROUPS}
    days = split["days"]
    cycle = blocks._params(block).get("cycle") or 1.0       # Wechselplan: ein Durchlauf dauert laenger als eine Woche
    for d in days:
        times = max(1, len(d.get("weekdays") or []))
        for e in d["exercises"]:
            g = PATTERN_GROUP.get(e.get("pattern"))
            if g:
                out[g] += blocks.sets_for(block, e, day) * times
    return {g: round(v / cycle) for g, v in out.items()}


MAX_BLOCK_WEEKS = 8
FATIGUE_TAU_DAYS = 5          # harte Saetze klingen mit etwa 5 Tagen Zeitkonstante ab
FATIGUE_HIGH = 1.3            # 30 % ueber dem eigenen Schnitt der Vorwochen
FATIGUE_BASELINE_DAYS = 28


def fatigue(profile_id, on_date=None):
    """Ermuedungswert: Summe der harten Saetze, exponentiell abklingend (Banister-artig, ohne Anpassung).
    -> (Schnitt der letzten 7 Tage, staerkste der vier Wochen davor) | None, wenn es dafuer noch zu wenig Verlauf gibt."""
    import math
    on_date = on_date or stats.today()
    per_day = {}
    for r in db.rows("SELECT COALESCE(w.started_at, w.finished_at) AS at, COUNT(*) AS n FROM sets s"
                     " JOIN workouts w ON w.id = s.workout_id WHERE w.profile_id=? AND s.warmup=0"
                     " GROUP BY w.id", (profile_id,)):
        d = stats._local_date(r["at"])
        if d and d <= on_date:
            per_day[d] = per_day.get(d, 0) + r["n"]
    if not per_day or (on_date - min(per_day)).days < FATIGUE_BASELINE_DAYS + 7:
        return None

    def value(day):
        return sum(n * math.exp(-(day - d).days / FATIGUE_TAU_DAYS) for d, n in per_day.items() if d <= day)
    # Tageswerte schwanken mit dem Trainingsrhythmus – verglichen werden Wochen-Durchschnitte: die letzten
    # 7 Tage gegen die staerkste der vier Wochen davor. So faellt der geplante Anstieg im Block (≈ +10 % je
    # Woche) nicht auf, ein sprunghafter Anstieg schon. Nach einer Pause (eine Vorwoche ohne Training) gibt
    # es keinen sinnvollen Vergleich – sonst wuerde die Rueckkehr als Ueberlastung gelten.
    def week_mean(offset):
        days = [on_date - timedelta(days=offset + k) for k in range(7)]
        return sum(value(d) for d in days) / 7, any(d in per_day for d in days)
    recent, _ = week_mean(0)
    past = [week_mean(7 * w) for w in range(1, FATIGUE_BASELINE_DAYS // 7 + 1)]
    if not all(trained for _, trained in past):
        return None
    return recent, max(mean for mean, _ in past)


def deload_signals(profile_id, split, on_date=None):
    """Hinweise auf aufgelaufene Ermuedung. Zwei davon -> Entlastung (Bell et al.: bedarfsgesteuert,
    ein fester Rhythmus ist nicht belegt). Der Ermuedungswert allein loest nie eine Entlastung aus."""
    rows = _split_rows(split)
    hist = progression.sessions_by_exercise(profile_id, [e["exercise_id"] for e in rows])
    signals = []
    drifting = [e["name"] for e in rows if progression.rpe_drift(hist[e["exercise_id"]])]
    if len(drifting) >= 2:
        signals.append(f"Anstrengung steigt bei gleichem Gewicht ({', '.join(drifting[:3])})")
    falling = [e["name"] for e in rows
               if len(hist[e["exercise_id"]]) >= 3 and (progression.e1rm_trend(hist[e["exercise_id"]][:3]) or 0) < -2]
    if len(falling) >= 2:
        signals.append(f"Leistung sinkt ({', '.join(falling[:3])})")
    stuck = [e["name"] for e in rows if progression.is_plateau(hist[e["exercise_id"]])]
    if len(stuck) >= 2:
        signals.append(f"Stillstand bei mehreren Übungen ({', '.join(stuck[:3])})")
    recent = db.rows("SELECT soreness FROM checkins WHERE profile_id=? ORDER BY date DESC LIMIT 3", (profile_id,))
    sore = sum(1 for r in recent if max((json.loads(r["soreness"] or "{}") or {"x": 0}).values()) >= 2)
    if len(recent) >= 3 and sore >= 3:
        signals.append("Muskelkater hält dauerhaft an")
    load = fatigue(profile_id, on_date)
    if load and load[1] > 0 and load[0] >= FATIGUE_HIGH * load[1]:
        signals.append(f"Trainingslast {round(100 * (load[0] / load[1] - 1))} % über deinen letzten Wochen")
    return signals


def extend_block(block, params):
    """Eine weitere Trainingswoche vor die Entlastung schieben (Anstrengung bleibt auf dem Gipfel)."""
    rir = params.get("rir") or []
    params["rir"] = (rir[:-1] + [rir[-2] if len(rir) >= 2 else 1, rir[-1]]) if rir else rir
    blocks._save_params(block["id"], params, weeks=block["weeks"] + 1)


def reclassify(profile_id):
    """Erfahrung aus den Daten nachziehen – nur wenn sie nicht von Hand gesetzt wurde."""
    profile = db.get_row("profiles", profile_id) or {}
    if profile.get("experience") and not profile.get("experience_auto"):
        return
    level, why = cold_start.infer_experience(profile_id)
    order = ["einsteiger", "fortgeschritten", "erfahren"]
    if level and order.index(level) > order.index(profile.get("experience") or "einsteiger"):
        db.update_row("profiles", profile_id, {"experience": level, "experience_auto": 1})
        explain.log(profile_id, "info", f"Einstufung angehoben auf „{level}“: {why}. Der nächste Block "
                    "startet mit mehr Volumen.")


def review(profile_id, on_date=None):
    with blocks.LOCK:                       # parallele /today-Aufrufe duerfen nicht doppelt nachholen
        return _review(profile_id, on_date)


def _review(profile_id, on_date=None):
    on_date = on_date or stats.today()
    split = db.active_split(profile_id)
    if not split:
        return {"block": None, "events": []}
    reclassify(profile_id)
    block = blocks.ensure(profile_id, split)
    st = blocks.state(block, on_date)
    if st["finished"]:
        block, split = rollover(profile_id, block, split, on_date)
        st = blocks.state(block, on_date)
    params = blocks._params(block)
    week = st["week"]
    if params.get("reviewed", 0) >= week or week == 1:
        params["reviewed"] = max(params.get("reviewed", 0), week)
        blocks._save_params(block["id"], params)
        return {"block": st, "events": explain.recent(profile_id, 10)}

    # 1. Entlastung nur bei Bedarf: zwei Signale -> jetzt; sonst am Blockende eine Woche weiter (bis 8 Wochen)
    signals = deload_signals(profile_id, split, on_date)
    if not st["deload"] and week >= 3 and len(signals) >= 2:
        block["weeks"] = week
        # Diese Woche ist jetzt die Entlastung: Reserve wie im Deload, und als geprueft markieren – sonst
        # wuerde ein zweiter Aufruf in derselben Woche (weniger Signale) die Entlastung wieder aufheben.
        params["rir"] = (params.get("rir") or vp.rir_by_week(week))[: week - 1] + [vp.DELOAD_RIR]
        params["reviewed"] = week
        blocks._save_params(block["id"], params, weeks=week)
        explain.log(profile_id, "deload", "Entlastungswoche vorgezogen: " + "; ".join(signals)
                    + " – diese Woche leichter, danach neuer Block.", None, split["id"])
        return {"block": blocks.state(blocks.active(profile_id), on_date), "events": explain.recent(profile_id, 10)}
    if st["deload"] and len(signals) < 2 and block["weeks"] < MAX_BLOCK_WEEKS:
        extend_block(block, params)
        params["reviewed"] = week
        blocks._save_params(block["id"], params)
        explain.log(profile_id, "info", "Weiter ohne Entlastung: Leistung und Anstrengung passen noch – "
                    "eine zusätzliche Trainingswoche.", None, split["id"])
        block = blocks.active(profile_id)
        return {"block": blocks.state(block, on_date), "events": explain.recent(profile_id, 10)}

    if not st["deload"]:
        swap_plateaus(profile_id, split)
    params["reviewed"] = week
    blocks._save_params(block["id"], params)
    return {"block": st, "events": explain.recent(profile_id, 10)}


def _swap_exercise(profile_id, split, item, injuries, why):
    """Uebung im Plan durch die beste Alternative desselben Musters ersetzen. -> neue Uebung | None"""
    in_plan = {e["exercise_id"] for e in _split_rows(split)}
    alts = generate.alternatives(item["pattern"], in_plan, injuries)
    if not alts:
        return None
    new = alts[0]
    db.update_row("split_exercises", item["id"], {"exercise_id": new["id"]})
    explain.log(profile_id, "swap", f"{item['name']} → {new['name']}: {why}", new["id"], split["id"])
    return new


def swap_plateaus(profile_id, split, today=None):
    """Zweiter Stillstand an derselben Uebung (nach dem 10-%-Reset) -> Variante tauschen. Hoechstens
    ein struktureller Eingriff pro Woche."""
    profile = db.get_row("profiles", profile_id) or {}
    injuries = generate.settings_from(profile)["injuries"]
    for item in _split_rows(split):
        hist = progression.sessions(profile_id, item["exercise_id"])
        window = 2 if (profile.get("experience") or "einsteiger") == "einsteiger" else progression.PLATEAU_SESSIONS
        if not progression.is_plateau(hist, window):
            continue
        # Nur ein Stillstand VOR den jetzt stagnierenden Einheiten zaehlt als "schon einmal zurueckgesetzt";
        # sonst wuerde der erste Stillstand sofort den Tausch ausloesen.
        stall_since = hist[window - 1]["date"]
        earlier = db.one("SELECT COUNT(*) AS n FROM plan_events WHERE profile_id=? AND exercise_id=? AND kind='plateau'"
                         " AND date < ? AND date >= date(?, '-56 day')",
                         (profile_id, item["exercise_id"], stall_since, stall_since))["n"]
        if earlier and _swap_exercise(profile_id, split, item, injuries,
                                      "weiter Stillstand nach dem Reset – neue Variante, gleicher Muskel"):
            return True
    return False


def report_pain(profile_id, area):
    """Schmerz melden: Bereich ins Profil, belastende Uebungen im Plan ersetzen."""
    from .muscles import INJURY_AREAS
    if area not in INJURY_AREAS:
        raise ValueError("Unbekannter Körperbereich.")
    profile = db.get_row("profiles", profile_id) or {}
    label = INJURY_AREAS[area]
    current = [x.strip() for x in (profile.get("limitations") or "").replace(";", ",").split(",") if x.strip()]
    if label.lower() not in [x.lower() for x in current]:
        current.append(label)
    db.update_row("profiles", profile_id, {"limitations": ", ".join(current)})
    split = db.active_split(profile_id)
    swapped = []
    if split:
        injuries = generate.settings_from(db.get_row("profiles", profile_id))["injuries"]
        for item in _split_rows(split):
            ex = db.one("SELECT contraindications FROM exercises WHERE id=?", (item["exercise_id"],)) or {}
            if set((ex.get("contraindications") or "").split(",")) & injuries:
                new = _swap_exercise(profile_id, db.active_split(profile_id), item, injuries,
                                     f"belastet {label} – ersetzt")
                if new:
                    swapped.append({"from": item["name"], "to": new["name"]})
    explain.log(profile_id, "info", f"Schmerz bei {label} gemeldet: belastende Übungen im Plan ersetzt.",
                None, split["id"] if split else None)
    return {"limitations": ", ".join(current), "swapped": swapped}


def rollover(profile_id, block, split, on_date=None):
    """Blockende: neuer Block, Isolationsuebungen wechseln (Hauptuebungen bleiben)."""
    params = blocks._params(block)
    variation = params.get("variation", 0) + 1
    profile = db.get_row("profiles", profile_id) or {}
    injuries = generate.settings_from(profile)["injuries"]
    in_plan = {e["exercise_id"] for e in _split_rows(split)}
    swapped = 0
    for e in _split_rows(split):
        # nur Isolationsuebungen, die der Plan-Ersteller gewaehlt hat – selbst eingetragene bleiben
        if e.get("pattern") in generate.COMPOUND or e.get("pattern") is None or not e.get("generated"):
            continue
        alts = [a for a in generate.alternatives(e["pattern"], in_plan, injuries)][:3]
        if not alts:
            continue
        new = alts[variation % len(alts)]
        db.update_row("split_exercises", e["id"], {"exercise_id": new["id"]})
        in_plan.add(new["id"])
        explain.log(profile_id, "swap", f"{e['name']} → {new['name']}: neuer Block, frischer Reiz bei gleichem Muskel",
                    new["id"], split["id"])
        swapped += 1
    split = db.active_split(profile_id)
    age = (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None
    # Ziel und Erfahrung aus dem Profil: eine Hoeherstufung (reclassify) wirkt ab dem neuen Block
    nb = blocks.create(profile_id, split["id"], profile.get("goal") or block["goal"],
                       profile.get("experience") or block["experience"],
                       db.parse_focus(profile.get("focus")), age, variation,
                       start=blocks.monday(on_date or stats.today()))
    explain.log(profile_id, "info", f"Neuer Block gestartet (Woche 1 von {nb['weeks']})"
                + (f", {swapped} Isolationsübungen gewechselt" if swapped else ""), None, split["id"])
    return nb, split
