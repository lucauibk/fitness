"""Fasten-Berater: welche Fastenart passt, wann ist Fasten keine gute Idee, Tipps je Fastenart.

Die Entscheidung trifft recommend() – rein, deterministisch, ohne KI. Die KI (advise) erklaert sie
nur und gibt Tipps; sie sieht nur Regelausgabe, Ziel, Alter, BMI-Klasse und Quote (keine Namen, keine
einzelnen Tage) und laeuft nur auf Knopfdruck. Bei einer Sperre wird sie nie aufgerufen.

Schwellen (18 Jahre, BMI 18,5 / 20, 5 von 7) sind Setzungen aus gaengigen Empfehlungen, keine
Leitlinie. Kein Medizinprodukt."""
import hashlib
import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from . import activity, ai, body_advice, claude_cli, db, fasting, stats

log = logging.getLogger(__name__)
MODEL = os.environ.get("FASTING_TIPS_MODEL", body_advice.MODEL)
TIMEOUT = 90
CACHE_HOURS = 24
MAX_TIPS = 3
MAX_CHARS = 220
MAX_EXPLAIN_CHARS = 500

MIN_AGE = 18
MIN_BMI = 18.5            # darunter gesperrt
ADVANCED_MIN_BMI = 20     # ab hier 18:6 ueberhaupt empfehlbar
STEP_UP_HITS = 5          # von den letzten STEP_UP_OF Fasten
STEP_UP_OF = 7
TRAINING_DAYS = 28        # so weit zurueck zaehlen Einheiten fuer den Trainingszeitpunkt
MIN_TRAININGS = 3
CHECKIN_DAYS = 7
MIN_CHECKINS = 3
BAD_SLEEP = 2.5           # Check-in-Skala 1 (sehr schlecht) .. 5 (sehr gut)
HIGH_STRESS = 3.5         # 1 (entspannt) .. 5 (sehr hoch)
ADVANCED_EXPERIENCE = ("fortgeschritten", "erfahren")

DISCLAIMER = ("Das ist eine Orientierung, kein medizinischer Rat. Bei Vorerkrankungen oder Medikamenten "
              "sprich vorher mit deiner Ärztin oder deinem Arzt.")

EVIDENCE_ADVANCED = (
    "Zu 18:6, 20:4 und OMAD ist die Studienlage dünn: Es gibt kaum Langzeitstudien, und ein "
    "Zusatznutzen gegenüber 16:8 ist nicht belegt – beim Abnehmen zählt vor allem die "
    "Kalorienbilanz. Dafür wird es schwerer, im kurzen Essensfenster genug Eiweiß und Nährstoffe "
    "zu essen. Nur wählen, wenn es sich gut anfühlt.")

BLOCK_TEXTS = {
    "age": "Unter 18 Jahren wird Intervallfasten nicht empfohlen (Wachstum und Entwicklung).",
    "bmi": "Bei einem BMI unter 18,5 (Untergewicht) wird Intervallfasten nicht empfohlen.",
    "pregnant": "In Schwangerschaft und Stillzeit wird Intervallfasten nicht empfohlen.",
    "eating_disorder": ("Bei einer Essstörung (auch früher) kann Fasten Rückfälle auslösen – bitte "
                        "nicht ohne ärztliche oder therapeutische Begleitung."),
    "diabetes_meds": ("Bei Diabetes mit Medikamenten (z. B. Insulin) kann Fasten zu gefährlichem "
                      "Unterzucker führen – nur nach Rücksprache mit der Ärztin oder dem Arzt."),
}
MISSING_LABELS = {"birth_year": "Geburtsjahr", "height_cm": "Größe", "weight_kg": "Gewicht"}

# Training zum Essensfenster: je nach typischer Uhrzeit der Einheiten
TRAINING_HINTS = {
    "morgens": ("Du trainierst meist morgens: Trainier nüchtern nur, wenn es sich gut anfühlt – "
                "sonst leg die erste Mahlzeit direkt hinter das Training."),
    "mittags": ("Du trainierst meist mittags oder nachmittags: Leg die erste Mahlzeit direkt vor "
                "oder nach das Training."),
    "abends": ("Du trainierst meist abends: Lass das Essensfenster bis nach dem Training laufen, "
               "damit das Eiweiß danach noch drin ist."),
    None: ("Leg schwere Einheiten ins Essensfenster oder direkt davor oder danach und vermeide hartes "
           "Training am Ende einer langen Fastenphase."),
}

