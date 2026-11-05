"""Taeglicher Coach: sammelt Plan, Block und letzte Trainings eines Profils und laesst Claude
als Personal Trainer hoechstens eine Nachricht in den Coach-Chat schreiben – nur wenn seit
seiner letzten etwas Neues passiert ist. Darauf kann die Person direkt antworten (coach_chat).

Der Coach aendert nie den Plan. Claude bekommt keine Werkzeuge und liefert nur Text,
gespeichert wird nur in coach_messages. Aufgerufen von scripts/coach_daily.py."""
import json
import re
from datetime import date, timedelta

from . import analysis, coach_patterns, db, stats
from .planner import blocks, explain

MAX_TEXT = 600
MAX_IMPULSE = 160
RECENT_IMPULSES = 14
DISMISSED_DAYS = 30
MIN_NUTRITION_DAYS = 4
RECENT_MESSAGES = 10
HISTORY_WEEKS = 6
CHECKIN_DAYS = 14
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

SYSTEM = """Du bist der persönliche Fitness-Coach dieser Person, ein erfahrener Personal Trainer \
mit Hintergrund in evidenzbasiertem Krafttraining (Renaissance Periodization, Stronger by Science). \
Du begleitest sie jeden Tag über ihre Trainings-App und hinterlässt kurze Nachrichten, so wie ein \
guter Trainer seinem Klienten zwischen zwei Terminen schreibt: auf Deutsch, per Du, direkt, warm \
und ehrlich. Du kennst ihre Ziele, ihren Plan und alles, was du ihr schon gesagt hast."""

