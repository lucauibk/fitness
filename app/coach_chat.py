"""Coach-Chat: Fragen an den Coach und die Nachrichten des Morgen-Coaches in einem Verlauf.

Jede Frage ist ein eigener `claude -p`-Aufruf (Claude-Abo, siehe claude_cli) mit frischen Daten
und den letzten Nachrichten aus der DB – keine CLI-Sitzungen. So stehen Plan und letzte Einheit
bei jeder Frage auf dem aktuellen Stand, und der Verlauf liegt nur an einer Stelle.
Claude hat keine Werkzeuge und aendert nie selbst etwas. Will die Person eine Uebung tauschen,
haengt der Coach Vorschlaege als <aktionen>-Block an; das Modul prueft sie gegen den eigenen Plan
und den Katalog und speichert sie an der Nachricht. Ersetzt wird erst, wenn die Person in der
App auf den Knopf tippt (apply_action) – und das laesst sich rueckgaengig machen."""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from . import activity, body, claude_cli, coach, db, nutrition, rewards, stats, workout_review
from .planner import explain, generate

log = logging.getLogger(__name__)
MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-5-5")
TIMEOUT = 120
MAX_QUESTION = 1000
HISTORY = 30
PAGE = 30
NUTRITION_DAYS = 7
CARDIO_DAYS = 14
MAX_ACTIONS = 4
ALTERNATIVES = 5

# Ein CLI-Prozess braucht auf dem Pi gut 250 MB und das Abo-Limit ist geteilt – eine Antwort
# zur Zeit reicht fuer zwei Profile.
_busy = threading.Lock()

SYSTEM = coach.SYSTEM + """

Jetzt schreibt dir die Person direkt im Chat der App. Du hast bei jeder Nachricht alles, was die \
App über sie weiß, in <daten> (Plan, Block, Einheiten, Check-ins, Cardio, Ernährung, Körper) und \
euer bisheriges Gespräch in <verlauf>. Nutze beides: Bezieh dich auf frühere Nachrichten, frag \
nicht nach Dingen, die in den Daten stehen.

So antwortest du:
- wie ein Trainer im Messenger: kurz (meist 2–6 Sätze), per Du, direkt und warm
- konkret mit Beleg aus den Daten (Datum, Gewicht x Wdh., Anzahl Einheiten), nichts erfinden; \
fehlen Daten, sag ehrlich, was sie eintragen müsste
- Formatierung: kurze Absätze mit Leerzeile dazwischen, **fett** für die eine Kernaussage oder \
wichtige Zahlen, Aufzählungen mit "- " wenn es mehrere Punkte sind. Keine Überschriften, keine \
Tabellen, keine Emojis.
- keine medizinischen Diagnosen; bei Schmerzen oder Verletzungen an Arzt oder Physio verweisen
- bleib bei Training, Ernährung, Erholung und Motivation

Regelmäßigkeit: nach vorne schauen, nicht abrechnen. Sprich höchstens über die laufende Woche \
("diese_woche") und würdige es, wenn sie nach einer Pause zurückgekommen ist; rechne nie \
Fehlzählungen seit Blockstart vor. Was heute dran ist, steht in "heute_geplant" – genau das zeigt \
auch ihre Startseite. Bezieh dich auf ihre eigenen Gründe ("profil.motivation").
Ernährung nur bewerten, wenn "ernaehrung.tage_mit_eintrag" mindestens 4 ist – sonst sag ehrlich, \
dass dafür mehr Einträge nötig sind.
Länge: meist höchstens ~600 Zeichen; länger nur, wenn du mehrere Optionen als Liste aufzählst.

Plan ändern: Du änderst nichts selbst. Möchte die Person eine Übung tauschen (oder passt eine \
Übung wegen Beschwerden, Gerät oder Vorliebe nicht), schlag 1–3 passende Alternativen vor und \
erklär in einem Halbsatz je Option, warum. Nimm bevorzugt Übungen aus "plan.tage[].uebungen[]\
.alternativen", sonst exakte Namen aus "uebungskatalog". Hänge dann GANZ AM ENDE deiner Antwort \
diesen Block an – die App macht daraus Knöpfe zum Ersetzen, die Person entscheidet:
<aktionen>[{"typ": "ersetzen", "plan_id": <plan_id der Übung>, "neu": "<exakter Name>"}]</aktionen>
Steht in "muster.ersetzt_im_training" eine Übung, die sie im Training immer wieder durch eine \
andere ersetzt, und passt das zur Frage, sprich es an und biete den dauerhaften Tausch an. \
Steht die Übung an mehreren Tagen und ist der Tag nicht klar, frag NICHT nach – biete die \
Optionen für jeden dieser Tage als eigene Knöpfe an (jede plan_id ein Eintrag); jeder Knopf zeigt \
den Tag. Verwende im Text exakt dieselben Übungsnamen wie im Block. Ist unklar, welche Übung \
gemeint ist, frag nach statt zu raten, dann ohne Block. \
Für alle anderen Änderungen (Sätze, Tage, Gewichte) sag, wo sie es in der App selbst ändert \
(Training → Plan, Profil, Ernährung)."""