# Neutrale Texte, keine Stundenversprechen ("ab Stunde X passiert Y")
COMMON_TIPS = (
    "Trinken: Wasser, Tee oder Kaffee ohne Zucker und Milch im Fasten zählen nicht als Essen.",
    "Elektrolyte: Bei Kopfweh oder Schwindel hilft oft eine Prise Salz im Wasser; Magnesium und "
    "Kalium kommen über Gemüse, Hülsenfrüchte und Nüsse im Essensfenster.",
    "Training im Essensfenster: Schwere Einheiten lieber ins Fenster oder direkt davor oder "
    "danach legen; bei Schwindel oder Zittern abbrechen und etwas essen.",
    "Fasten brechen: Mit Eiweiß und Gemüse starten, nicht mit einer großen Portion Zucker "
    "oder Weißmehl; langsam essen.",
    "Eiweiß: Pro Tag etwa 1,2–1,6 g je kg Körpergewicht, auf zwei bis drei Mahlzeiten im "
    "Essensfenster verteilt.",
)
KIND_TIPS = {
    "12:12": ("Gut zum Einstieg: Abendessen etwas früher, Frühstück nicht sofort nach dem Aufstehen.",),
    "14:10": ("Der Einstieg: Spät essen vermeiden und das Frühstück ein wenig nach hinten schieben.",),
    "16:8": ("Starte mit einem festen Essensfenster (z. B. 11–19 Uhr), damit der Rhythmus stabil wird.",),
    "18:6": ("Im 6-Stunden-Fenster brauchst du meist zwei volle Mahlzeiten, damit Eiweiß und "
             "Nährstoffe reichen.",),
    "20:4": ("Im 4-Stunden-Fenster ist die Eiweißmenge schwer zu erreichen; plane eine eiweißreiche "
             "Hauptmahlzeit fest ein.",),
    "OMAD": ("Mit nur einer Mahlzeit ist es schwer, den Eiweiß- und Nährstoffbedarf zu decken; bei "
             "Schwindel, Schlafproblemen oder Heißhunger lieber ein längeres Fenster wählen.",),
}


# ------------------------------------------------------------------ Hilfen
def _flags(profile):
    """Flags als Liste, egal ob rohe DB-Zeile (Text) oder profile_out (Liste)."""
    value = profile.get("fasting_flags")
    return db.parse_flags(",".join(value) if isinstance(value, (list, tuple)) else value)


def age(profile, now):
    year = profile.get("birth_year")
    return now.year - int(year) if year else None


def bmi(profile, weight_kg):
    h = profile.get("height_cm")
    if not h or not weight_kg:
        return None
    return round(weight_kg / (h / 100) ** 2, 1)


def bmi_class(value):
    if value is None:
        return "unbekannt"
    if value < MIN_BMI:
        return "Untergewicht"
    if value < 25:
        return "Normalgewicht"
    if value < 30:
        return "Übergewicht"
    return "Adipositas"


def quota(history, n=STEP_UP_OF):
    """{"hits", "total"} ueber die letzten n beendeten Fasten (history: neueste zuerst)."""
    last = history[:n]
    return {"hits": sum(1 for e in last if e.get("hit")), "total": len(last)}


def blockers(profile, weight_kg, now):
    """Gruende, aus denen Fasten nicht empfohlen wird: [{"code", "text"}]. Fehlende Angaben
    sperren nicht (siehe recommend: dann nur 14:10)."""
    out = []
    a = age(profile, now)
    if a is not None and a < MIN_AGE:
        out.append({"code": "age", "text": BLOCK_TEXTS["age"]})
    b = bmi(profile, weight_kg)
    if b is not None and b < MIN_BMI:
        out.append({"code": "bmi", "text": f"{BLOCK_TEXTS['bmi']} Dein BMI: {str(b).replace('.', ',')}."})
    for flag in _flags(profile):
        out.append({"code": flag, "text": BLOCK_TEXTS[flag]})
    return out