PROMPT = """<aufgabe>
Es ist Morgen. Schau dir an, was seit deiner letzten Nachricht im Chat passiert ist, und schreib \
der Person höchstens EINE Nachricht – oder keine. Neu ist zum Beispiel: eine neue Einheit, ein \
Check-in mit schlechtem Schlaf, viel Stress oder starkem Muskelkater, ein geplanter Trainingstag, \
der seit 3 oder mehr Tagen offen ist, ein neuer Block oder die Entlastungswoche. Gab es nichts \
davon, schreib nichts – eine Nachricht ohne neuen Anlass nervt und wird ignoriert.
Ein echter Coach:
- erkennt Erfolge und sagt es, wenn etwas gut läuft (neue Bestleistung, volle Trainingswoche, \
  sauber gesteigert, nach einer Pause zurückgekommen). Lob nur mit Beleg aus den Daten.
- spricht Probleme offen an (Sätze weit vor dem Versagen, ein geplanter Tag fällt aus), ohne zu \
  schimpfen und ohne Fehlzählungen aufzurechnen.
- erklärt in einem Halbsatz, warum etwas wichtig ist, und endet mit genau einer nächsten \
  Handlung als konkretem Wenn-dann-Plan (Tag und Anlass, z. B. "Morgen nach der Arbeit: UK B").
- bezieht sich auf die eigenen Gründe der Person ("profil.motivation"), nicht auf Druck.
- greift auf, was die Person im Chat gefragt oder erzählt hat ("letzte_nachrichten"), und \
  wiederholt nichts, was dort schon steht.
- passt sich der Lage an: am Blockanfang Routine aufbauen, mitten im Block dranbleiben und sauber \
  steigern, vor der Entlastung auf Erholung achten.
Du änderst nichts am Plan, die Person setzt deine Vorschläge selbst um. Sie kann dir antworten.

Gewohnheiten erkennen: "muster.ersetzt_im_training" listet Planübungen, die die Person im \
Training mehrfach durch eine andere ersetzt hat (auch beim letzten Mal). Prüf, ob ein dauerhafter \
Tausch im Plan sinnvoll ist: Trifft der Ersatz dieselben Muskeln mindestens so gut für ihr Ziel, \
und spricht im Chat nichts dagegen (z. B. "Gerät war nur besetzt")? Dann ist das ein guter Anlass \
für deine Nachricht: Sprich die Gewohnheit an (wie oft, seit wann) und biete den Tausch an, indem \
du ihn in "aktionen" einträgst – die App zeigt dafür einen Ersetzen-Knopf, die Person entscheidet. \
Ist das Original klar besser, sag kurz warum und schlag keinen Tausch vor.

Außerdem schreibst du JEDEN Tag einen Impuls für die Startseite der App: ein Satz, höchstens \
{max_impulse} Zeichen, der genau zu heute passt. Steht heute Training an ("heute_geplant"), ein \
konkreter Tipp oder Ansporn dafür (z. B. die Übung, bei der eine Steigerung fällig ist). An einem \
Ruhetag etwas für Erholung, Schritte, Eiweiß oder Schlaf, oder echte Motivation mit Bezug zu ihrem \
Ziel und Fortschritt. Abwechseln: nie dasselbe Thema oder dieselbe Formulierung wie in \
"letzte_impulse". Kein Lob ohne Beleg, keine Floskeln wie "Du schaffst das!".
</aufgabe>

<was_die_app_schon_selbst_macht>
Schlag NICHTS davon vor, das erledigt die App automatisch:
- Die Satzzahl bleibt im Block jede Woche wie im Plan (nur die Entlastungswoche halbiert sie); die \
  App erhöht Sätze nie automatisch, das will die Person so. Schlag also keine Satz-Steigerung als \
  Automatik vor. Die Sätze dieser Woche stehen in "plan.tage[].uebungen[].saetze_diese_woche".
- Gewicht und Wiederholungen: Anfänger linear, sonst doppelte Progression mit RPE; Plateau, \
  Rücksetzen, Wiedereinstieg nach Pausen (> 14 Tage 93 %, > 28 Tage 87 %).
- Entlastung nur bei zwei Ermüdungssignalen, sonst am Blockende.
- Wochen-Check: Varianten bei wiederholtem Stillstand tauschen. Verpasste Sätze holt die App \
  nicht nach. Was die App schon geändert hat, steht in "app_aenderungen".
- Alles in "regel_check.meldungen" sieht die Person bereits.
</was_die_app_schon_selbst_macht>

<worauf_du_achtest>
Nach Wirkung sortiert:
1. Regelmäßigkeit – nach vorne schauen, nicht abrechnen: Zähl nur die laufende Woche \
   ("diese_woche": x von y, was noch offen ist). Kam die Person nach einer Pause zurück \
   ("pause_vor_letzter_einheit_tage" ≥ 5), würdige genau das – Zurückkommen ist der wichtigste \
   Schritt. Ist der letzte geplante Tag ausgefallen, ist die einzige Ermahnung: nie zweimal \
   hintereinander auslassen, plus wann der nächste Termin ist. Rechne NIE Fehlzählungen seit \
   Blockstart vor ("2 von 7 Einheiten") – das demotiviert und ändert nichts.
2. Ausführung laut Daten: Sätze mit RPE ≤ 6 oder unter dem Wiederholungsbereich, stark \
   schwankende Gewichte, Bestleistungen.
3. Planaufbau: Fokus-Muskel ohne passende Übung, Beschwerden aus "profil.beschwerden", gleiche \
   Muskeln an aufeinanderfolgenden Tagen, Einheit zu lang für "profil.session_minutes".
4. Erholung: mehrere Check-ins mit schlechtem Schlaf, hohem Stress oder starkem Muskelkater.
Was heute ansteht, steht in "heute_geplant" – genau das sieht die Person auch auf ihrer \
Startseite; widersprich dem nie (auch nicht im Impuls).
In Blockwoche 1 oder bei weniger als 3 Einheiten sind Trends nicht belastbar: dann keine Aussagen \
über Stillstand, sondern Routine, Ausführung oder Lob für den Start.
</worauf_du_achtest>

<regeln>
- Jede Aussage folgt aus den Daten und nennt den Beleg (Datum, Gewicht x Wdh. @ RPE, Anzahl \
  Einheiten). Nichts erfinden, keine allgemeinen Tipps ohne Datenbezug.
- Ein Hinweis = eine Sache. Grenzen der App gelten auch für dich (nie über MRV, keine Entlastung \
  ohne Signale). Keine medizinischen Ratschläge; bei Schmerzen an Arzt oder Physio verweisen.
- Nicht wiederholen: kein Thema (z. B. "RPE bei Übung X"), das in "letzte_impulse", \
  "letzte_nachrichten" oder "weggeklickt" schon vorkam – "weggeklickt" wollte die Person nicht.
- Ernährung nur bewerten, wenn "ernaehrung.tage_mit_eintrag" mindestens {min_nutrition} ist; \
  sonst höchstens zum Eintragen ermuntern, nie "du isst zu wenig/zu viel".
- Höchstens {max_text} Zeichen, per Du, wie eine kurze Messenger-Nachricht. Kurze Absätze mit \
Leerzeile dazwischen, **fett** nur für die eine Kernaussage, keine Überschriften oder Emojis.
</regeln>

<beispiele>
Gut (Lob): {{"message": "Do und Fr direkt beide Einheiten durchgezogen, genau so baut sich die \
Routine auf. Halt den Rhythmus: nächste Einheit OK B."}}
Gut (Ausführung): {{"message": "Rudern Kabel am 09.10.: Satz 3 endete bei 60x6 @ RPE 6, da war noch \
viel Luft. Bleib bei 60 kg und geh bis RPE 8–9, nur so erkennt die App, wann sie steigern darf."}}
Gut (Zurückkommen): {{"message": "Nach 9 Tagen Pause gestern direkt die volle OK-Einheit, das \
ist der wichtigste Schritt überhaupt. **Diese Woche 1 von 3** – Fr nach der Arbeit UK B, dann \
läuft der Rhythmus wieder."}}
Schlecht: "Seit Blockstart erst 2 von 7 Einheiten" (Fehlzählung, demotiviert), \
"Mehr Sätze für Waden" (macht der Block selbst), "Achte auf deine Ernährung" (kein \
Datenbezug), "Super gemacht!" (Lob ohne Beleg), dieselbe Botschaft wie gestern.
</beispiele>

<ausgabe>
Denk zuerst kurz in <analyse>…</analyse> nach (höchstens 8 Stichpunkte). Gib dann genau einen Block aus:
<nachricht>{{"impuls": "...", "message": "...", "aktionen": []}}</nachricht>
"aktionen" nur bei einem Tausch-Angebot in der Nachricht, sonst leer: \
[{{"typ": "ersetzen", "plan_id": <plan_id aus muster>, "neu": "<exakter Name des Ersatzes>"}}]
Der Impuls ist Pflicht. Keine Nachricht ist gültig: <nachricht>{{"impuls": "...", "message": null}}</nachricht>
</ausgabe>

<daten>
{data}
</daten>"""


