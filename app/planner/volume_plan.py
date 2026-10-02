"""Mesozyklus: Wochenvolumen je Muskel ueber einen Block (steigend, am Ende Deload).

Woche 1 startet nahe MEV, jede Woche kommen Saetze dazu (Richtung MAV, bei Fokus Richtung MRV),
die letzte Woche ist ein Deload. Quellen: Renaissance Periodization (Volumen-Landmarken),
Schoenfeld et al. (Dosis-Wirkung, abnehmender Nutzen ab ca. 12-20 Saetzen), Bell et al. (Deload).
"""
from .muscles import GROUPS, PATTERN_GROUP

LEVEL = {"einsteiger": 0.0, "fortgeschritten": 0.6, "erfahren": 1.0}
GOAL_FACTOR = {"kraft": 0.8, "aufbau": 1.0, "abnehmen": 0.9, "fitness": 0.85}
# Wochen je Block inklusive Deload
BLOCK_WEEKS = {"einsteiger": 6, "fortgeschritten": 5, "erfahren": 5}
# Wiederholungen in Reserve je Trainingswoche: vorsichtig starten, am Ende nah ans Limit
RIR_RAMP = {5: [3, 2, 2, 1], 6: [3, 3, 2, 2, 1]}
DELOAD_RIR = 4

MINUTES_PER_SET = {"kraft": 4.0, "aufbau": 3.2, "abnehmen": 2.8, "fitness": 2.8}
WARMUP_MINUTES = 8
MAX_SETS_PER_SESSION = 26


def capacity(days, minutes, goal="aufbau"):
    """(Saetze pro Woche, Saetze pro Einheit). minutes=None -> nur die Obergrenze je Einheit."""
    if not minutes:
        return days * MAX_SETS_PER_SESSION, MAX_SETS_PER_SESSION
    per_set = MINUTES_PER_SET.get(goal, 3.2)
    per_session = max(6, min(MAX_SETS_PER_SESSION, int((minutes - WARMUP_MINUTES) / per_set)))
    return days * per_session, per_session


def weekly_volume(days, item_sets, cycle=1.0):
    """Direkte Saetze pro Woche je Muskelgruppe – dieselbe Rechnung fuer Generator und Pruefung.
    item_sets(uebung) -> (Muster, Saetze). Ein Tag mit mehreren Wochentagen zaehlt mehrfach; beim
    Wechselplan (cycle = Plantage / Einheiten pro Woche) verteilt sich ein Durchlauf auf mehr als eine Woche.
    Indirekte Arbeit (Po bei Kniebeugen, Trizeps beim Druecken) zaehlt nicht: sie steckt schon in den
    Landmarken (muscles.GROUPS)."""
    out = {g: 0 for g in GROUPS}
    for day in days:
        times = max(1, len(day.get("weekdays") or []))
        for item in day.get("exercises", []):
            pattern, sets = item_sets(item)
            g = PATTERN_GROUP.get(pattern)
            if g:
                out[g] += (sets or 0) * times
    return {g: round(v / (cycle or 1.0)) for g, v in out.items()}


def session_minutes(sets, goal="aufbau"):
    """Geschaetzte Dauer einer Einheit (Aufwaermen + Arbeitssaetze samt Pause), auf 5 Minuten gerundet."""
    return int(5 * round((WARMUP_MINUTES + sets * MINUTES_PER_SET.get(goal, 3.2)) / 5))


def rir_by_week(weeks):
    ramp = RIR_RAMP.get(weeks) or [3] + [2] * (weeks - 3) + [1]
    return ramp[: weeks - 1] + [DELOAD_RIR]