def _training_slot(workouts, now):
    """"morgens" | "mittags" | "abends" nach der mittleren Startzeit der letzten Einheiten, sonst None."""
    since = now - timedelta(days=TRAINING_DAYS)
    zone = stats.tz() or timezone.utc
    hours = []
    for w in workouts:
        try:
            at = fasting.parse_ts(w.get("started_at"))
        except (ValueError, TypeError):
            continue
        if since <= at <= now:
            hours.append(at.astimezone(zone).hour)
    if len(hours) < MIN_TRAININGS:
        return None
    mid = sorted(hours)[len(hours) // 2]
    return "morgens" if mid < 11 else "abends" if mid >= 16 else "mittags"


def _window_hint(kind, pref):
    hours = fasting.KINDS.get(kind)
    if not hours or pref not in ("morgens", "abends"):
        return None
    eat = 24 - hours
    start = 8 if pref == "morgens" else 20 - eat
    return f"Dein Wunsch ({pref}): Essensfenster zum Beispiel {start}–{start + eat} Uhr."


def _checkin_warnings(checkins, now):
    since = (now - timedelta(days=CHECKIN_DAYS)).date().isoformat()
    recent = [c for c in checkins or [] if (c.get("date") or "") >= since]
    sleep = [c["sleep"] for c in recent if c.get("sleep")]
    stress = [c["stress"] for c in recent if c.get("stress")]
    out = []
    if len(sleep) >= MIN_CHECKINS and sum(sleep) / len(sleep) <= BAD_SLEEP:
        out.append("Dein Schlaf war zuletzt schlecht. Dann vertragen viele Fasten schlechter: "
                   "lass die Steigerung ruhen und fasten nur so lang, wie es sich gut anfühlt.")
    if len(stress) >= MIN_CHECKINS and sum(stress) / len(stress) >= HIGH_STRESS:
        out.append("Dein Stress war zuletzt hoch. Fasten ist ein zusätzlicher Reiz: halte das "
                   "Fenster lieber moderat und lass ein Fasten aus, wenn es zu viel wird.")
    return out


# ------------------------------------------------------------- Entscheidung
def recommend(profile, weight_kg, history, workouts, now, checkins=None):
    """Regelentscheidung. profile: Zeile aus profiles; history: fasting.history (neueste zuerst);
    workouts: Dicts mit "started_at"; checkins: Dicts mit date/sleep/stress (optional).
    Rueckgabe: {"allowed", "kind", "reasons", "warnings", "blocked", "missing"}; bei allowed=False
    ist kind None, blocked nennt [{"code", "text"}], missing [Feldnamen] (birth_year, height_cm, weight_kg)."""
    blocked = blockers(profile, weight_kg, now)
    missing = [k for k, v in (("birth_year", profile.get("birth_year")), ("height_cm", profile.get("height_cm")),
                              ("weight_kg", weight_kg)) if not v]
    if blocked:
        return {"allowed": False, "kind": None, "reasons": [], "warnings": [], "blocked": blocked,
                "missing": missing}

    reasons, warnings = [], []
    q = quota(history)
    goal, advanced = profile.get("goal"), profile.get("experience") in ADVANCED_EXPERIENCE
    b = bmi(profile, weight_kg)

    if missing:
        kind = "14:10"
        names = ", ".join(MISSING_LABELS[m] for m in missing)
        warnings.append(f"Es fehlen Angaben ({names}), deshalb kann ich nur den Einstieg 14:10 "
                        "empfehlen. Ergänze sie im Profil, um mehr zu bekommen.")
        reasons.append("14:10 ist der sanfte Einstieg: 14 Stunden ohne Essen, 10 Stunden Essensfenster.")
    elif q["hits"] >= STEP_UP_HITS and q["total"] >= STEP_UP_HITS:
        kind = "16:8"
        reasons.append(f"Du hast das Ziel bei {q['hits']} der letzten {q['total']} Fasten erreicht: "
                       "Zeit für den nächsten Schritt, 16:8.")
        long_hits = sum(1 for e in history[:STEP_UP_OF] if e.get("hit") and e.get("target_hours", 0) >= 16)
        if advanced and b >= ADVANCED_MIN_BMI and goal == "abnehmen" and long_hits >= STEP_UP_HITS:
            kind = "18:6"
            reasons = [f"Du hast {long_hits} der letzten {q['total']} Fasten mit mindestens 16 Stunden "
                       "geschafft, bist erfahren, dein BMI liegt über 20 und dein Ziel ist Fettabbau: "
                       "18:6 ist möglich.", EVIDENCE_ADVANCED]
    else:
        kind = "14:10"
        if q["total"] == 0:
            reasons.append("Der Einstieg ist 14:10: 14 Stunden ohne Essen, 10 Stunden Essensfenster.")
        else:
            reasons.append(f"Du hast das Ziel bei {q['hits']} der letzten {q['total']} Fasten erreicht. "
                           f"Für den nächsten Schritt brauchst du mindestens {STEP_UP_HITS} "
                           f"von {STEP_UP_OF}: bleib bei 14:10.")

    if goal == "aufbau":
        reasons.append("Beim Muskelaufbau ist 16:8 die Obergrenze: Ein kürzeres Essensfenster macht es "
                       "schwer, genug Eiweiß und Kalorien zu essen.")
        if fasting.KINDS[kind] > 16:
            kind = "16:8"

    window = _window_hint(kind, profile.get("eating_window_pref"))
    if window:
        reasons.append(window)
    reasons.append("Training: " + TRAINING_HINTS[_training_slot(workouts, now)])

    chosen = fasting.target_hours(profile)
    current = profile.get("fasting_kind")
    if chosen and chosen > fasting.KINDS[kind]:
        warnings.append(f"Deine gewählte Fastenart ({current if current != fasting.CUSTOM else f'{chosen:g} h'})"
                        f" liegt über der Empfehlung {kind}. Du kannst sie behalten – ich empfehle sie nicht.")
    if current in fasting.ADVANCED and kind not in fasting.ADVANCED:
        warnings.append(EVIDENCE_ADVANCED)
    if goal == "aufbau" and chosen and chosen > 16:
        warnings.append("Für den Muskelaufbau ist ein Essensfenster unter 8 Stunden ungünstig: Eiweiß und Kalorien sind schwerer zu erreichen.")
    warnings.extend(_checkin_warnings(checkins, now))
    return {"allowed": True, "kind": kind, "reasons": reasons, "warnings": warnings, "blocked": [],
            "missing": missing}


# -------------------------------------------------------------------- Tipps
def phase_tips(kind):
    """Feste Tipps je Fastenart (Trinken, Elektrolyte, Training, Fasten brechen, Eiweiß):
    {"kind", "tipps": [...], "evidenz": Text oder None}. Unbekannte Arten bekommen nur die allgemeinen."""
    return {"kind": kind, "tipps": list(KIND_TIPS.get(kind, ())) + list(COMMON_TIPS),
            "evidenz": EVIDENCE_ADVANCED if kind in fasting.ADVANCED else None}


def protocols():
    """Alle Fastenarten fuer die Auswahl: Stunden, Essensfenster, fortgeschritten ja/nein, Evidenzhinweis."""
    return [{"kind": k, "hours": h, "eating_hours": 24 - h, "advanced": k in fasting.ADVANCED,
             "evidenz": EVIDENCE_ADVANCED if k in fasting.ADVANCED else None}
            for k, h in fasting.KINDS.items()]


# ------------------------------------------------------------ aus der DB
def evaluate(profile_id, now=None):
    """recommend() mit den Daten dieses Profils."""
    now = now or datetime.now(timezone.utc)
    profile = db.get_row("profiles", profile_id) or {}
    since = (now - timedelta(days=TRAINING_DAYS)).isoformat(timespec="seconds")
    workouts = db.rows("SELECT COALESCE(started_at, finished_at) AS started_at FROM workouts"
                       " WHERE profile_id=? AND COALESCE(started_at, finished_at) >= ?", (profile_id, since))
    checkins = db.rows("SELECT date, sleep, stress FROM checkins WHERE profile_id=? AND date >= ?",
                       (profile_id, (now - timedelta(days=CHECKIN_DAYS)).date().isoformat()))
    return recommend(profile, activity.latest_weight(profile_id), fasting.history(profile_id), workouts,
                     now, checkins)


def start_blockers(profile_id, now=None):
    """Nur die Sperren (billiger als evaluate) – fuer die Start-Sperre in start_fast."""
    now = now or datetime.now(timezone.utc)
    return blockers(db.get_row("profiles", profile_id) or {}, activity.latest_weight(profile_id), now)


# ----------------------------------------------------------------- KI-Text
SYSTEM = (
    "Du erklärst die Entscheidung einer Fasten-Beratung. Die Fastenart wurde bereits von festen "
    "Regeln bestimmt: Du darfst sie nicht ändern, relativieren oder eine andere vorschlagen. "
    "Erkläre in zwei bis drei Sätzen, warum sie zu dieser Person passt, und gib höchstens drei "
    "kurze, konkrete Alltagstipps (Trinken, Elektrolyte, Training im Essensfenster, Fasten brechen, "
    "Eiweiß 1,2–1,6 g je kg Körpergewicht). Keine Zeitversprechen zu Ketose oder Fettverbrennung, "
    "keine Heilversprechen, keine Diagnosen, keine Medikamentenratschläge. Bei 18:6, 20:4 oder OMAD "
    "sagst du ehrlich, dass die Studienlage dünn ist. Keine Zahlen erfinden. "
    'Antworte NUR mit JSON ohne Text davor oder danach: {"erklaerung": "...", "tipps": ["..."]}. '
    f"Jeder Tipp ein Satz mit höchstens 25 Wörtern auf Deutsch (du-Form), höchstens {MAX_TIPS} Tipps."
)


class Busy(Exception):
    """Es laeuft schon eine Anfrage an die KI."""


_busy = threading.Lock()


def prompt(profile, weight_kg, history, rec, now):
    """Text an die KI: Regelausgabe plus Ziel, Alter, BMI-Klasse und Quote. Kein Name, keine Tage."""
    a = age(profile, now)
    q = quota(history)
    lines = [f"Regelentscheidung (verbindlich): Fastenart {rec['kind']}.",
             "Gründe:", *[f"- {r}" for r in rec["reasons"]]]
    if rec["warnings"]:
        lines += ["Warnungen:", *[f"- {w}" for w in rec["warnings"]]]
    lines.append(f"Ziel: {profile.get('goal') or 'unbekannt'}; Alter: "
                 f"{a if a is not None else 'unbekannt'}; BMI-Klasse: {bmi_class(bmi(profile, weight_kg))}; "
                 f"Quote: {q['hits']} von {q['total']} letzten Fasten im Ziel; "
                 f"Essensfenster-Wunsch: {profile.get('eating_window_pref') or 'keine Angabe'}.")
    return "\n".join(lines)


def _cut(text, limit):
    text = str(text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return head[:end + 1] if end > limit // 2 else head


def parse(text, kind):
    """{"kind", "erklaerung", "tipps"}; ohne Erklaerung ValueError, ohne Tipps die festen
    Standardtipps der Fastenart."""
    data = ai._json_block(text)
    explain = data.get("erklaerung") if isinstance(data, dict) else None
    if not isinstance(explain, str) or not explain.strip():
        raise ValueError("keine Erklärung")
    tips = data.get("tipps")
    tips = [_cut(t, MAX_CHARS) for t in tips if isinstance(t, str) and t.strip()][:MAX_TIPS] \
        if isinstance(tips, list) else []
    return {"kind": kind, "erklaerung": _cut(explain, MAX_EXPLAIN_CHARS),
            "tipps": tips or phase_tips(kind)["tipps"][:MAX_TIPS]}


def _key(text):
    return hashlib.sha256(f"{MODEL}\n{text}".encode()).hexdigest()


def _cached(profile_id, key, now):
    row = db.one("SELECT body FROM fasting_advice WHERE profile_id=? AND input_hash=? AND created_at >= ?",
                 (profile_id, key, (now - timedelta(hours=CACHE_HOURS)).isoformat(timespec="seconds")))
    return json.loads(row["body"]) if row else None


def _store(profile_id, key, body, now):
    cutoff = (now - timedelta(hours=CACHE_HOURS)).isoformat(timespec="seconds")
    with db.connect() as c:
        c.execute("DELETE FROM fasting_advice WHERE profile_id=? AND created_at < ?", (profile_id, cutoff))
        c.execute("INSERT OR REPLACE INTO fasting_advice (profile_id, input_hash, body, created_at)"
                  " VALUES (?,?,?,?)", (profile_id, key, json.dumps(body, ensure_ascii=False),
                                        now.isoformat(timespec="seconds")))


def blocked_answer(rec):
    """Antwort bei Sperre – feste Texte, kein KI-Aufruf."""
    return {"kind": None, "erklaerung": " ".join(b["text"] for b in rec["blocked"]) + " " + DISCLAIMER,
            "tipps": [], "cached": False}


def advise(profile_id, rec, now=None):
    """Erklaerung und Tipps zur Regelentscheidung `rec` (allowed=True) oder {"error": ...}.
    Wirft Busy, wenn schon eine Anfrage laeuft. Gleiche Eingabe innerhalb von 24 h kommt aus der DB."""
    now = now or datetime.now(timezone.utc)
    profile = db.get_row("profiles", profile_id) or {}
    text = prompt(profile, activity.latest_weight(profile_id), fasting.history(profile_id), rec, now)
    key = _key(text)
    hit = _cached(profile_id, key, now)
    if hit:
        return {**hit, "cached": True}
    if not _busy.acquire(blocking=False):
        raise Busy()
    try:
        try:
            out = parse(claude_cli.ask(text, SYSTEM, MODEL, TIMEOUT), rec["kind"])
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            log.warning("Fasten-Tipps fehlgeschlagen: %s", e)
            return {"error": "Die Tipps sind gerade nicht erreichbar – bitte später nochmal versuchen."}
        except (ValueError, json.JSONDecodeError):
            return {"error": "Die Tipps konnten nicht gelesen werden – bitte nochmal versuchen."}
        _store(profile_id, key, out, now)
        return {**out, "cached": False}
    finally:
        _busy.release()