# Waehrend einer laufenden Einheit: dieselbe Person, aber im Studio zwischen zwei Saetzen.
SESSION_SYSTEM = """

GERADE LÄUFT EINE EINHEIT. In <daten>.laufende_einheit steht der Stand aus der App: jede Übung \
mit "nr", Zielbereich, Sätzen (erledigt oder offen, mit Wdh., kg, RPE), "schritt_kg", \
"alternativen" und was letztes Mal war; "aktuell" ist die Übung, an der sie gerade ist. Sie liest \
zwischen zwei Sätzen am Handy:
- Antworte in 1–3 kurzen Sätzen (höchstens ~300 Zeichen), ohne Einleitung; eine Aufzählung nur \
für Optionen.
- Ist nicht klar, welche Übung gemeint ist, meint sie "aktuell".
- Gewicht: richte dich nach den erledigten Sätzen dieser Einheit und dem Ziel (block.ziel_rir; \
RPE 10 = keine Wiederholung mehr möglich). Lag der letzte Satz über dem Ziel-RPE oder unter dem \
Wdh.-Bereich: weniger. Deutlich leichter bzw. über dem Bereich: mehr. Ändere nur in Vielfachen \
von "schritt_kg".
- Schmerz (nicht Muskelbrennen): Übung jetzt beenden, schmerzfreie Alternative anbieten, bei \
anhaltenden Beschwerden ärztlich abklären.
Änderungen für diese Einheit bietest du als Knöpfe an – im selben <aktionen>-Block am Ende:
- nur heute tauschen (Gerät besetzt, fühlt sich nicht gut an): {"typ": "heute_tauschen", \
"nr": <nr>, "neu": "<exakter Name>"} – bevorzugt aus "alternativen" der Übung, sonst aus \
"uebungskatalog", nie etwas, das schon in der Einheit steht.
- offene Sätze anpassen: {"typ": "saetze", "nr": <nr>, "gewicht": <kg oder null>, "wdh": <Zahl \
oder null>, "saetze": <Anzahl Arbeitssätze gesamt oder null>} – nur setzen, was sich ändert; \
erledigte Sätze bleiben, wie sie sind.
Dauerhaft im Plan ersetzen ("typ": "ersetzen") nur, wenn sie ausdrücklich den Plan ändern will. \
Fragt sie nach einer Änderung, frag nicht zurück – schlag sie als Knopf vor."""
SESSION_ALTERNATIVES = 4


class Busy(Exception):
    """Es laeuft schon eine Antwort."""


# ------------------------------------------------------------ Nachrichten
def _out(row):
    if row is None:
        return None
    row = dict(row)
    row["actions"] = json.loads(row["actions"]) if row.get("actions") else []
    return row


