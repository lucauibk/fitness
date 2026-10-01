"""Eine einzige Muskelgruppen-Einteilung fuer Generator, Progression und Pruefung.

Landmarken in direkten harten Saetzen pro Woche (Renaissance Periodization):
MV = Erhaltung, MEV = kleinste wirksame Menge, MAV = optimaler Bereich (Obergrenze),
MRV = Grenze der Erholung. Indirekte Saetze (z. B. Trizeps beim Bankdruecken) sind in den
Zahlen der kleinen Muskeln schon beruecksichtigt, deshalb liegen sie niedriger.
"""

# Gruppe: (Name, MV, MEV, MAV, MRV)
GROUPS = {
    "brust":      ("Brust", 6, 8, 14, 22),
    "ruecken":    ("Rücken", 6, 10, 16, 25),
    "schultern":  ("Schultern", 4, 6, 14, 24),
    "bizeps":     ("Bizeps", 3, 5, 12, 20),
    "trizeps":    ("Trizeps", 3, 5, 10, 18),
    "quads":      ("Oberschenkel vorne", 6, 8, 14, 20),
    "beinbeuger": ("Beinrückseite", 4, 6, 10, 16),
    "po":         ("Po", 0, 4, 8, 16),
    "waden":      ("Waden", 4, 6, 12, 16),
    "rumpf":      ("Rumpf", 0, 4, 8, 16),
}
LABEL = {k: v[0] for k, v in GROUPS.items()}

# Bewegungsmuster -> Muskelgruppe, die die Saetze bekommt (direkter Reiz)
PATTERN_GROUP = {
    "h_push": "brust", "chest_iso": "brust",
    "h_pull": "ruecken", "v_pull": "ruecken",
    "v_push": "schultern", "lateral": "schultern", "rear_delt": "schultern",
    "biceps": "bizeps", "triceps": "trizeps",
    "squat": "quads", "lunge": "quads", "leg_iso_front": "quads",
    "hinge": "beinbeuger", "leg_iso_back": "beinbeuger",
    "glutes": "po", "calves": "waden", "core": "rumpf",
}
PATTERNS = tuple(PATTERN_GROUP)
COMPOUND = {"h_push", "v_push", "h_pull", "v_pull", "squat", "hinge", "lunge"}

# Muskeln, die ein Muster zusaetzlich deutlich belastet (fuer Erholung am Folgetag)
SECONDARY = {
    "squat": ["po", "beinbeuger"], "lunge": ["po"], "hinge": ["po", "ruecken"],
    "h_push": ["trizeps", "schultern"], "v_push": ["trizeps"],
    "h_pull": ["bizeps", "schultern"], "v_pull": ["bizeps"],
}

# Grosse Muskeln, die 48 h Pause brauchen (wie splits.RECOVERY)
BIG = ("brust", "ruecken", "quads", "beinbeuger", "po")

# Verletzungsbereiche -> Stichwort in exercises.contraindications
INJURY_AREAS = {"knee": "Knie", "lower_back": "unterer Rücken", "shoulder": "Schulter",
                "elbow": "Ellbogen", "wrist": "Handgelenk", "hip": "Hüfte"}

# Alte 6er-Gruppen (exercises.muscle_group) -> neue Gruppen, fuer alte Auswertungen
LEGACY_GROUP = {"brust": "brust", "ruecken": "ruecken", "schultern": "schultern",
                "arme": "bizeps", "beine": "quads", "rumpf": "rumpf"}


def group_of(pattern):
    return PATTERN_GROUP.get(pattern)


def landmark(group, which):
    """which: 'mv' | 'mev' | 'mav' | 'mrv'"""
    _, mv, mev, mav, mrv = GROUPS[group]
    return {"mv": mv, "mev": mev, "mav": mav, "mrv": mrv}[which]
