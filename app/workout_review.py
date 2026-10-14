"""Coach-Rückmeldung nach dem Training: `session_review.facts()` rechnet, Claude schreibt daraus,
was ein Personal Trainer direkt nach der Einheit sagen würde.

Ablauf: Beim Speichern einer Einheit wird die Rückmeldung `pending` und läuft im Hintergrund
(FastAPI-BackgroundTask). Die Zahlen sind sofort da, der Text kommt nach. Ein Lock sorgt dafür,
dass auf dem Pi nie zwei `claude`-Prozesse gleichzeitig laufen. Wird die Einheit korrigiert,
startet die Rückmeldung neu. Bleibt sie zu lange `pending` (Neustart mitten im Lauf), stößt der
nächste Abruf sie wieder an.

Claude ändert nichts: keine Werkzeuge, nur Text, gespeichert in `workout_reviews`."""
import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from . import claude_cli, coach, db, session_review, stats
from .planner import blocks

log = logging.getLogger(__name__)
MODEL = os.environ.get("REVIEW_MODEL", "claude-sonnet-5-5")
TIMEOUT = 120
ATTEMPTS = 2
RETRY_PAUSE = 10
STALE_MINUTES = 10        # länger pending heisst: der Lauf ist gestorben (Neustart o. ä.)
RECENT_SESSIONS = 3
LIMITS = {"kurzurteil": 180, "item": 300}       # Prompt nennt etwas weniger, damit nichts abreißt
PROMPT_LIMITS = {"kurzurteil": 150, "item": 240}
MAX_ITEMS = {"stark": 2, "vergleiche": 4, "naechstes_mal": 2}
WEEKDAYS = coach.WEEKDAYS
LOCK = threading.Lock()

PROMPT = """<aufgabe>
Dein Klient hat gerade eine Einheit beendet und schaut aufs Handy. Schreib ihm, was du als sein \
Personal Trainer direkt nach dem Training sagen würdest: ehrlich, konkret, mit Zahlen aus den \
Daten. Kein Notenzettel, kein Schulterklopfen ohne Grund.
</aufgabe>

<so_liest_du_die_daten>
- "einheit.exercises": was gemacht wurde, in der Reihenfolge der Einheit ("position").
- "substitute_for": Diese Übung wurde statt der geplanten gemacht. Das ist KEIN Auslassen. Sag, \
  ob der Ersatz denselben Muskel ähnlich trifft oder ob etwas fehlt (z. B. Rudern statt Latzug \
  trifft den Rücken, aber anders: horizontal statt vertikal).
- "compare": letzte Einheit mit derselben Übung. "delta_pct" ist die Änderung des geschätzten \
  Maximalgewichts (e1RM). "slot" sagt, wo die Übung heute im Vergleich zu damals stand: \
  "spaeter" = weiter hinten, also mit Ermüdung aus den Übungen davor (weniger Wiederholungen \
  sind normal; trotzdem stärker ist besonders gut). "frueher" = weiter vorne, also frischer \
  (ein Teil der Steigerung kommt daher, das ehrlich einordnen). "order_note": true heißt, \
  dass ein kleiner Rückgang allein durch die spätere Position erklärt ist.
- "pr": neue Bestleistung über alle früheren Einheiten.
- "einheit.muscles": Sätze je Muskel heute gegen den Plan für heute. "woche": Sätze je Muskel \
  in dieser Woche bisher gegen das Wochenziel, dazu "noch_geplant" (die Trainingstage, die \
  diese Woche noch kommen). Daraus folgt, ob ein heute zu kurz gekommener Muskel noch drankommt.
- "einheit.hints" hat die App schon selbst erkannt. Greif das Wichtigste auf, aber schreib es \
  nicht ab: Ordne es ein wie ein Trainer.
- "letzte_einheiten_dieses_tags": dieselbe Einheit an früheren Terminen, für Trends.
- "notiz": was der Klient selbst zur Einheit geschrieben hat (Grund für Ersatz oder Abbruch). \
  Ernst nehmen.
</so_liest_du_die_daten>

<regeln>
- Jede Aussage hat einen Beleg aus den Daten (Gewicht×Wdh., Satzzahl, Datum, Position).
- Gewicht und Wiederholungen steigert die App selbst, schlag also nie "nächstes Mal 2,5 kg mehr" \
  vor. Die Satzzahl bleibt wie im Plan; verpasste Sätze holt die App nicht nach, schlag also auch \
  kein Nachholen mit Zusatzsätzen vor. Deine Hinweise betreffen, was der Klient selbst steuert: Übungsauswahl, Reihenfolge, \
  Aufwärmen, Anstrengung, Pausen, Fokusmuskeln nicht ans Ende legen, dranbleiben.
- War die Einheit kurz oder unvollständig, sag das klar, aber ohne Vorwurf, und sag, was es \
  für die Woche bedeutet.
- Keine medizinischen Ratschläge. Keine Wiederholung derselben Aussage in zwei Feldern.
- Deutsch, per Du, kurz. "kurzurteil" höchstens {kurz} Zeichen, jeder Listenpunkt höchstens \
  {item} Zeichen. Leere Listen sind erlaubt.
</regeln>

<ausgabe>
Genau ein Block, nichts davor oder danach:
<auswertung>{{"kurzurteil": "ein Satz: wie war die Einheit", \
"stark": ["höchstens 2: was richtig gut war, mit Beleg"], \
"vergleiche": ["höchstens 4: Übung für Übung gegen das letzte Mal, Ersatz und Reihenfolge eingeordnet"], \
"naechstes_mal": ["höchstens 2: genau eine Handlung je Punkt"]}}</auswertung>
</ausgabe>

<daten>
{data}
</daten>"""


