"""Eingaben des Plan-Erstellers als saubere Daten (aus Profil + Wizard-Angaben)."""
from datetime import date

from .. import db
from .muscles import INJURY_AREAS

GOALS = {"aufbau": "Muskelaufbau", "kraft": "Kraft", "abnehmen": "Abnehmen", "fitness": "Fitness"}
EXPERIENCE = {"einsteiger": "Einsteiger", "fortgeschritten": "Fortgeschritten", "erfahren": "Erfahren"}

# Freitext -> Bereich (die Auswahl-Chips im Wizard schreiben dieselben Schluessel)
INJURY_WORDS = {
    "knee": ("knie",), "lower_back": ("rücken", "ruecken", "bandscheibe", "lws", "kreuz"),
    "shoulder": ("schulter",), "elbow": ("ellbogen", "tennisarm", "golfer"),
    "wrist": ("handgelenk",), "hip": ("hüfte", "huefte"),
}


def injuries_from(text):
    """'Knie, linke Schulter' oder 'knee,shoulder' -> {'knee', 'shoulder'}."""
    low = (text or "").lower()
    found = {area for area in INJURY_AREAS if area in low}
    for area, words in INJURY_WORDS.items():
        if any(w in low for w in words):
            found.add(area)
    return found


def settings_from(profile, overrides=None):
    focus = profile.get("focus")
    s = {"goal": profile.get("goal") or "aufbau",
         "experience": profile.get("experience") or "einsteiger",
         "days": profile.get("days_per_week") or 3,
         "minutes": profile.get("session_minutes"),
         "weekdays": db.parse_weekdays(profile.get("train_weekdays")),
         "focus": db.parse_focus(focus) if isinstance(focus, str) else list(focus or []),
         "limitations": profile.get("limitations") or "",
         "age": (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None}
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    s["minutes"] = None          # die Dauer wird nicht abgefragt: der Plan richtet sich nach dem Volumen
    s["weekdays"] = db.parse_weekdays(s["weekdays"])
    if s["weekdays"]:
        s["days"] = len(s["weekdays"])
    s["goal"] = s["goal"] if s["goal"] in GOALS else "aufbau"
    s["experience"] = s["experience"] if s["experience"] in EXPERIENCE else "einsteiger"
    s["injuries"] = injuries_from(s["limitations"])
    return s
