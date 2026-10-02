"""Wochen-Check: gleicht Ist und Soll ab und passt den Block an.

Passiert automatisch (einmal pro Woche, beim Oeffnen von „Heute“) und wird begruendet:
- verpasste Saetze einer Muskelgruppe werden in der Folgewoche nachgeholt (statt zu verfallen),
- steigt bei gleichem Gewicht die Anstrengung (RPE-Drift), wird die laufende Woche zum Deload,
- war eine Gruppe ueberall sehr leicht (⌀ RPE ≤ 6,5), kommt ein Satz dazu,
- am Blockende startet ein neuer Block mit frischen Isolationsuebungen.
"""
import json
from datetime import date, timedelta

from .. import db, stats
from . import blocks, cold_start, explain, generate, progression
from .muscles import GROUPS, LABEL, PATTERN_GROUP, landmark

MISSED_MIN = 2          # so viele Saetze muessen fehlen, damit nachgeholt wird
EASY_RPE = 6.5
EASY_MIN_SETS = 6
MAX_ADJUST_PER_GROUP = 2


def _split_rows(split):
    rows = []
    for d in split["days"]:
        for e in d["exercises"]:
            rows.append(e)
    return rows


def actual_sets(profile_id, start, end):
    """{Gruppe: Saetze} und {Gruppe: [RPE]} im Zeitraum [start, end]."""
    rows = db.rows(
        "SELECT e.pattern, s.rpe FROM sets s JOIN workouts w ON w.id = s.workout_id"
        " JOIN exercises e ON e.id = s.exercise_id"
        " WHERE w.profile_id=? AND s.warmup=0 AND substr(COALESCE(w.started_at, w.finished_at),1,10) BETWEEN ? AND ?",
        (profile_id, start.isoformat(), end.isoformat()))
    sets, rpes = {g: 0 for g in GROUPS}, {g: [] for g in GROUPS}
    for r in rows:
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
    for e in _split_rows(split):
        g = PATTERN_GROUP.get(e.get("pattern"))
        if g:
            out[g] += blocks.sets_for(block, e, day)
    return out


def _bump(block, params, week, split, group, why, budget=None, planned=None):
    """+1 Satz bei den Uebungen der Gruppe mit der besten SFR (hoechstens 2 Uebungen).
    budget: [Rest] – Nachholen insgesamt hoechstens +20 % der Wochensaetze; nie ueber MRV."""
    rows = [e for e in _split_rows(split) if PATTERN_GROUP.get(e.get("pattern")) == group]
    day = date.fromisoformat(block["start_date"]) + timedelta(weeks=week - 1)
    rows = [e for e in rows if blocks.sets_for(block, e, day) < blocks.MAX_EXERCISE_SETS]
    rows.sort(key=lambda e: -(db.one("SELECT sfr FROM exercises WHERE id=?", (e["exercise_id"],)) or {}).get("sfr", 0))
    adj = params.setdefault("adjust", {}).setdefault(str(week), {})
    done = 0
    for e in rows[:MAX_ADJUST_PER_GROUP]:
        if budget is not None and budget[0] <= 0:
            break
        if planned is not None and planned.get(group, 0) + 1 > landmark(group, "mrv"):
            break
        if budget is not None:
            budget[0] -= 1
        if planned is not None:
            planned[group] = planned.get(group, 0) + 1
        adj[str(e["id"])] = adj.get(str(e["id"]), 0) + 1
        explain.log(block["profile_id"], "sets_up",
                    f"{e['name']}: +1 Satz diese Woche – {why}", e["exercise_id"], split["id"])
        done += 1
    return done


MAX_BLOCK_WEEKS = 8


def deload_signals(profile_id, split):
    """Hinweise auf aufgelaufene Ermuedung. Zwei davon -> Entlastung (Bell et al.: bedarfsgesteuert,
    ein fester Rhythmus ist nicht belegt)."""
    rows = _split_rows(split)
    hist = {e["exercise_id"]: progression.sessions(profile_id, e["exercise_id"]) for e in rows}
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
    return signals


def extend_block(block, params):
    """Eine weitere Trainingswoche vor die Entlastung schieben (Volumen bleibt auf dem Gipfel)."""
    ratios = {g: v[:-1] + [v[-2] if len(v) >= 2 else v[-1], v[-1]] for g, v in params["ratios"].items()}
    rir = params.get("rir") or []
    params["ratios"] = ratios
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
    on_date = on_date or date.today()
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

    prev = week - 1
    prev_start = date.fromisoformat(block["start_date"]) + timedelta(weeks=prev - 1)
    prev_end = prev_start + timedelta(days=6)
    was_deload = prev == block["weeks"]
    actual, rpes = actual_sets(profile_id, prev_start, prev_end)
    plan = planned_sets(block, split, prev)

    # 1. Entlastung nur bei Bedarf: zwei Signale -> jetzt; sonst am Blockende eine Woche weiter (bis 8 Wochen)
    signals = deload_signals(profile_id, split)
    if not st["deload"] and week >= 3 and len(signals) >= 2:
        block["weeks"] = week
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

    if not st["deload"] and not was_deload:
        this_week = planned_sets(block, split, week)
        budget = [max(1, round(0.2 * sum(this_week.values())))]      # Nachholen: hoechstens +20 %
        for g in GROUPS:
            missed = plan[g] - actual[g]
            if plan[g] >= 3 and missed >= MISSED_MIN:
                if _bump(block, params, week, split, g, f"letzte Woche fehlten {missed} Sätze {LABEL[g]}",
                         budget, this_week):
                    continue
            elif len(rpes[g]) >= EASY_MIN_SETS and sum(rpes[g]) / len(rpes[g]) <= EASY_RPE \
                    and plan[g] < landmark(g, "mav"):
                _bump(block, params, week, split, g, f"{LABEL[g]} war letzte Woche überall leicht (⌀ RPE "
                      f"{sum(rpes[g]) / len(rpes[g]):.1f})", budget, this_week)
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
        if e.get("pattern") in generate.COMPOUND or e.get("pattern") is None:
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
    nb = blocks.create(profile_id, split["id"], block["goal"], block["experience"],
                       db.parse_focus(profile.get("focus")), age, variation,
                       start=blocks.monday(on_date or date.today()))
    explain.log(profile_id, "info", f"Neuer Block gestartet (Woche 1 von {nb['weeks']})"
                + (f", {swapped} Isolationsübungen gewechselt" if swapped else ""), None, split["id"])
    return nb, split
