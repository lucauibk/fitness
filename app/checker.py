"""Plan-Check: findet Luecken im aktiven Split.

Der Grundcheck ist regelbasiert – er braucht kein Netz und ist nachvollziehbar:
Wochensaetze je Muskelgruppe, Drueck-/Zug-Verhaeltnis und haeufige blinde Flecken
(Beinrueckseite, Waden, hintere Schulter).

Optional kann eine KI den Plan zusaetzlich beurteilen. Das passiert nur, wenn in .env
ein ANTHROPIC_API_KEY steht, und nur auf ausdruecklichen Knopfdruck – dann wird der
Plan (Uebungen, Saetze, Wiederholungen; keine Namen, keine Trainingsdaten) an die
Anthropic-API geschickt.
"""
import logging

from . import ai, db
from .stats import GROUP_LABEL

log = logging.getLogger(__name__)

# Richtwerte fuer Muskelaufbau: grob 10–20 harte Saetze je Muskelgruppe pro Woche.
LOW_SETS = 6
GOOD_SETS = 10

HAMSTRING = ("pattern:hinge", "pattern:leg_iso_back", "pattern:glutes",
             "beinbeuger", "hyperextension", "rumänisches kreuzheben", "kreuzheben", "hip thrust",
             "good morning", "nordic")
CALVES = ("wade", "pattern:calves")
REAR_DELT = ("pattern:rear_delt", "pattern:h_pull", "face pull", "reverse butterfly", "rudern", "rear delt", "seitheben vorgebeugt")


def _weekly(split):
    """Geschaetzte Saetze pro Woche je Muskelgruppe. Ein Tag ohne festen Wochentag
    zaehlt einmal pro Woche – eine Annahme, die im Ergebnis auch so benannt wird.
    names enthaelt neben den Uebungsnamen auch die Bewegungsmuster ("pattern:hinge"),
    damit z. B. Kreuzheben als Beinrueckseite zaehlt, obwohl es unter Ruecken steht."""
    sets = {g: 0 for g in GROUP_LABEL}
    names = {g: [] for g in GROUP_LABEL}
    rotating = False
    for day in split["days"]:
        per_week = len(day.get("weekdays") or []) or 1
        if not day.get("weekdays"):
            rotating = True
        for ex in day["exercises"]:
            g = ex.get("muscle_group")
            if g not in sets:
                continue
            sets[g] += (ex.get("target_sets") or 0) * per_week
            names[g].append(ex["name"].lower())
            if ex.get("pattern"):
                names[g].append(f"pattern:{ex['pattern']}")
    return sets, names, rotating


def _has(names, keywords):
    return any(k in n for n in names for k in keywords)


def analyze_split(split):
    """Regeln fuer einen beliebigen Plan – auch fuer die Vorschau im Assistenten, die
    noch nicht gespeichert ist. Erwartet days[].weekdays und
    days[].exercises[].{name, muscle_group, target_sets}."""
    sets, names, rotating = _weekly(split)
    all_names = [n for group in names.values() for n in group]
    findings = []

    for g, label in GROUP_LABEL.items():
        if sets[g] == 0:
            findings.append({"level": "warn", "group": g,
                             "text": f"{label}: keine Übung im Plan."})
        elif sets[g] < LOW_SETS:
            findings.append({"level": "info", "group": g,
                             "text": f"{label}: nur etwa {sets[g]} Sätze pro Woche – "
                                     f"für Aufbau sind {GOOD_SETS}–20 üblich."})

    push = sets["brust"] + sets["schultern"]
    pull = sets["ruecken"]
    if pull and push > pull * 1.6:
        findings.append({"level": "info", "group": "ruecken",
                         "text": f"Deutlich mehr Drücken ({push} Sätze) als Ziehen ({pull}). "
                                 "Mehr Rücken- und Zugarbeit entlastet die Schultern."})

    if sets["beine"] and not _has(all_names, HAMSTRING):
        findings.append({"level": "warn", "group": "beine",
                         "text": "Keine Übung für die Beinrückseite – "
                                 "z. B. Beinbeuger oder Rumänisches Kreuzheben."})
    if sets["beine"] and not _has(all_names, CALVES):
        findings.append({"level": "info", "group": "beine",
                         "text": "Keine Wadenübung im Plan."})
    if push and not _has(all_names, REAR_DELT):
        findings.append({"level": "info", "group": "schultern",
                         "text": "Keine Übung für die hintere Schulter – "
                                 "z. B. Face Pulls oder Reverse Butterfly."})

    empty_days = [d["label"] for d in split["days"] if not d["exercises"]]
    for label in empty_days:
        findings.append({"level": "warn", "text": f"„{label}“ hat noch keine Übungen."})

    return {
        "weekly": [{"group": g, "label": GROUP_LABEL[g], "sets": sets[g]} for g in GROUP_LABEL],
        "rotating": rotating,
        "findings": findings,
    }


# ------------------------------------------------------------------ KI
def _plan_text(split):
    lines = [f"Split: {split['name']}"]
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    for day in split["days"]:
        when = ", ".join(wd[i] for i in day.get("weekdays") or []) or "ohne festen Wochentag"
        lines.append(f"\n{day['label']} ({when}):")
        for ex in day["exercises"]:
            group = GROUP_LABEL.get(ex.get("muscle_group"), "?")
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
    rules = analyze(profile_id)["findings"]
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
