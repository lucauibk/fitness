"""Anbindung an die Anthropic-API – nur aktiv, wenn ANTHROPIC_API_KEY in .env steht.

Alles hier ist Zusatz: jeder Fehler wird als {"error": ...} zurueckgegeben, damit die
regelbasierten Funktionen (Plan-Check, Plan-Assistent) immer weiter funktionieren.
Verschickt werden nur Plandaten und Eckdaten (Alter, Ziel, Erfahrung, Einschraenkungen),
nie Namen oder Trainingsverlaeufe.
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
MODEL_DEFAULT = "claude-haiku-4-5-20251001"
MAX_EXERCISES_PER_DAY = 9


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _clean(text):
    # Falls doch Markdown kommt: Fettschrift und Aufzaehlungszeichen glaetten
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("•", "–").strip()


def ask(prompt, max_tokens=500, images=()):
    """Eine Frage, eine Antwort. {"text": ...} oder {"error": ...}.
    images: [(media_type, base64)] – werden vor den Text gestellt."""
    if not available():
        return {"error": "Kein KI-Schlüssel hinterlegt (ANTHROPIC_API_KEY in .env)."}
    body = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", MODEL_DEFAULT),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            *({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
              for mt, data in images),
            {"type": "text", "text": prompt},
        ]}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60 if images else 45) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        log.warning("KI-Anfrage fehlgeschlagen: HTTP %s", e.code)
        if e.code in (401, 403, 404):
            return {"error": "Die KI hat die Anfrage abgelehnt (Schlüssel oder Modell prüfen)."}
        if e.code == 400:
            return {"error": "Die KI hat die Anfrage abgelehnt – eventuell ist das Guthaben aufgebraucht."}
        return {"error": f"KI gerade nicht erreichbar (HTTP {e.code})."}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("KI nicht erreichbar: %s", e)
        return {"error": "KI gerade nicht erreichbar – der Pi hat vermutlich kein Internet."}
    text = "".join(part.get("text", "") for part in data.get("content", [])
                   if part.get("type") == "text")
    text = _clean(text)
    return {"text": text} if text else {"error": "Die KI hat keine Antwort geliefert."}


# --------------------------------------------------------- Plan verfeinern
def _json_block(text):
    """Das erste {...} aus der Antwort – Modelle schreiben gern Text drumherum."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("keine JSON-Antwort")
    return json.loads(text[start:end + 1])