def add_message(profile_id, role, text, source="chat", status="ok", actions=None):
    with db.connect() as c:
        cur = c.execute("INSERT INTO coach_messages (profile_id, role, text, source, status, created_at,"
                        " actions) VALUES (?,?,?,?,?,?,?)",
                        (profile_id, role, text, source, status, db.now(),
                         json.dumps(actions, ensure_ascii=False) if actions else None))
        new_id = cur.lastrowid
    return get_message(new_id)


def get_message(message_id):
    return _out(db.one("SELECT * FROM coach_messages WHERE id=?", (message_id,)))


def messages(profile_id, before=None, limit=PAGE):
    """Neueste zuerst geholt, chronologisch zurueckgegeben (fuers Nachladen beim Hochscrollen)."""
    rows = db.rows("SELECT * FROM coach_messages WHERE profile_id=? AND id < ? ORDER BY id DESC LIMIT ?",
                   (profile_id, before or 2**62, limit))
    return [_out(r) for r in reversed(rows)]


def unread(profile_id):
    seen = (db.get_row("profiles", profile_id) or {}).get("coach_seen_id") or 0
    return db.one("SELECT COUNT(*) AS n FROM coach_messages WHERE profile_id=? AND role='coach'"
                  " AND status='ok' AND id > ?", (profile_id, seen))["n"]


def mark_seen(profile_id, message_id):
    with db.connect() as c:
        c.execute("UPDATE profiles SET coach_seen_id=MAX(COALESCE(coach_seen_id,0), ?) WHERE id=?",
                  (message_id, profile_id))


GREETING = ("Hallo, ich bin dein Coach. Ich kenne deinen Plan, deine Einheiten und Check-ins – "
            "frag mich alles zu Training, Ernährung und Erholung.")
# Falls der Morgen-Coach heute (noch) nichts geschrieben hat: allgemeine, belastbare Tipps,
# einer pro Tag im Wechsel. Bewusst nichts Personalisiertes – das kann nur der Coach mit Daten.
FALLBACK_TIPS = (
    "Steigern schlägt Abwechslung: Mehr Gewicht oder eine Wiederholung mehr als letztes Mal ist das Ziel jeder Einheit.",
    "Die letzten Wiederholungen zählen am meisten – beende Arbeitssätze mit 1–3 Wiederholungen Reserve.",
    "Eiweiß auf 3–4 Mahlzeiten verteilen hilft beim Muskelaufbau mehr als eine große Portion am Abend.",
    "7–9 Stunden Schlaf sind Training, das niemand sieht: Erholung entscheidet, ob der Reiz ankommt.",
    "Schritte sind der unterschätzte Hebel: 8.000–10.000 am Tag verbrennen mehr als die meisten Cardio-Einheiten.",
    "Trag RPE oder Reserve mit ein – nur so weiß die App, wann sie das Gewicht erhöhen darf.",
    "Eine verpasste Einheit ist kein Problem, zwei in Folge schon: Nie zweimal hintereinander auslassen.",
    "Sauber vor schwer: Volle Bewegungsamplitude bringt mehr Wachstum als Gewicht mit halber Strecke.",
    "Trinken nicht vergessen – schon 2 % Flüssigkeitsverlust kosten messbar Kraft im Training.",
    "Fortschritt zeigt sich über Wochen, nicht Tage: Vergleich dich mit dir vor einem Monat.",
)


def set_impulse(profile_id, text, day=None):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO coach_impulses (profile_id, date, text) VALUES (?,?,?)",
                  (profile_id, (day or stats.today()).isoformat(), text))


AFTER_WORKOUT = "Training erledigt. Jetzt zählt die Erholung: genug Eiweiß und eine gute Nacht Schlaf."