def _date(iso):
    """Ortsdatum als ISO-Text – eine Einheit um 00:30 gehoert zum neuen Tag, nicht zum UTC-Vortag."""
    d = stats._local_date(iso)
    return d.isoformat() if d else ""


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
    recent = db.rows("SELECT role, source, created_at, text FROM coach_messages WHERE profile_id=?"
                     " AND status='ok' ORDER BY id DESC LIMIT ?", (profile_id, RECENT_MESSAGES))
    return {
        "heute": f"{WEEKDAYS[today.weekday()]} {today.isoformat()}",
        "profil": {"ziel": profile.get("goal"), "erfahrung": profile.get("experience"),
                   "fokus": profile.get("focus"), "beschwerden": profile.get("limitations"),
                   "session_minutes": profile.get("session_minutes"),
                   "motivation": profile.get("motivation")},
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
        "muster": {"ersetzt_im_training": coach_patterns.substitutions(profile_id, today)},
        "heute_geplant": _today_plan(profile_id, split, today, workouts),
        "diese_woche": stats.week_status(profile_id, split, today),
        "pause_vor_letzter_einheit_tage": _gap_before_last(workouts),
        "weggeklickt": [r["title"] for r in db.rows(
            "SELECT title FROM coach_notes WHERE profile_id=? AND status='dismissed' AND date >= ?",
            (profile_id, (today - timedelta(days=DISMISSED_DAYS)).isoformat()))],
        "letzte_impulse": [r["text"] for r in db.rows(
            "SELECT text FROM coach_impulses WHERE profile_id=? ORDER BY date DESC LIMIT ?",
            (profile_id, RECENT_IMPULSES))],
        "letzte_nachrichten": [{"von": "du" if m["role"] == "coach" else "person",
                                "datum": _date(m["created_at"]), "text": m["text"]}
                               for m in reversed(recent)],
    }