def validate_plan(candidate, original, catalog):
    """Prueft einen Plan streng gegen den Katalog und die Struktur des Originals.
    Gibt den bereinigten Plan zurueck oder wirft ValueError."""
    by_id = {e["id"]: e for e in catalog}
    days = candidate.get("days")
    if not isinstance(days, list) or len(days) != len(original["days"]):
        raise ValueError("andere Anzahl an Trainingstagen")
    out_days = []
    for got, orig in zip(days, original["days"]):
        if not isinstance(got, dict) or got.get("label") != orig["label"]:
            raise ValueError("Trainingstage vertauscht oder umbenannt")
        items = got.get("exercises")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_EXERCISES_PER_DAY:
            raise ValueError(f"„{orig['label']}“: unpassende Zahl an Übungen")
        seen, clean = set(), []
        for it in items:
            try:
                ex = by_id[int(it["exercise_id"])]
                sets = int(it["target_sets"])
                lo, hi = int(it["target_reps_min"]), int(it["target_reps_max"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("unbekannte Übung oder fehlende Werte") from None
            if ex["id"] in seen:
                raise ValueError(f"„{ex['name']}“ doppelt an einem Tag")
            if not (1 <= sets <= 6 and 1 <= lo <= hi <= 30):
                raise ValueError(f"„{ex['name']}“: Sätze/Wiederholungen außerhalb des Rahmens")
            seen.add(ex["id"])
            clean.append({"exercise_id": ex["id"], "name": ex["name"], "pattern": ex["pattern"],
                          "focus": bool(it.get("focus")),
                          "muscle_group": ex["muscle_group"], "unit": ex["unit"],
                          "target_sets": sets, "target_reps_min": lo, "target_reps_max": hi})
        out_days.append({"label": orig["label"], "weekdays": list(orig["weekdays"]),
                         "exercises": clean})
    notes = candidate.get("notes") or []
    if not isinstance(notes, list):
        notes = []
    notes = [_clean(str(n))[:200] for n in notes if str(n).strip()][:3]
    return {**original, "days": out_days, "notes": notes}


def _profile_text(profile, preview):
    from datetime import date
    parts = []
    if profile.get("birth_year"):
        parts.append(f"Alter: etwa {date.today().year - int(profile['birth_year'])} Jahre")
    if profile.get("sex"):
        parts.append(f"Geschlecht: {profile['sex']}")
    if profile.get("height_cm"):
        parts.append(f"Größe: {profile['height_cm']:g} cm")
    weight = db.one("SELECT kg FROM bodyweight WHERE profile_id=? ORDER BY date DESC LIMIT 1",
                    (profile["id"],))
    if weight:
        parts.append(f"Gewicht: {weight['kg']:g} kg")
    parts.append(f"Ziel: {preview['goal']}")
    parts.append(f"Erfahrung: {preview['experience']}")
    parts.append(f"Dauer pro Einheit: {preview['minutes']} Minuten")
    if preview.get("focus"):
        parts.append("Muskel-Fokus (mehr Volumen gewünscht): " + ", ".join(preview["focus"]))
    if profile.get("limitations"):
        parts.append(f"Einschränkungen/Verletzungen: {profile['limitations'][:300]}")
    return "\n".join(parts)


def refine_plan(profile, preview, catalog, feedback=None):
    """KI passt den Regelplan an. Ergebnis: {"preview": plan, "error"?: str}.
    Bei jedem Problem bleibt der Regelplan unveraendert."""
    if not available():
        return {"preview": preview, "error": "Kein KI-Schlüssel hinterlegt."}
    compact = {"days": [{"label": d["label"],
                         "exercises": [{k: e[k] for k in ("exercise_id", "target_sets",
                                                          "target_reps_min", "target_reps_max")}
                                       for e in d["exercises"]]}
                        for d in preview["days"]]}
    lines = [f"{e['id']} | {e['name']} | {GROUP_LABEL.get(e['muscle_group'], '?')} | {e['pattern']}"
             for e in catalog]
    prompt = (
        "Du bist Trainer im Fitnessstudio und verfeinerst einen automatisch erstellten "
        "Krafttrainingsplan für diese Person:\n"
        f"{_profile_text(profile, preview)}\n\n"
        "Aktueller Plan (JSON):\n"
        f"{json.dumps(compact, ensure_ascii=False)}\n\n"
        "Erlaubte Übungen (id | Name | Muskelgruppe | Bewegungsmuster):\n"
        + "\n".join(lines) +
        "\n\nRegeln:\n"
        "- Nutze ausschließlich Übungen aus der Liste, über ihre id.\n"
        "- Behalte Anzahl, Reihenfolge und Namen (label) der Trainingstage genau bei.\n"
        f"- Pro Tag 1 bis {MAX_EXERCISES_PER_DAY} Übungen, keine Übung doppelt am selben Tag; "
        "die Übungszahl soll zur Dauer passen.\n"
        "- target_sets 1–6, target_reps_min ≤ target_reps_max, beides 1–30.\n"
        "- Berücksichtige Einschränkungen: ersetze belastende Übungen durch schonendere.\n"
        "- Halte Drücken und Ziehen (Brust/Schultern vs. Rücken) etwa im Gleichgewicht; "
        "tausche eher innerhalb desselben Bewegungsmusters als Muster zu streichen.\n"
        "- Streiche keine vorhandene Rumpfübung und keine Übung für die Beinrückseite.\n"
        "- Wenn ein Muskel-Fokus angegeben ist, behalte dessen zusätzliches Volumen bei.\n"
        "- Ändere nur, was die Person wirklich weiterbringt; ein guter Plan darf so bleiben.\n"
        "Antworte NUR mit JSON in genau dieser Form, ohne Text davor oder danach:\n"
        '{"days":[{"label":"...","exercises":[{"exercise_id":1,"target_sets":3,'
        '"target_reps_min":8,"target_reps_max":12}]}],'
        '"notes":["höchstens 3 kurze deutsche Sätze: was geändert wurde und warum"]}'
    )
    if feedback:
        prompt += ("\n\nDein letzter Vorschlag hatte diese Lücken, die der Ausgangsplan nicht hatte – "
                   "vermeide sie diesmal:\n" + "\n".join(f"- {f}" for f in feedback))
    res = ask(prompt, max_tokens=2000)
    if "error" in res:
        return {"preview": preview, "error": res["error"]}
    try:
        plan = validate_plan(_json_block(res["text"]), preview, catalog)
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("KI-Plan verworfen: %s", e)
        return {"preview": preview,
                "error": f"Der KI-Vorschlag war nicht verwendbar ({e}) – der Regelplan bleibt."}
    return {"preview": plan}


# ------------------------------------------------------ Koerperanalyse lesen
MEASUREMENT_FIELDS = {
    "weight_kg": "Körpergewicht in kg",
    "body_fat_pct": "Körperfett in Prozent",
    "muscle_mass_kg": "Muskelmasse in kg (Skelettmuskelmasse, falls beides angegeben)",
    "water_pct": "Körperwasser in Prozent (bei Angabe in Litern: null)",
    "visceral_fat": "viszerales Fett (Level/Index-Zahl)",
    "bmr_kcal": "Grundumsatz in kcal",
    "bio_age": "biologisches bzw. metabolisches Alter in Jahren",
    "waist_cm": "Taillenumfang in cm", "hip_cm": "Hüftumfang in cm",
    "chest_cm": "Brustumfang in cm", "arm_cm": "Oberarmumfang in cm",
    "thigh_cm": "Oberschenkelumfang in cm", "neck_cm": "Halsumfang in cm",
}


def read_measurement(image_b64, media_type):
    """Messwerte aus einem Screenshot (z. B. ViCoach / mescan) lesen.
    Ergebnis: {"values": {...}, "date": "YYYY-MM-DD"|None, "dropped": [...]} oder {"error": ...}.
    Das Bild wird nirgends gespeichert."""
    from . import body  # hier importiert, um Kreis-Importe zu vermeiden
    fields = "\n".join(f'- "{k}": {v}' for k, v in MEASUREMENT_FIELDS.items())
    prompt = (
        "Das Bild ist ein Screenshot einer Körperanalyse aus einem Fitnessstudio "
        "(z. B. vicoach, mescan, bodyscan, InBody). Lies die Messwerte ab.\n"
        "Antworte NUR mit JSON, ohne Text davor oder danach, in dieser Form:\n"
        '{"date": "YYYY-MM-DD oder null", "values": {<Feld>: Zahl oder null}, '
        '"not_a_measurement": false}\n'
        f"Felder:\n{fields}\n"
        "Regeln: Nur Werte eintragen, die eindeutig im Bild stehen – nichts schätzen oder "
        "berechnen. Dezimalkomma als Punkt. Wenn das Bild keine Körperanalyse zeigt, "
        'setze "not_a_measurement": true.'
    )
    res = ask(prompt, max_tokens=600, images=[(media_type, image_b64)])
    if "error" in res:
        return res
    try:
        data = _json_block(res["text"])
    except (ValueError, json.JSONDecodeError):
        return {"error": "Die Werte konnten nicht gelesen werden – bitte von Hand eintragen."}
    if data.get("not_a_measurement"):
        return {"error": "Auf dem Bild ist keine Körperanalyse zu erkennen."}
    values, dropped = body.clean(data.get("values") or {})
    if not values:
        return {"error": "Auf dem Bild wurden keine Messwerte gefunden."}
    day = data.get("date")
    try:
        from datetime import date as _date
        day = _date.fromisoformat(day).isoformat() if day else None
        if day and day > _date.today().isoformat():
            day = None
    except (TypeError, ValueError):
        day = None
    return {"values": values, "date": day, "dropped": dropped}
