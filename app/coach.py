"""Taeglicher Coach: sammelt Plan und letzte Trainings eines Profils, laesst Claude kurze
Verbesserungsvorschlaege formulieren und legt sie als Hinweise ab.

Der Coach aendert nie den Plan. Claude bekommt keine Werkzeuge und liefert nur Text,
dieses Modul schreibt ausschliesslich in coach_notes. Aufgerufen von scripts/coach_daily.py."""
import json
import re
from datetime import timedelta

from . import analysis, db, stats

CATEGORIES = ("plan", "progress")
MAX_NOTES = 3
MAX_TITLE = 60
MAX_TEXT = 200
EXPIRE_DAYS = 14          # offene Hinweise, die so lange niemand wegklickt, verschwinden
HISTORY_WEEKS = 4

PROMPT = """Du bist Trainingscoach in einer persönlichen Fitness-App. Unten stehen der aktuelle \
Trainingsplan, der Regel-Check der App und die Trainings der letzten {weeks} Wochen als JSON.

Formuliere höchstens {max_notes} kurze Verbesserungsvorschläge auf Deutsch, die die Person \
selbst umsetzen kann. Du änderst nichts, du schlägst nur vor.

Regeln:
- Kategorie "plan" für den Aufbau (Übungsauswahl, Sätze, Wiederholungsbereiche, Verteilung), \
"progress" für den Verlauf (Stagnation, ausgelassene Einheiten, fällige Steigerungen).
- Halte dich an die Regeln der App: Wochensätze je Muskel nie über MRV, Entlastung nur bei \
Ermüdungssignalen, Anfänger steigern linear, sonst doppelte Progression mit RPE.
- Wiederhole nichts aus "offene_hinweise" und nichts, was der Regel-Check schon meldet.
- Titel höchstens {max_title} Zeichen, Text höchstens {max_text} Zeichen, konkret mit \
Übungsnamen und Zahlen.
- Nur Vorschläge, die aus den Daten folgen. Gibt es nichts Sinnvolles, antworte mit [].
- "exercise" ist der exakte Übungsname aus dem Plan oder null.

Antworte NUR mit einem JSON-Array, ohne Text davor oder danach:
[{{"category": "plan", "title": "...", "text": "...", "exercise": "..."}}]

DATEN:
{data}"""


def _recent_workouts(profile_id, weeks=HISTORY_WEEKS):
    since = (stats.today() - timedelta(weeks=weeks)).isoformat()
    out = []
    for w in db.list_workouts(profile_id, limit=60):
        at = (w.get("started_at") or w.get("finished_at") or "")[:10]
        if at < since:
            continue
        per_ex = {}
        for s in w["sets"]:
            if s.get("warmup"):
                continue
            per_ex.setdefault(s["exercise"], []).append(
                f"{s['weight'] or 0:g}x{s['reps'] or 0}" + (f"@{s['rpe']:g}" if s.get("rpe") else ""))
        out.append({"datum": at, "tag": w.get("day_label"), "saetze": per_ex})
    return out


def context(profile_id):
    """Alles, was Claude sehen soll – ohne Namen oder Körperdaten der Person."""
    split = db.active_split(profile_id)
    if not split or not split["days"]:
        return None
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    check = analysis.analyze(profile_id)
    return {
        "profil": {k: profile.get(k) for k in ("goal", "experience", "days_per_week", "session_minutes", "focus")},
        "plan": {"name": split["name"], "tage": [
            {"tag": d["label"], "wochentage": d.get("weekdays"),
             "uebungen": [{"name": e["name"], "muskel": e["muscle_group"], "saetze": e["target_sets"],
                           "wdh": [e["target_reps_min"], e["target_reps_max"]]} for e in d["exercises"]]}
            for d in split["days"]]},
        "regel_check": {
            "volumen": [{k: v[k] for k in ("label", "actual", "target", "peak")} for v in check["volume"]],
            "meldungen": [s["text"] for s in check["suggestions"]],
        },
        "faellige_steigerungen": stats.ready_to_raise(profile_id),
        "trainings": _recent_workouts(profile_id),
        "einheiten_pro_woche": [w["count"] for w in stats.frequency(profile_id, weeks=HISTORY_WEEKS)["weeks"]],
        "offene_hinweise": [n["title"] for n in open_notes(profile_id)],
    }


def prompt(ctx):
    return PROMPT.format(weeks=HISTORY_WEEKS, max_notes=MAX_NOTES, max_title=MAX_TITLE,
                         max_text=MAX_TEXT, data=json.dumps(ctx, ensure_ascii=False, default=str))


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse(raw, profile_id):
    """Claude-Antwort in gepruefte Hinweise verwandeln. Alles Unbrauchbare fliegt raus,
    statt den Lauf scheitern zu lassen. Wirft ValueError nur, wenn gar kein JSON da ist."""
    match = re.search(r"\[.*\]", raw or "", re.S)
    if not match:
        raise ValueError("Keine JSON-Liste in der Antwort")
    items = json.loads(match.group(0))
    if not isinstance(items, list):
        raise ValueError("Antwort ist keine Liste")
    names = {r["name"].casefold(): r["exercise_id"] for r in db.rows(
        "SELECT DISTINCT e.name, e.id AS exercise_id FROM split_exercises se"
        " JOIN exercises e ON e.id = se.exercise_id JOIN split_days d ON d.id = se.split_day_id"
        " JOIN splits s ON s.id = d.split_id WHERE s.profile_id=?", (profile_id,))}
    seen = {n["title"].casefold() for n in open_notes(profile_id)}
    out = []
    for item in items:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
            continue
        title, text = _clip(item.get("title"), MAX_TITLE), _clip(item.get("text"), MAX_TEXT)
        if not title or not text or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        ex = item.get("exercise")
        out.append({"category": item["category"], "title": title, "text": text,
                    "exercise_id": names.get(ex.casefold()) if isinstance(ex, str) else None})
        if len(out) == MAX_NOTES:
            break
    return out


def store(profile_id, notes):
    day = stats.today().isoformat()
    with db.connect() as c:
        for n in notes:
            c.execute("INSERT INTO coach_notes (profile_id, date, category, title, text, exercise_id)"
                      " VALUES (?,?,?,?,?,?)",
                      (profile_id, day, n["category"], n["title"], n["text"], n["exercise_id"]))


def expire(profile_id):
    cutoff = (stats.today() - timedelta(days=EXPIRE_DAYS)).isoformat()
    with db.connect() as c:
        c.execute("UPDATE coach_notes SET status='dismissed'"
                  " WHERE profile_id=? AND status='open' AND date < ?", (profile_id, cutoff))


def open_notes(profile_id):
    return db.rows("SELECT n.id, n.date, n.category, n.title, n.text, n.exercise_id, e.name AS exercise"
                   " FROM coach_notes n LEFT JOIN exercises e ON e.id = n.exercise_id"
                   " WHERE n.profile_id=? AND n.status='open' ORDER BY n.date DESC, n.id", (profile_id,))


def dismiss(note_id):
    with db.connect() as c:
        c.execute("UPDATE coach_notes SET status='dismissed' WHERE id=?", (note_id,))