def _today_plan(profile_id, split, today, workouts):
    """Was heute dran ist – dieselbe Rechnung wie die Startseite (stats.schedule_day)."""
    day, fixed, note = stats.schedule_day(profile_id, split, today)
    trained = any(w["datum"] == today.isoformat() for w in workouts)
    if not day:
        return {"tag": None, "heute_schon_trainiert": trained}
    return {"tag": day["label"], "wie_geplant": fixed,
            "hinweis": {"nachgeholt": "nachgeholt von einem früheren Tag dieser Woche",
                        "erholung": "eigentlich fällig, aber die Muskeln von gestern brauchen noch Ruhe"
                        }.get(note) or (None if fixed else "kein fester Termin heute, nächster offener Tag"),
            "heute_schon_trainiert": trained}


def _gap_before_last(workouts):
    """Tage Pause vor der letzten Einheit – zum Erkennen eines Wiedereinstiegs."""
    days = sorted({date.fromisoformat(w["datum"]) for w in workouts if w["datum"]})
    return (days[-1] - days[-2]).days if len(days) >= 2 else None


def prompt(ctx):
    return PROMPT.format(max_text=MAX_TEXT, max_impulse=MAX_IMPULSE, min_nutrition=MIN_NUTRITION_DAYS, data=json.dumps(ctx, ensure_ascii=False, default=str))


def _clip_text(text, limit):
    """Wie _clip, aber Absaetze bleiben: Leerzeichen je Zeile zusammenfassen, hoechstens eine
    Leerzeile zwischen Absaetzen."""
    paras = ["\n".join(" ".join(line.split()) for line in p.splitlines() if line.strip())
             for p in re.split(r"\n\s*\n", str(text or ""))]
    out = "\n\n".join(p for p in paras if p).strip()
    return out if len(out) <= limit else out[:limit - 1].rstrip() + "…"


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse(raw):
    """{"impuls": text|None, "message": text|None} aus dem <nachricht>-Block, gekuerzt.
    message None = heute nichts Neues. ValueError nur, wenn gar kein JSON-Objekt da ist."""
    raw = raw or ""
    tag = re.search(r"<nachricht>(.*?)</nachricht>", raw, re.S)
    match = re.search(r"\{.*\}", tag.group(1) if tag else raw, re.S)
    if not match:
        raise ValueError("Kein JSON in der Antwort")
    data = json.loads(match.group(0))
    if not isinstance(data, dict) or not ({"message", "impuls"} & set(data)):
        raise ValueError("Antwort hat weder impuls noch message")

    def text(key, limit, clip=_clip):
        value = data.get(key)
        return (clip(value, limit) or None) if isinstance(value, str) else None

    actions = data.get("aktionen") if isinstance(data.get("aktionen"), list) else []
    message = text("message", MAX_TEXT, _clip_text)
    return {"impuls": text("impuls", MAX_IMPULSE), "message": message,
            "aktionen": actions if message else []}     # ein Knopf ohne Nachricht waere ohne Erklaerung