# ------------------------------------------------------------------ Kontext
def _week(profile_id, workout, day):
    """Sätze je Muskel in der Woche der Einheit (bis einschliesslich dieser) gegen das
    Wochenziel des Plans, und welche Trainingstage diese Woche noch kommen."""
    split_day = db.get_row("split_days", workout["split_day_id"]) if workout.get("split_day_id") else None
    split = db.split_detail(split_day["split_id"]) if split_day else db.active_split(profile_id)
    if not split or not day:
        return None
    block = blocks.for_split(split["id"])
    target = {}
    for d in split["days"]:
        per_week = max(1, len(d.get("weekdays") or []))
        for e in d["exercises"]:
            g = session_review.group_of(e)
            sets = blocks.sets_for(block, e, day) if block else (e["target_sets"] or 0)
            target[g] = target.get(g, 0) + sets * per_week
    monday = day - timedelta(days=day.weekday())
    at, wid = workout.get("started_at") or workout.get("finished_at"), workout["id"]
    rows = db.rows(
        "SELECT e.pattern, e.muscle_group, COALESCE(w.started_at, w.finished_at) AS at, w.id AS wid"
        " FROM sets s JOIN workouts w ON w.id = s.workout_id JOIN exercises e ON e.id = s.exercise_id"
        " WHERE w.profile_id=? AND s.warmup=0 AND s.reps > 0"
        " AND COALESCE(w.started_at, w.finished_at) >= ?",
        # einen Tag Puffer: die Grenze liegt in Ortszeit, gespeichert ist UTC
        (profile_id, (monday - timedelta(days=1)).isoformat()))
    done = {}
    for r in rows:
        local = stats._local_date(r["at"])
        if not local or not monday <= local <= day or (r["at"], r["wid"]) > (at, wid):
            continue
        g = session_review.group_of(r)
        done[g] = done.get(g, 0) + 1
    groups = list(target) + [g for g in done if g not in target]
    later = [f"{d['label']} ({WEEKDAYS[i]})" for d in split["days"]
             for i in sorted(d.get("weekdays") or []) if i > day.weekday()]
    return {"saetze": [{"muskel": session_review.group_label(g), "bisher": done.get(g, 0),
                        "ziel": target.get(g, 0)} for g in groups],
            "noch_geplant": later}


def _recent_same_day(profile_id, workout):
    if not workout.get("split_day_id"):
        return []
    at = workout.get("started_at") or workout.get("finished_at")
    prev = db.rows("SELECT id FROM workouts WHERE profile_id=? AND split_day_id=? AND id<>?"
                   " AND COALESCE(started_at, finished_at) < ?"
                   " ORDER BY COALESCE(started_at, finished_at) DESC LIMIT ?",
                   (profile_id, workout["split_day_id"], workout["id"], at, RECENT_SESSIONS))
    out = []
    for p in prev:
        w = db.get_workout(p["id"])
        per_ex = {}
        for s in w["sets"]:
            if not s.get("warmup") and s.get("reps"):
                per_ex.setdefault(s["exercise"], []).append(session_review.set_text(s))
        local = stats._local_date(w.get("started_at") or w.get("finished_at"))
        out.append({"datum": local.isoformat() if local else None, "uebungen": per_ex})
    return out


def context(profile_id, workout, facts=None):
    facts = facts or session_review.facts(profile_id, workout)
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    day = stats._local_date(workout.get("started_at") or workout.get("finished_at"))
    plan = [{"name": e["name"], "saetze": e["sets"], "wdh": e["reps"]} for e in _plan_view(workout, day)]
    return {
        "datum": f"{WEEKDAYS[day.weekday()]} {day.isoformat()}" if day else None,
        "profil": {"ziel": profile.get("goal"), "erfahrung": profile.get("experience"),
                   "fokus": profile.get("focus"), "beschwerden": profile.get("limitations")},
        "trainingstag": workout.get("day_label"),
        "plan_heute": plan,
        "einheit": facts,
        "woche": _week(profile_id, workout, day),
        "letzte_einheiten_dieses_tags": _recent_same_day(profile_id, workout),
        "notiz": workout.get("notes"),
    }