def impulse(profile_id, done_today=None):
    """Text fuer die Coach-Karte auf "Heute". Nach dem heutigen Training die Kurzbilanz der
    Einheit (der Morgen-Impuls "geh heute bis RPE 8" ist dann ueberholt); sonst der Impuls des
    Morgen-Coaches, zur Begruessung die Vorstellung oder ein allgemeiner Tipp des Tages."""
    if done_today:
        # Das Urteil aus der Auswertung nach dem Training; die Satz-Bilanz steht schon auf der
        # Erledigt-Karte darueber und waere hier nur doppelt.
        review = workout_review.get(done_today["id"]) or {}
        verdict = ((review.get("body") or {}).get("kurzurteil") or "").strip()
        return {"text": verdict or AFTER_WORKOUT, "kind": "erledigt"}
    today = stats.today()
    row = db.one("SELECT text FROM coach_impulses WHERE profile_id=? AND date=?",
                 (profile_id, today.isoformat()))
    if row:
        return {"text": row["text"], "kind": "impuls"}
    if not db.one("SELECT 1 AS x FROM coach_messages WHERE profile_id=? LIMIT 1", (profile_id,)):
        return {"text": GREETING, "kind": "hallo"}
    return {"text": FALLBACK_TIPS[today.toordinal() % len(FALLBACK_TIPS)], "kind": "tipp"}


def summary(profile_id, done_today=None):
    """Fuer "Heute": Impuls des Tages und wie viele Coach-Nachrichten ungelesen sind."""
    return {"impulse": impulse(profile_id, done_today), "unread": unread(profile_id)}


# ------------------------------------------------------------ Kontext
def _nutrition(profile_id):
    profile = db.get_row("profiles", profile_id)
    today = stats.today()
    days = [nutrition.day(profile_id, (today - timedelta(days=i)).isoformat())
            for i in range(NUTRITION_DAYS)]
    logged = [d["totals"] for d in days[1:] if d["totals"]["kcal"]]
    avg = {k: round(sum(t[k] for t in logged) / len(logged)) for k in ("kcal", "protein")} if logged else None
    return {"ziele": nutrition.goals(profile)["goals"],
            "heute": {k: round(days[0]["totals"][k]) for k in ("kcal", "protein", "carbs", "fat")},
            "schnitt_vortage": avg, "tage_mit_eintrag": len(logged)}


def context(profile_id):
    """coach.context (Plan, Block, Einheiten, Check-ins) plus Ernaehrung, Koerper und Topf.
    Ohne Plan bleibt der Rest, damit man auch dann fragen kann."""
    profile = db.profile_out(db.get_row("profiles", profile_id)) or {}
    ctx = coach.context(profile_id) or {
        "heute": stats.today().isoformat(), "plan": None,
        "profil": {"ziel": profile.get("goal"), "erfahrung": profile.get("experience"),
                   "fokus": profile.get("focus"), "beschwerden": profile.get("limitations")}}
    ctx.pop("letzte_nachrichten", None)     # der Chat bekommt den ganzen Verlauf separat
    latest = body.history(profile_id)["latest"]
    ctx["koerper"] = {"gewicht_kg": activity.latest_weight(profile_id),
                      "groesse_cm": profile.get("height_cm"), "geschlecht": profile.get("sex"),
                      **{k: latest[k]["value"] for k in ("waist_cm", "hip_cm", "body_fat_pct") if k in latest}}
    ctx["ernaehrung"] = _nutrition(profile_id)
    ctx["cardio"] = db.rows("SELECT date, activity, minutes, distance_km FROM cardio WHERE profile_id=?"
                            " AND date >= ? ORDER BY date", (profile_id, (stats.today() - timedelta(
                                days=CARDIO_DAYS)).isoformat()))
    ctx["shopping_topf"] = rewards.card(profile_id)
    _plan_ids(profile_id, ctx, profile)
    ctx["uebungskatalog"] = _catalog()
    ctx["impuls_heute"] = impulse(profile_id)["text"]   # steht auf "Heute" – Nachfragen beziehen sich oft darauf
    return ctx


