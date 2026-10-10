"""Taeglicher Coach: sammelt Plan, Block und letzte Trainings eines Profils, laesst Claude als
Personal Trainer kurze Hinweise formulieren und legt sie in der App ab.

Der Coach aendert nie den Plan. Claude bekommt keine Werkzeuge und liefert nur Text,
dieses Modul schreibt ausschliesslich in coach_notes. Aufgerufen von scripts/coach_daily.py."""
import json
import re
from datetime import date, timedelta

from . import analysis, db, stats
from .planner import blocks, explain

CATEGORIES = ("plan", "progress")
MAX_NOTES = 3
MAX_TITLE = 60
MAX_TEXT = 220
EXPIRE_DAYS = 14          # offene Hinweise, die so lange niemand wegklickt, verschwinden
HISTORY_WEEKS = 6
CHECKIN_DAYS = 14
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

SYSTEM = """Du bist der persönliche Fitness-Coach dieser Person, ein erfahrener Personal Trainer \
mit Hintergrund in evidenzbasiertem Krafttraining (Renaissance Periodization, Stronger by Science). \
Du begleitest sie jeden Tag über ihre Trainings-App und hinterlässt kurze Nachrichten, so wie ein \
guter Trainer seinem Klienten zwischen zwei Terminen schreibt: auf Deutsch, per Du, direkt, warm \
und ehrlich. Du kennst ihre Ziele, ihren Plan und alles, was du ihr schon gesagt hast."""

PROMPT = """<aufgabe>
Schau dir an, was seit deiner letzten Nachricht passiert ist, und schreib höchstens {max_notes} \
Hinweise, die jetzt für den Fortschritt am meisten bringen. Ein echter Coach:
- erkennt Erfolge und sagt es, wenn etwas gut läuft (neue Bestleistung, volle Trainingswoche, \
  sauber gesteigert). Lob nur mit Beleg aus den Daten, höchstens ein Lob pro Tag.
- spricht Probleme offen an (ausgelassene Einheiten, Sätze weit vor dem Versagen, lange Pausen), \
  ohne zu schimpfen.
- erklärt in einem Halbsatz, warum etwas wichtig ist, und gibt genau eine klare nächste Handlung.
- passt sich der Lage an: am Blockanfang Routine aufbauen, mitten im Block dranbleiben und sauber \
  steigern, vor der Entlastung auf Erholung achten.
- nervt nicht: Gab es seit deinen offenen Hinweisen keine neue Einheit und nichts Neues, schreib \
  lieber nichts als dasselbe anders formuliert.
Du änderst nichts am Plan, die Person setzt deine Vorschläge selbst um.
</aufgabe>

<was_die_app_schon_selbst_macht>
Schlag NICHTS davon vor, das erledigt die App automatisch:
- Das Volumen steigt innerhalb des Blocks von Woche zu Woche (MEV → MAV, Fokus Richtung MRV). Die \
  Sätze dieser Woche stehen in "plan.tage[].uebungen[].saetze_diese_woche".
- Gewicht und Wiederholungen: Anfänger linear, sonst doppelte Progression mit RPE; Plateau, \
  Rücksetzen, Wiedereinstieg nach Pausen (> 14 Tage 93 %, > 28 Tage 87 %).
- Entlastung nur bei zwei Ermüdungssignalen, sonst am Blockende.
- Wochen-Check: verpasste Sätze nachholen (höchstens +20 %, nie über MRV), Varianten bei Stillstand \
  tauschen. Was die App schon geändert hat, steht in "app_aenderungen".
- Alles in "regel_check.meldungen" sieht die Person bereits.
</was_die_app_schon_selbst_macht>

<worauf_du_achtest>
Nach Wirkung sortiert:
1. Regelmäßigkeit: Einheiten seit Blockstart gegen "block.einheiten_pro_woche_geplant", \
   "tage_seit_letzter_einheit", Trainingstage, die nie stattfinden. Bewerte nur die Zeit seit \
   "block.start", davor galt ein anderer Plan.
2. Ausführung laut Daten: Sätze mit RPE ≤ 6 oder unter dem Wiederholungsbereich, stark \
   schwankende Gewichte, Bestleistungen.
3. Planaufbau: Fokus-Muskel ohne passende Übung, Beschwerden aus "profil.beschwerden", gleiche \
   Muskeln an aufeinanderfolgenden Tagen, Einheit zu lang für "profil.session_minutes".
4. Erholung: mehrere Check-ins mit schlechtem Schlaf, hohem Stress oder starkem Muskelkater.
In Blockwoche 1 oder bei weniger als 3 Einheiten sind Trends nicht belastbar: dann keine Aussagen \
über Stillstand, sondern Routine, Ausführung oder Lob für den Start.
</worauf_du_achtest>

<regeln>
- Jede Aussage folgt aus den Daten und nennt den Beleg (Datum, Gewicht x Wdh. @ RPE, Anzahl \
  Einheiten). Nichts erfinden, keine allgemeinen Tipps ohne Datenbezug.
- Ein Hinweis = eine Sache. Grenzen der App gelten auch für dich (nie über MRV, keine Entlastung \
  ohne Signale). Keine medizinischen Ratschläge; bei Schmerzen an Arzt oder Physio verweisen.
- Nichts wiederholen aus "offene_hinweise" oder "weggeklickt" (das wollte die Person nicht).
- Ist ein offener Hinweis erledigt oder überholt, nimm seine id in "erledigt" auf.
- category: "plan" (Aufbau, Übungen, Tage) oder "progress" (Verlauf, Ausführung, Regelmäßigkeit, \
  Erholung, Lob).
- title höchstens {max_title} Zeichen, text höchstens {max_text} Zeichen.
- exercise: exakter Übungsname aus dem Plan oder null.
</regeln>

<beispiele>
Gut (Lob): {{"category": "progress", "title": "Starker Start in den Block", "text": "Do und Fr \
direkt beide Einheiten durchgezogen, genau so baut sich die Routine auf. Halt den Rhythmus: \
nächste Einheit OK B.", "exercise": null}}
Gut (Ausführung): {{"category": "progress", "title": "Rudern Kabel: letzten Satz ausreizen", \
"text": "Am 09.10. endete Satz 3 bei 60x6 @ RPE 6, da war noch viel Luft. Bleib bei 60 kg und geh \
bis RPE 8–9, nur so erkennt die App, wann sie steigern darf.", "exercise": "Rudern Kabel"}}
Schlecht: "Mehr Sätze für Waden" (macht der Block selbst), "Achte auf deine Ernährung" (kein \
Datenbezug), "Super gemacht!" (Lob ohne Beleg).
</beispiele>

<ausgabe>
Denk zuerst kurz in <analyse>…</analyse> nach (höchstens 8 Stichpunkte). Gib dann genau einen Block aus:
<hinweise>{{"notes": [{{"category": "...", "title": "...", "text": "...", "exercise": null}}], "erledigt": []}}</hinweise>
Keine Hinweise ist eine gültige Antwort: {{"notes": [], "erledigt": []}}
</ausgabe>

<daten>
{data}
</daten>"""


