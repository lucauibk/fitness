"""KI-Zweitmeinung zum Plan (optional).

Der regelbasierte Check lebt in planner/validate.py. Hier wird – nur mit ANTHROPIC_API_KEY in
.env und nur auf ausdruecklichen Knopfdruck – der Plan (Uebungen, Saetze, Wiederholungen; keine
Namen, keine Trainingsdaten) an die Anthropic-API geschickt.
"""
import logging

from . import ai, db
from .planner import validate
from .planner.muscles import LABEL
from .planner.profile_model import injuries_from

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ KI
def _plan_text(split):
    lines = [f"Split: {split['name']}"]
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    for day in split["days"]:
        when = ", ".join(wd[i] for i in day.get("weekdays") or []) or "ohne festen Wochentag"
        lines.append(f"\n{day['label']} ({when}):")
        for ex in day["exercises"]:
            group = LABEL.get(validate.PATTERN_GROUP.get(ex.get("pattern")), "?")
            lines.append(f"- {ex['name']} [{group}]: {ex['target_sets']} Sätze × "
                         f"{ex['target_reps_min']}–{ex['target_reps_max']} Wdh.")
    return "\n".join(lines)


def ai_review(profile_id):
    """Zweite Meinung per KI. Gibt {"text": ...} oder {"error": ...} zurueck –
    ein Fehler hier darf den regelbasierten Check nie verhindern."""
    if not ai.available():
        return {"error": "Kein KI-Schlüssel hinterlegt (ANTHROPIC_API_KEY in .env)."}
    split = db.active_split(profile_id)
    if not split or not split["days"]:
        return {"error": "Noch kein Split zum Prüfen."}
    profile = db.get_row("profiles", profile_id) or {}
    rules = validate.analyze({"days": split["days"]}, injuries_from(profile.get("limitations")))["findings"]
    from . import analysis as plan_analysis
    data = plan_analysis.analyze(profile_id)
    volume_text = "\n".join(
        f"- {v['label']}: {v['actual']} Sätze/Woche (Richtwert {v['target']}), "
        f"{v['frequency']}× pro Woche" for v in data["volume"])
    ideas = "\n".join(f"- {s['text']} ({s['why']})" for s in data["suggestions"][:8])
    prompt = (
        "Du prüfst einen Krafttrainingsplan für eine Freizeitsportlerin bzw. einen "
        "Freizeitsportler (Muskelaufbau, allgemeine Fitness). Antworte auf Deutsch, knapp, "
        "als kurze Liste mit höchstens 5 Punkten. Nenne nur, was wirklich fehlt oder "
        "unausgewogen ist, jeweils mit einer konkreten Übung als Vorschlag. Keine "
        "medizinischen Aussagen, keine Einleitung, kein Schlusssatz, kein Markdown "
        "(keine Sternchen, keine Überschriften), Punkte mit „–“ beginnen. Wenn der Plan "
        "ausgewogen ist, sag das in einem Satz.\n\n"
        f"{_plan_text(split)}\n\n"
        f"Wochenvolumen laut Plan:\n{volume_text}\n\n"
        + (f"Die App schlägt bereits vor:\n{ideas}\n\n" if ideas else "")
        + "Ein automatischer Check hat bereits gefunden:\n"
        + ("\n".join(f"- {f['text']}" for f in rules) or "- nichts")
        + "\nWiederhole diese Punkte nicht, sondern ergänze nur, was darüber hinausgeht."
    )
    return ai.ask(prompt, max_tokens=500)