def block_targets(experience, goal, focus_groups=(), age=None, weeks=None):
    """{Gruppe: [Saetze je Woche]} fuer einen ganzen Block (letzte Woche = Deload)."""
    n = weeks or BLOCK_WEEKS.get(experience, 5)
    train = n - 1
    level = LEVEL.get(experience, 0.0)
    factor = GOAL_FACTOR.get(goal, 1.0) * (0.85 if age and age >= 50 else 1.0)
    focus = set(focus_groups)
    out = {}
    for g, (_, mv, mev, mav, mrv) in GROUPS.items():
        if g in focus:
            start, end = mev + (mav - mev) * 0.6, mav + (mrv - mav) * 0.6
        elif focus:
            start = end = max(mv, mev - 1)          # Spezialisierung: der Rest nur Erhaltung
        else:
            start = mev + (mav - mev) * level * 0.5
            end = mev + (mav - mev) * (0.5 + 0.5 * level)
        weekly = []
        for w in range(train):
            value = start + (end - start) * (w / max(1, train - 1))
            weekly.append(max(mv, round(value * factor)) if value else 0)
        for i in range(1, train):                   # nie weniger als die Vorwoche
            weekly[i] = max(weekly[i], weekly[i - 1])
        weekly.append(max(mv, round(weekly[0] * 0.5)) if weekly[0] else 0)
        out[g] = weekly
    return out


def fit_to_capacity(targets, total):
    """Passt alle Wochen an die verfuegbare Zeit an (Spitzenwoche zaehlt). -> (Ziele, Hinweis|None)."""
    weeks = len(next(iter(targets.values())))
    peak = max(sum(v[w] for v in targets.values()) for w in range(weeks - 1))
    if peak <= total:
        return targets, None
    # Erhaltung (MV) hat Vorrang: gekuerzt wird nur der Zuschlag darueber – auch der des Fokus –, und zwar
    # fuer alle Muskeln im selben Verhaeltnis. Sonst blieben die Ziele ueber der Zeit, und die Verteilung auf die
    # Tage wuerde zufaellig einzelne Muskeln unter Erhaltung druecken.
    pw = max(range(weeks - 1), key=lambda w: sum(v[w] for v in targets.values()))
    base = sum(min(v[pw], GROUPS[g][1]) for g, v in targets.items())
    extra = peak - base
    f = max(0.0, (total - base) / extra) if extra else 0.0
    out = {g: [(min(x, GROUPS[g][1]) + int(max(0, x - GROUPS[g][1]) * f)) if x else 0 for x in v]
           for g, v in targets.items()}
    left = max(sum(v[w] for v in out.values()) for w in range(weeks - 1))
    note = (f"Deine Zeit reicht für ≈ {total} Sätze pro Woche, der Block würde {peak} verlangen – "
            "gekürzt ist nur, was über die Erhaltung hinausgeht.")
    if left > total:
        note += (f" Selbst gekürzt bleiben ≈ {left} Sätze – für dieses Volumen bräuchte es mehr Zeit "
                 "pro Einheit oder einen zusätzlichen Trainingstag.")
    return out, note


def reps_for(pattern, goal, compound):
    if goal == "kraft":
        base = (4, 6) if compound else (8, 12)
    elif goal == "aufbau":
        base = (6, 10) if compound else (10, 15)
    else:
        base = (10, 15) if compound else (12, 20)
    if pattern == "glutes":
        return (10, 20) if goal != "kraft" else (8, 12)
    if pattern in ("calves", "core"):
        return (12, 20) if goal != "kraft" else (10, 15)
    if pattern == "hinge" and goal != "kraft":
        return (6, 12)
    return base


def rest_for(pattern, goal, compound):
    """Satzpause in Sekunden (Schoenfeld 2016: lange Pausen bringen mehr)."""
    if compound:
        return 180 if goal == "kraft" else 150
    if pattern in ("core", "calves"):
        return 60
    return 90


def weekly_targets(experience, goal, focus_groups=(), days=3, minutes=None, age=None):
    """Spitzenwoche je Gruppe, zeitlich angepasst: (Ziele, Hinweise, Saetze je Einheit)."""
    total, per_session = capacity(max(2, days), minutes, goal)
    weeks, note = fit_to_capacity(block_targets(experience, goal, focus_groups, age), total)
    peak = {g: max(v[:-1]) for g, v in weeks.items()}
    return peak, ([note] if note else []), per_session