def session_context(profile_id, session):
    """laufende_einheit aus dem Entwurf der App. Namen, Einheit, Gewichtsschritt, letztes Mal und
    Alternativen kommen aus der DB – vom Geraet nur, was es allein weiss: die Saetze von heute."""
    from .planner import progression
    ids = [e["exercise_id"] for e in session["exercises"]]
    rows = {r["id"]: r for r in db.rows(
        f"SELECT id, name, unit, pattern, equipment_type FROM exercises WHERE id IN ({','.join('?' * len(ids))})",
        ids)} if ids else {}
    injuries = generate.settings_from(db.profile_out(db.get_row("profiles", profile_id)) or {})["injuries"]
    out = []
    for nr, e in enumerate(session["exercises"]):
        ex = rows.get(e["exercise_id"])
        if not ex:
            continue
        top = max((s["weight"] or 0 for s in e["sets"]), default=0)
        item = {"nr": nr, "name": ex["name"],
                "einheit": "kg Zusatzgewicht (Körpergewicht)" if ex["unit"] == "koerper" else "kg",
                "ziel_wdh": f"{e['reps_min']}–{e['reps_max']}" if e.get("reps_min") else None,
                "schritt_kg": progression.step_for(ex["equipment_type"], ex["pattern"], top),
                "saetze": [{"wdh": s["reps"], "kg": s["weight"], "rpe": s["rpe"],
                            "erledigt": s["done"], **({"aufwaermen": True} if s["warmup"] else {})}
                           for s in e["sets"]],
                "letztes_mal": [{"wdh": s["reps"], "kg": s["weight"], "rpe": s["rpe"]}
                                for s in stats.last_sets(profile_id, ex["id"])]}
        if ex["pattern"]:
            item["alternativen"] = [a["name"] for a in generate.alternatives(
                ex["pattern"], set(ids), injuries)][:SESSION_ALTERNATIVES]
        out.append(item)
    started = session.get("started_at")
    minutes = None
    if started:
        try:
            minutes = max(0, round((datetime.now(timezone.utc) - datetime.fromisoformat(
                started.replace("Z", "+00:00"))).total_seconds() / 60))
        except ValueError:
            minutes = None
    block = session.get("block") or {}
    return {"titel": session.get("title"), "minuten_seit_start": minutes, "aktuell": session.get("current"),
            "block": {"woche": block.get("week"), "von": block.get("weeks"), "ziel_rir": block.get("rir"),
                      "entlastung": block.get("deload")} if block else None,
            "notiz": session.get("notes") or None, "uebungen": out}


def _plan_ids(profile_id, ctx, profile):
    """plan_id und passende Alternativen an jede Planuebung haengen – daraus baut der Coach
    seine Ersetzen-Vorschlaege. Alternativen: gleiches Bewegungsmuster, nicht schon im Plan,
    ohne Uebungen, die zu den Beschwerden aus dem Profil nicht passen."""
    split = db.active_split(profile_id)
    if not split or not ctx.get("plan"):
        return
    injuries = generate.settings_from(profile)["injuries"]
    in_plan = {e["exercise_id"] for d in split["days"] for e in d["exercises"]}
    for day, out in zip(split["days"], ctx["plan"]["tage"]):
        for e, item in zip(day["exercises"], out["uebungen"]):
            item["plan_id"] = e["id"]
            if e.get("pattern"):
                item["alternativen"] = [a["name"] for a in
                                        generate.alternatives(e["pattern"], in_plan, injuries)][:ALTERNATIVES]


def _catalog():
    """Alle Uebungsnamen nach Muskelgruppe – fuer Wuensche, die nicht unter den Alternativen sind."""
    out = {}
    for r in db.rows("SELECT name, muscle_group FROM exercises WHERE archived=0 ORDER BY name"):
        out.setdefault(r["muscle_group"] or "sonstige", []).append(r["name"])
    return out


STATE_WORD = {"open": "offen", "applied": "übernommen", "undone": "rückgängig gemacht",
              "skipped": "nicht gewählt"}


def _history(profile_id, limit=HISTORY):
    rows = db.rows("SELECT role, text, source, created_at, actions FROM coach_messages WHERE profile_id=?"
                   " AND status='ok' ORDER BY id DESC LIMIT ?", (profile_id, limit))
    out = []
    for r in reversed(rows):
        item = {"von": "du" if r["role"] == "coach" else "person",
                "art": {"daily": "morgen-check", "session": "training"}.get(r["source"], "chat"),
                "datum": coach._date(r["created_at"]), "text": r["text"]}
        if r["actions"]:
            item["vorschlaege"] = [f"{describe(a)}: {STATE_WORD[a['state']]}" for a in json.loads(r["actions"])]
        out.append(item)
    return out