def _date(iso):
    return (iso or "")[:10]


def _weekday(iso):
    try:
        return WEEKDAYS[date.fromisoformat(_date(iso)).weekday()]
    except ValueError:
        return None


def _workouts(profile_id, since):
    out = []
    for w in reversed(db.list_workouts(profile_id, limit=80)):
        at = w.get("started_at") or w.get("finished_at")
        if _date(at) < since.isoformat():
            continue
        per_ex = {}
        for s in w["sets"]:
            if s.get("warmup"):
                continue
            per_ex.setdefault(s["exercise"], []).append(
                f"{s['weight'] or 0:g}x{s['reps'] or 0}" + (f"@{s['rpe']:g}" if s.get("rpe") else ""))
        out.append({"datum": _date(at), "wochentag": _weekday(at), "tag": w.get("day_label"),
                    "saetze": per_ex})
    return out


def _checkins(profile_id, since):
    rows = db.rows("SELECT date, sleep, energy, stress, soreness, readiness FROM checkins"
                   " WHERE profile_id=? AND date >= ? ORDER BY date", (profile_id, since.isoformat()))
    for r in rows:
        try:
            r["soreness"] = {k: v for k, v in json.loads(r["soreness"] or "{}").items() if v}
        except ValueError:
            r["soreness"] = {}
    return rows


def context(profile_id):
    """Alles, was der Coach sehen soll – ohne Namen und Koerperdaten der Person."""
    split = db.active_split(profile_id)
    if not split or not split["days"]:
        return None
    today = stats.today()
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    check = analysis.analyze(profile_id)
    block = blocks.active(profile_id)
    st = blocks.state(block) if block else None
    start = date.fromisoformat(block["start_date"]) if block else today - timedelta(weeks=HISTORY_WEEKS)
    workouts = _workouts(profile_id, min(start, today - timedelta(weeks=HISTORY_WEEKS)))
    since_start = [w for w in workouts if w["datum"] >= start.isoformat()]
    last = workouts[-1]["datum"] if workouts else None
    notes = db.rows("SELECT id, date, status, title, text FROM coach_notes WHERE profile_id=?"
                    " AND (status='open' OR (status='dismissed' AND date >= ?)) ORDER BY date",
                    (profile_id, (today - timedelta(days=30)).isoformat()))
    return {
        "heute": f"{WEEKDAYS[today.weekday()]} {today.isoformat()}",
        "profil": {"ziel": profile.get("goal"), "erfahrung": profile.get("experience"),
                   "fokus": profile.get("focus"), "beschwerden": profile.get("limitations"),
                   "session_minutes": profile.get("session_minutes")},
        "block": {"start": start.isoformat(), "woche": st["week"], "wochen": st["weeks"],
                  "entlastung": st["deload"], "rir_ziel": st["rir"],
                  "einheiten_pro_woche_geplant": check.get("sessions"),
                  "einheiten_seit_start": len(since_start)} if st else None,
        "plan": {"name": split["name"], "tage": [
            {"tag": d["label"], "wochentage": [WEEKDAYS[i] for i in (d.get("weekdays") or [])],
             "uebungen": [{"name": e["name"], "muskel": e["muscle_group"],
                           "saetze_diese_woche": blocks.sets_for(block, e) if block else e["target_sets"],
                           "wdh": [e["target_reps_min"], e["target_reps_max"]]} for e in d["exercises"]]}
            for d in split["days"]]},
        "regel_check": {
            "volumen": [{k: v[k] for k in ("label", "actual", "target", "peak")} for v in check["volume"]],
            "meldungen": [s["text"] for s in check["suggestions"]],
        },
        "app_aenderungen": explain.recent(profile_id, 10),
        "faellige_steigerungen": stats.ready_to_raise(profile_id),
        "trainings": workouts,
        "tage_seit_letzter_einheit": (today - date.fromisoformat(last)).days if last else None,
        "checkins": _checkins(profile_id, today - timedelta(days=CHECKIN_DAYS)),
        "offene_hinweise": [{k: n[k] for k in ("id", "date", "title", "text")}
                            for n in notes if n["status"] == "open"],
        "weggeklickt": [n["title"] for n in notes if n["status"] == "dismissed"],
    }