def _plan_view(workout, day):
    items = session_review.plan_items(workout.get("split_day_id"))
    block = session_review.block_for_day(workout.get("split_day_id"))
    return [{"name": i["name"],
             "sets": blocks.sets_for(block, i, day) if block else i["target_sets"],
             "reps": [i["target_reps_min"], i["target_reps_max"]]} for i in items]


def prompt(ctx):
    return PROMPT.format(kurz=PROMPT_LIMITS["kurzurteil"], item=PROMPT_LIMITS["item"],
                         data=json.dumps(ctx, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ Antwort prüfen
def parse(raw):
    """<auswertung>{…}</auswertung> -> {"kurzurteil", "stark", "vergleiche", "naechstes_mal"}.
    Zu Langes wird gekürzt, Falsches fliegt raus. ValueError, wenn nichts Brauchbares kommt."""
    raw = raw or ""
    tag = re.search(r"<auswertung>(.*?)</auswertung>", raw, re.S)
    body = tag.group(1) if tag else raw
    match = re.search(r"\{.*\}", body, re.S)
    if not match:
        raise ValueError("Kein JSON in der Antwort")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Antwort ist kein Objekt")
    out = {"kurzurteil": coach._clip(data.get("kurzurteil"), LIMITS["kurzurteil"])}
    for key, n in MAX_ITEMS.items():
        items = data.get(key) if isinstance(data.get(key), list) else []
        out[key] = [t for t in (coach._clip(i, LIMITS["item"]) for i in items if isinstance(i, str)) if t][:n]
    if not out["kurzurteil"] and not any(out[k] for k in MAX_ITEMS):
        raise ValueError("Antwort ist leer")
    return out


# ------------------------------------------------------------------ Speichern und Ablauf
def _set(workout_id, status, body=None, model=None):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO workout_reviews (workout_id, status, body, model, updated_at)"
                  " VALUES (?,?,?,?,?)", (workout_id, status, body, model, db.now()))


def get(workout_id):
    row = db.one("SELECT * FROM workout_reviews WHERE workout_id=?", (workout_id,))
    if not row:
        return None
    out = {"status": row["status"], "updated_at": row["updated_at"], "body": None, "error": None}
    if row["status"] == "done":
        out["body"] = json.loads(row["body"])
    elif row["status"] == "error":
        out["error"] = row["body"]
    return out


def _stale(review):
    try:
        at = datetime.fromisoformat(review["updated_at"])
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - at > timedelta(minutes=STALE_MINUTES)


def run(profile_id, workout_id):
    """Rückmeldung schreiben lassen und speichern. Läuft im Hintergrund – jeder Fehler landet
    als `error` in der Tabelle statt im Nichts."""
    with LOCK:
        workout = db.get_workout(workout_id)
        if not workout:                       # inzwischen gelöscht
            return
        try:
            raw = claude_cli.ask_with_retry(prompt(context(profile_id, workout)), coach.SYSTEM,
                                            MODEL, TIMEOUT, ATTEMPTS, RETRY_PAUSE)
            body = parse(raw)
        except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("Coach-Rückmeldung für Einheit %s fehlgeschlagen: %s", workout_id, e)
            if db.exists("workouts", workout_id):
                _set(workout_id, "error", str(e)[:300], MODEL)
            return
        if db.exists("workouts", workout_id):
            _set(workout_id, "done", json.dumps(body, ensure_ascii=False), MODEL)


def schedule(profile_id, workout_id, background):
    """Als pending markieren und den Lauf anstoßen. Ohne BackgroundTasks (direkter Aufruf, Tests)
    bleibt es bei pending; der nächste Abruf über die API holt es nach."""
    _set(workout_id, "pending")
    if background is not None:
        background.add_task(run, profile_id, workout_id)


def for_api(profile_id, workout, background=None):
    """{facts, coach} für die Antwort. Fehlt der Text oder ist ein Lauf hängen geblieben,
    wird er angestoßen – so bekommen auch ältere Einheiten beim Öffnen ihren Text."""
    review = get(workout["id"])
    if background is not None and (review is None or (review["status"] == "pending" and _stale(review))):
        schedule(profile_id, workout["id"], background)
        review = get(workout["id"])
    return {"facts": session_review.facts(profile_id, workout), "coach": review}