def describe(a):
    """Ein Vorschlag in Worten – fuer den Verlauf, den das Modell sieht."""
    if a["type"] == "session_sets":
        parts = [f"{a['sets']} Sätze" if a.get("sets") else None, f"{a['reps']} Wdh." if a.get("reps") else None,
                 f"{a['weight']} kg" if a.get("weight") is not None else None]
        return f"heute {a['from']}: " + ", ".join(p for p in parts if p)
    prefix = "heute " if a["type"] == "session_swap" else ""
    return f"{prefix}{a['from']} → {a['to']}"


def prompt(profile_id, question, session=None):
    ctx = context(profile_id)
    if session:
        ctx["laufende_einheit"] = session_context(profile_id, session)
    data = json.dumps(ctx, ensure_ascii=False, default=str)
    history = json.dumps(_history(profile_id), ensure_ascii=False)
    return (f"<daten>\n{data}\n</daten>\n\n<verlauf>\n{history}\n</verlauf>\n\n"
            f"<frage>\n{question}\n</frage>")


# ------------------------------------------------------------ Vorschlaege
ACTIONS_RE = re.compile(r"<aktionen>(.*?)(?:</aktionen>|$)", re.S)


def split_actions(raw, profile_id, session=None):
    """Antworttext ohne <aktionen>-Block plus die gueltigen Vorschlaege."""
    match = ACTIONS_RE.search(raw or "")
    text = ACTIONS_RE.sub("", raw or "").strip()
    if not match:
        return text, []
    try:
        items = json.loads(match.group(1))
    except ValueError:
        log.warning("Coach: <aktionen> ohne gueltiges JSON")
        return text, []
    return text, validate_actions(items, profile_id, session)


def _number(value, lo, hi, integer=False):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("keine Zahl") from None
    if not lo <= v <= hi or (integer and v != int(v)):
        raise ValueError("ausserhalb des Bereichs")
    return int(v) if integer else round(v, 2)


def _session_action(a, session, names):
    """Vorschlag fuer die laufende Einheit -> gespeicherte Form, oder (None, Grund)."""
    try:
        nr = int(a.get("nr"))
    except (TypeError, ValueError):
        return None, "nr fehlt"
    exercises = session["exercises"]
    if not 0 <= nr < len(exercises):
        return None, "nr nicht in der Einheit"
    cur = exercises[nr]
    row = db.get_row("exercises", cur["exercise_id"])
    if not row:
        return None, "Übung unbekannt"
    base = {"index": nr, "from_id": row["id"], "from": row["name"], "state": "open"}
    if a["typ"] == "heute_tauschen":
        new = names.get(str(a.get("neu") or "").strip().casefold())
        why = ("Übung nicht im Katalog" if not new else "dieselbe Übung" if new["id"] == row["id"] else
               "steht schon in der Einheit" if new["id"] in {e["exercise_id"] for e in exercises} else None)
        if why:
            return None, why
        return {"type": "session_swap", **base, "day": "Nur heute", "to_id": new["id"], "to": new["name"],
                "unit": new["unit"], "pattern": new["pattern"], "kind": new["kind"]}, None
    try:
        weight = _number(a.get("gewicht"), 0, 500)
        reps = _number(a.get("wdh"), 1, 50, integer=True)
        sets = _number(a.get("saetze"), 1, 10, integer=True)
    except ValueError as e:
        return None, str(e)
    if weight is None and reps is None and sets is None:
        return None, "nichts zu ändern"
    done = sum(1 for s in cur["sets"] if s["done"] and not s["warmup"])
    if sets is not None and sets < done:
        return None, "weniger Sätze als schon erledigt"
    return {"type": "session_sets", **base, "day": "Nur heute", "weight": weight, "reps": reps,
            "sets": sets}, None