def prompt(ctx):
    return PROMPT.format(max_notes=MAX_NOTES, max_title=MAX_TITLE, max_text=MAX_TEXT,
                         data=json.dumps(ctx, ensure_ascii=False, default=str))


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _payload(raw):
    """JSON aus dem <hinweise>-Block holen; zur Not das erste Objekt bzw. die erste Liste."""
    raw = raw or ""
    tag = re.search(r"<hinweise>(.*?)</hinweise>", raw, re.S)
    body = tag.group(1) if tag else raw
    match = re.search(r"\{.*\}|\[.*\]", body, re.S)
    if not match:
        raise ValueError("Kein JSON in der Antwort")
    data = json.loads(match.group(0))
    if isinstance(data, list):
        return {"notes": data, "erledigt": []}
    if not isinstance(data, dict) or not isinstance(data.get("notes", []), list):
        raise ValueError("Antwort hat kein notes-Feld")
    return data


def parse(raw, profile_id):
    """Claude-Antwort pruefen: {"notes": [...], "resolved": [ids]}. Unbrauchbares fliegt raus,
    statt den Lauf scheitern zu lassen. ValueError nur, wenn gar kein JSON da ist."""
    data = _payload(raw)
    names = {r["name"].casefold(): r["exercise_id"] for r in db.rows(
        "SELECT DISTINCT e.name, e.id AS exercise_id FROM split_exercises se"
        " JOIN exercises e ON e.id = se.exercise_id JOIN split_days d ON d.id = se.split_day_id"
        " JOIN splits s ON s.id = d.split_id WHERE s.profile_id=?", (profile_id,))}
    open_ids = {n["id"] for n in open_notes(profile_id)}
    resolved = {i for i in data.get("erledigt") or [] if isinstance(i, int) and i in open_ids}
    seen = {n["title"].casefold() for n in open_notes(profile_id) if n["id"] not in resolved}
    out = []
    for item in data.get("notes") or []:
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
    return {"notes": out, "resolved": sorted(resolved)}


def store(profile_id, notes):
    day = stats.today().isoformat()
    with db.connect() as c:
        for n in notes:
            c.execute("INSERT INTO coach_notes (profile_id, date, category, title, text, exercise_id)"
                      " VALUES (?,?,?,?,?,?)",
                      (profile_id, day, n["category"], n["title"], n["text"], n["exercise_id"]))


def resolve(profile_id, note_ids):
    """Vom Coach selbst als erledigt markiert – zaehlt nicht als 'weggeklickt'."""
    with db.connect() as c:
        for i in note_ids:
            c.execute("UPDATE coach_notes SET status='resolved' WHERE id=? AND profile_id=? AND status='open'",
                      (i, profile_id))


def expire(profile_id):
    cutoff = (stats.today() - timedelta(days=EXPIRE_DAYS)).isoformat()
    with db.connect() as c:
        c.execute("UPDATE coach_notes SET status='expired'"
                  " WHERE profile_id=? AND status='open' AND date < ?", (profile_id, cutoff))


def open_notes(profile_id):
    return db.rows("SELECT n.id, n.date, n.category, n.title, n.text, n.exercise_id, e.name AS exercise"
                   " FROM coach_notes n LEFT JOIN exercises e ON e.id = n.exercise_id"
                   " WHERE n.profile_id=? AND n.status='open' ORDER BY n.date DESC, n.id", (profile_id,))


def dismiss(note_id):
    with db.connect() as c:
        c.execute("UPDATE coach_notes SET status='dismissed' WHERE id=?", (note_id,))
