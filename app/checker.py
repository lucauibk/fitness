"""Plan-Check: findet Luecken im aktiven Split.

Der Grundcheck ist regelbasiert – er braucht kein Netz und ist nachvollziehbar:
Wochensaetze je Muskelgruppe, Drueck-/Zug-Verhaeltnis und haeufige blinde Flecken
(Beinrueckseite, Waden, hintere Schulter).

Optional kann eine KI den Plan zusaetzlich beurteilen. Das passiert nur, wenn in .env
ein ANTHROPIC_API_KEY steht, und nur auf ausdruecklichen Knopfdruck – dann wird der
Plan (Uebungen, Saetze, Wiederholungen; keine Namen, keine Trainingsdaten) an die
Anthropic-API geschickt.
"""
import json
import logging
import os
import re
import urllib.error
import urllib.request

from . import db
from .stats import GROUP_LABEL

log = logging.getLogger(__name__)

# Richtwerte fuer Muskelaufbau: grob 10–20 harte Saetze je Muskelgruppe pro Woche.
LOW_SETS = 6
GOOD_SETS = 10

HAMSTRING = ("beinbeuger", "rumänisches kreuzheben", "kreuzheben", "hip thrust",
             "good morning", "nordic")
CALVES = ("wade",)
REAR_DELT = ("face pull", "reverse butterfly", "rudern", "rear delt", "seitheben vorgebeugt")
AI_MODEL_DEFAULT = "claude-haiku-4-5-20251001"


def _weekly(split):
    """Geschaetzte Saetze pro Woche je Muskelgruppe. Ein Tag ohne festen Wochentag
    zaehlt einmal pro Woche – eine Annahme, die im Ergebnis auch so benannt wird."""
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
    return sets, names, rotating


def _has(names, keywords):
    return any(k in n for n in names for k in keywords)


def analyze(profile_id):
    split = db.active_split(profile_id)
    base = {"ai_available": ai_available()}
    if not split or not split["days"]:
        return {**base, "split": None, "weekly": [], "findings": [
            {"level": "info", "text": "Noch kein Split mit Trainingstagen angelegt."}]}

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

    if sets["beine"] and not _has(names["beine"], HAMSTRING):
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
        **base,
        "split": {"id": split["id"], "name": split["name"]},
        "weekly": [{"group": g, "label": GROUP_LABEL[g], "sets": sets[g]} for g in GROUP_LABEL],
        "rotating": rotating,
        "findings": findings,
    }


# ------------------------------------------------------------------ KI
def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


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
    if not ai_available():
        return {"error": "Kein KI-Schlüssel hinterlegt (ANTHROPIC_API_KEY in .env)."}
    split = db.active_split(profile_id)
    if not split or not split["days"]:
        return {"error": "Noch kein Split zum Prüfen."}
    rules = analyze(profile_id)["findings"]
    prompt = (
        "Du prüfst einen Krafttrainingsplan für eine Freizeitsportlerin bzw. einen "
        "Freizeitsportler (Muskelaufbau, allgemeine Fitness). Antworte auf Deutsch, knapp, "
        "als kurze Liste mit höchstens 5 Punkten. Nenne nur, was wirklich fehlt oder "
        "unausgewogen ist, jeweils mit einer konkreten Übung als Vorschlag. Keine "
        "medizinischen Aussagen, keine Einleitung, kein Schlusssatz, kein Markdown "
        "(keine Sternchen, keine Überschriften), Punkte mit „–“ beginnen. Wenn der Plan "
        "ausgewogen ist, sag das in einem Satz.\n\n"
        f"{_plan_text(split)}\n\n"
        "Ein automatischer Check hat bereits gefunden:\n"
        + ("\n".join(f"- {f['text']}" for f in rules) or "- nichts")
        + "\nWiederhole diese Punkte nicht, sondern ergänze nur, was darüber hinausgeht."
    )
    body = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", AI_MODEL_DEFAULT),
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        log.warning("KI-Check fehlgeschlagen: HTTP %s", e.code)
        return {"error": "Die KI hat die Anfrage abgelehnt (Schlüssel oder Modell prüfen)."
                if e.code in (401, 403, 404) else f"KI gerade nicht erreichbar (HTTP {e.code})."}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("KI-Check nicht erreichbar: %s", e)
        return {"error": "KI gerade nicht erreichbar – der Pi hat vermutlich kein Internet."}
    text = "".join(part.get("text", "") for part in data.get("content", [])
                   if part.get("type") == "text").strip()
    # Falls doch Markdown kommt: Fettschrift und Aufzaehlungszeichen glaetten
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("•", "–")
    return {"text": text} if text else {"error": "Die KI hat keine Antwort geliefert."}