def validate_actions(items, profile_id, session=None):
    """Nur Vorschlaege, die genau so moeglich sind: im aktiven Plan ("ersetzen") bzw. in der
    laufenden Einheit ("heute_tauschen", "saetze" – nur, wenn die Frage aus dem Training kam).
    Ungueltiges faellt weg, damit nie ein Knopf erscheint, der etwas Falsches tut. Gilt fuer den
    Chat und den Morgen-Coach."""
    split = db.active_split(profile_id)
    plan = {e["id"]: (d, e) for d in (split or {}).get("days", []) for e in d["exercises"]}
    names = {r["name"].casefold(): r for r in db.rows(
        "SELECT id, name, unit, pattern, kind FROM exercises WHERE archived=0")}
    out, seen = [], set()
    for a in items if isinstance(items, list) else []:
        kind = a.get("typ") if isinstance(a, dict) else None
        if kind in ("heute_tauschen", "saetze"):
            action, why = _session_action(a, session, names) if session else (None, "keine laufende Einheit")
            key = (action or {}).get("index"), (action or {}).get("to_id"), kind
            if not why and key in seen:
                why = "doppelt"
            if why:
                log.info("Vorschlag verworfen (%s): %s", why, a)
                continue
            seen.add(key)
            out.append(action)
            if len(out) == MAX_ACTIONS:
                break
            continue
        if kind != "ersetzen":
            log.info("Vorschlag verworfen (unbekannter Typ): %s", a)
            continue
        day, item = plan.get(a.get("plan_id")) or (None, None)
        new = names.get(str(a.get("neu") or "").strip().casefold())
        why = ("plan_id nicht im aktiven Plan" if not item else
               "Übung nicht im Katalog" if not new else
               "dieselbe Übung" if new["id"] == item["exercise_id"] else
               "steht am Tag schon" if new["id"] in {e["exercise_id"] for e in day["exercises"]} else
               "doppelt" if (item["id"], new["id"]) in seen else None)
        if why:
            # Sichtbar im Log: verworfene Vorschlaege sind sonst nur ein fehlender Knopf
            log.info("Vorschlag verworfen (%s): %s", why, a)
            continue
        seen.add((item["id"], new["id"]))
        out.append({"type": "swap", "item_id": item["id"], "day": day["label"],
                    "from_id": item["exercise_id"], "from": item["name"],
                    "to_id": new["id"], "to": new["name"], "state": "open"})
        if len(out) == MAX_ACTIONS:
            break
    return out


class ActionError(Exception):
    pass


def apply_action(profile_id, message_id, index, undo=False):
    """Vorschlag aus dem Chat uebernehmen (oder zuruecknehmen). Prueft erneut gegen den aktuellen
    Plan: zwischen Vorschlag und Tippen kann sich der Plan geaendert haben."""
    msg = get_message(message_id)
    if not msg or msg["profile_id"] != profile_id:
        raise ActionError("Diese Nachricht gibt es nicht.")
    actions = msg["actions"]
    if not 0 <= index < len(actions):
        raise ActionError("Diesen Vorschlag gibt es nicht.")
    a = actions[index]
    if a["type"] in ("session_swap", "session_sets"):
        return _session_state(message_id, actions, index, undo)
    item = db.get_row("split_exercises", a["item_id"])
    split = db.active_split(profile_id)
    in_split = item and any(item["split_day_id"] == d["id"] for d in (split or {}).get("days", []))
    if not in_split:
        raise ActionError("Die Übung steht nicht mehr in deinem aktiven Plan.")
    if undo:
        if a["state"] != "applied" or item["exercise_id"] != a["to_id"]:
            raise ActionError("Das lässt sich nicht mehr rückgängig machen – der Plan wurde inzwischen geändert.")
        new_id, new_state, others = a["from_id"], "undone", "open"
        text = f"{a['to']} → {a['from']}: zurückgenommen (Coach-Chat)"
    else:
        if a["state"] != "open":
            raise ActionError("Dieser Vorschlag ist schon erledigt.")
        if item["exercise_id"] != a["from_id"]:
            raise ActionError(f"An der Stelle steht nicht mehr {a['from']} – der Plan wurde inzwischen geändert.")
        new_id, new_state, others = a["to_id"], "applied", "skipped"
        text = f"{a['from']} → {a['to']}: auf deinen Wunsch (Coach-Chat)"
    # Selbst gewaehlt: der Wochen-Check tauscht die Uebung nicht wieder weg (wie im Plan-Editor)
    db.update_row("split_exercises", a["item_id"], {"exercise_id": new_id, "generated": None})
    explain.log(profile_id, "swap", text, new_id, split["id"])
    a["state"] = new_state
    for i, other in enumerate(actions):     # andere Optionen fuer dieselbe Uebung
        if i != index and other["item_id"] == a["item_id"] and other["state"] in ("open", "skipped"):
            other["state"] = others
    with db.connect() as c:
        c.execute("UPDATE coach_messages SET actions=? WHERE id=?",
                  (json.dumps(actions, ensure_ascii=False), message_id))
    return get_message(message_id)


