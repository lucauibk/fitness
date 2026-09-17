"""Wochenvolumen je Muskelgruppe – die Grundlage der Planung.

Statt „Fokus = ein Satz mehr" wird geplant, wie viele harte Saetze eine Muskelgruppe
pro Woche bekommt. Grundlage:

- Schoenfeld et al. 2017 (Dosis-Wirkung): mehr Wochensaetze = mehr Wachstum, ab etwa
  10 Saetzen je Muskel nahezu maximaler Effekt.
- Volumen-Landmarken (Renaissance Periodization): MV = Erhaltung, MEV = kleinste
  wirksame Menge, MAV = optimaler Bereich, MRV = Obergrenze der Erholung.
- Spezialisierung: die Fokus-Muskeln Richtung oberes MAV/MRV, **alle anderen zurueck
  auf Erhaltung** – sonst reicht die Erholung nicht. Solche Bloecke laufen 4–8 Wochen.
- Schoenfeld et al. 2016 (Pausen): ~3 min bei schweren Grunduebungen, ~1–1,5 min
  bei Isolation.
"""

# Saetze pro Woche: (MV, MEV, MAV, Spezialisierung)
LANDMARKS = {
    "brust":      (6, 8, 14, 20),
    "ruecken":    (6, 10, 16, 22),
    "schultern":  (6, 8, 16, 20),
    "arme":       (4, 6, 12, 16),
    "quads":      (6, 8, 14, 18),
    "beinbeuger": (4, 6, 12, 16),
    "po":         (4, 6, 10, 18),
    "waden":      (4, 8, 12, 16),
    "rumpf":      (0, 4, 8, 12),
}
LABEL = {"brust": "Brust", "ruecken": "Rücken", "schultern": "Schultern", "arme": "Arme",
         "quads": "Oberschenkel vorne", "beinbeuger": "Beinrückseite", "po": "Po",
         "waden": "Waden", "rumpf": "Rumpf"}

# Bewegungsmuster -> Zielgruppe des Volumens
PATTERN_TARGET = {
    "h_push": "brust", "chest_iso": "brust",
    "h_pull": "ruecken", "v_pull": "ruecken",
    "v_push": "schultern", "lateral": "schultern", "rear_delt": "schultern",
    "biceps": "arme", "triceps": "arme",
    "squat": "quads", "lunge": "quads", "leg_iso_front": "quads",
    "hinge": "beinbeuger", "leg_iso_back": "beinbeuger",
    "glutes": "po", "calves": "waden", "core": "rumpf",
}
# Fokus des Users -> Zielgruppen
FOCUS_TARGETS = {
    "brust": ["brust"], "ruecken": ["ruecken"], "schultern": ["schultern"], "arme": ["arme"],
    "beine": ["quads", "beinbeuger", "waden"], "po": ["po", "beinbeuger"], "rumpf": ["rumpf"],
}
# Anteil zwischen MEV und MAV je Erfahrung
EXPERIENCE_LEVEL = {"einsteiger": 0.0, "fortgeschritten": 0.6, "erfahren": 1.0}
# Ziel: Faktor aufs Volumen (Kraft arbeitet schwerer, also weniger Saetze)
GOAL_FACTOR = {"kraft": 0.8, "aufbau": 1.0, "abnehmen": 0.9, "fitness": 0.85}

# Minuten pro Satz inkl. Pause (Grunduebungen ~3 min Pause, Isolation ~1,5 min);
# beim Ziel Kraft sind die Pausen laenger, es passen also weniger Saetze in die Zeit.
MINUTES_PER_SET = {"kraft": 4.0, "aufbau": 3.2, "abnehmen": 2.8, "fitness": 2.8}
WARMUP_MINUTES = 8
MAX_SETS_PER_SESSION = 26


def capacity(days, minutes, goal="aufbau"):
    """Wie viele Arbeitssaetze passen in die Woche? minutes=None -> ohne Obergrenze."""
    if not minutes:
        return days * MAX_SETS_PER_SESSION, MAX_SETS_PER_SESSION
    per_set = MINUTES_PER_SET.get(goal, 3.2)
    per_session = max(6, min(MAX_SETS_PER_SESSION, int((minutes - WARMUP_MINUTES) / per_set)))
    return days * per_session, per_session


def targets(experience, goal, focus=(), days=3, minutes=None):
    """Wochensaetze je Zielgruppe plus erklaerende Saetze."""
    level = EXPERIENCE_LEVEL.get(experience, 0.0)
    factor = GOAL_FACTOR.get(goal, 1.0)
    focus_targets = {t for f in focus for t in FOCUS_TARGETS.get(f, [])}
    out, notes = {}, []
    for group, (mv, mev, mav, spec) in LANDMARKS.items():
        if group in focus_targets:
            value = spec
        elif focus_targets:
            # Spezialisierung: der Rest faehrt auf Erhaltung zurueck
            value = max(mv, mev - 2)
        else:
            value = mev + (mav - mev) * level
        out[group] = max(0, round(value * factor))

    total, per_session = capacity(days, minutes, goal)
    needed = sum(out.values())
    if needed > total:
        over = needed - total
        if focus_targets:
            # Spezialisierung: zuerst die uebrigen Muskeln bis auf Erhaltung kuerzen
            for group in sorted(out, key=lambda g: (g in focus_targets, -out[g])):
                if over <= 0:
                    break
                floor = round(LANDMARKS[group][3] * 0.7) if group in focus_targets else LANDMARKS[group][0]
                cut = min(over, max(0, out[group] - floor))
                out[group] -= cut
                over -= cut
        if over > 0:
            # Sonst alles gleichmaessig herunterskalieren, aber nicht unter Erhaltung
            keep = max(0.3, (sum(out.values()) - over) / max(1, sum(out.values())))
            out = {g: max(LANDMARKS[g][0] if out[g] else 0, round(v * keep)) for g, v in out.items()}
        notes.append(f"{days} Einheiten × {per_session} Sätze ≈ {total} Sätze pro Woche – "
                     "mehr passt nicht in deine Zeit, deshalb sind die Vorgaben gekürzt.")
    if focus_targets:
        notes.append("Spezialisierung: " + ", ".join(LABEL[t] for t in sorted(focus_targets))
                     + " bekommen deutlich mehr Sätze, alle anderen Muskeln laufen auf "
                       "Erhaltungsvolumen. So bleibt die Erholung machbar (4–8 Wochen, dann wechseln).")
    else:
        notes.append(f"Volumen nach Erfahrung: {'Einstiegsniveau (MEV)' if level == 0 else 'im optimalen Bereich (MAV)' if level == 1 else 'zwischen Einstieg und optimalem Bereich'}"
                     " – Richtwert sind 10+ harte Sätze je Muskel und Woche.")
    if goal == "kraft":
        notes.append("Ziel Kraft: etwas weniger Sätze, dafür schwerer und mit längeren Pausen.")
    return out, notes, per_session


def reps_for(pattern, goal, compound):
    """Wiederholungsbereich je Muster und Ziel (Hip Thrust und Waden mögen es höher,
    Grundübungen bei Kraft niedriger)."""
    if goal == "kraft":
        base = (4, 6) if compound else (8, 12)
    elif goal == "aufbau":
        base = (6, 10) if compound else (10, 15)
    else:
        base = (10, 15) if compound else (12, 20)
    if pattern in ("glutes",):
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