def _session_state(message_id, actions, index, undo):
    """Vorschlaege fuer die laufende Einheit aendern nur den Entwurf auf dem Geraet (den kennt der
    Server nicht). Hier wird nur festgehalten, was gewaehlt wurde – fuer die Knoepfe und den Verlauf."""
    a = actions[index]
    if undo:
        if a["state"] != "applied":
            raise ActionError("Dieser Vorschlag ist nicht übernommen.")
        a["state"], others = "undone", "open"
    else:
        if a["state"] != "open":
            raise ActionError("Dieser Vorschlag ist schon erledigt.")
        a["state"], others = "applied", "skipped"
    for i, other in enumerate(actions):     # andere Vorschlaege fuer dieselbe Uebung der Einheit
        if (i != index and other["type"] in ("session_swap", "session_sets")
                and other["index"] == a["index"] and other["state"] in ("open", "skipped")):
            other["state"] = others
    with db.connect() as c:
        c.execute("UPDATE coach_messages SET actions=? WHERE id=?",
                  (json.dumps(actions, ensure_ascii=False), message_id))
    return get_message(message_id)


# ------------------------------------------------------------ Antworten
def friendly(error):
    text = str(error)
    low = text.lower()
    if "limit" in low or "rate" in low or "usage" in low:
        return "Das Claude-Abo hat gerade sein Nutzungslimit erreicht. Probier es später nochmal."
    if "zu lange" in low:
        return "Der Coach hat zu lange gebraucht. Probier es gleich nochmal."
    return "Der Coach konnte gerade nicht antworten. Probier es gleich nochmal."


def answer(profile_id, question, session=None):
    """Generator: ("delta", text)…, dann ("done", nachricht) oder ("error", nachricht).
    Die Frage wird vor dem Aufruf gespeichert, die Antwort danach – auch eine misslungene,
    damit der Verlauf zeigt, dass die Frage offen blieb. session: Entwurf der laufenden Einheit
    (dann kurze Antworten und Vorschlaege fuer heute)."""
    if not _busy.acquire(blocking=False):
        raise Busy()
    source = "session" if session else "chat"
    system = SYSTEM + SESSION_SYSTEM if session else SYSTEM
    try:
        built = prompt(profile_id, question, session)   # vor dem Speichern: die Frage steht in <frage>
        add_message(profile_id, "user", question, source)
        try:
            for kind, text in claude_cli.stream(built, system, MODEL, TIMEOUT):
                if kind == "delta":
                    yield "delta", text
                else:
                    clean, actions = split_actions(text, profile_id, session)
                    yield "done", add_message(profile_id, "coach", clean, source, actions=actions)
                    return
        except (RuntimeError, OSError) as e:
            log.warning("Coach-Chat fehlgeschlagen (Profil %s): %s", profile_id, e)
            yield "error", add_message(profile_id, "coach", friendly(e), source, status="error")
    finally:
        _busy.release()
