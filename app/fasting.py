"""Intervallfasten: Zielstunden je Fastenart, Verlauf, Serie.

Alle Zeiten liegen als UTC-ISO in der DB. Dauern rechnen wir nur aus UTC-Differenzen (so stimmt
sie auch an Tagen der Zeitumstellung), den Kalendertag eines Fastens liefert stats._local_date
(ein Fasten ueber Mitternacht zaehlt fuer den Tag, an dem es endet)."""
from datetime import datetime, timedelta, timezone

from . import db, stats

# Feste Fastenarten -> Stunden ohne Essen. "custom" nimmt profiles.fasting_hours.
KINDS = {"14:10": 14, "16:8": 16, "18:6": 18}
CUSTOM = "custom"
MAX_HOURS = 48          # Obergrenze fuer die eigene Stundenzahl
MAX_PAST = timedelta(hours=72)     # aelter darf eine nachgetragene Startzeit nicht sein
CLOCK_SKEW = timedelta(minutes=1)  # Toleranz fuer Geraeteuhren, die leicht vorgehen
HISTORY = 14


def parse_ts(value):
    """ISO-String -> UTC-datetime (aware). Ohne Zeitzone gilt UTC. ValueError bei Unsinn."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def target_hours(profile):
    """Zielstunden des Profils, None = Fasten aus."""
    kind = profile.get("fasting_kind")
    if kind == CUSTOM:
        return profile.get("fasting_hours")
    return KINDS.get(kind)


def hours_between(start, end):
    return (parse_ts(end) - parse_ts(start)).total_seconds() / 3600


def running(profile_id):
    return db.one("SELECT * FROM fasts WHERE profile_id=? AND ended_at IS NULL", (profile_id,))


def history(profile_id, limit=HISTORY):
    """Beendete Fasten, neueste zuerst, mit Dauer, Ziel und Tag des Endes (Ortszeit)."""
    out = []
    for r in db.rows("SELECT * FROM fasts WHERE profile_id=? AND ended_at IS NOT NULL"
                     " ORDER BY ended_at DESC, id DESC LIMIT ?", (profile_id, limit)):
        hours = hours_between(r["started_at"], r["ended_at"])
        out.append({**r, "hours": round(hours, 2),
                    "hit": hours >= r["target_hours"],
                    "day": stats._local_date(r["ended_at"]).isoformat()})
    return out


def streak(entries, today):
    """Aufeinanderfolgende Kalendertage (Ende des Fastens) mit erreichtem Ziel. Mehrere Fasten
    an einem Tag zaehlen einmal. Ein Tag ohne Treffer bricht die Serie, ausser es ist heute und
    das Fasten laeuft noch – dann zaehlt die Serie bis gestern."""
    hit_days = {e["day"] for e in entries if e["hit"]}
    day = today if today.isoformat() in hit_days else today - timedelta(days=1)
    n = 0
    while day.isoformat() in hit_days:
        n += 1
        day -= timedelta(days=1)
    return n


def hint(profile_id, current):
    """Hinweis, wenn waehrend des laufenden Fastens trainiert wurde: die Eiweissmenge bleibt,
    muss aber ins Essensfenster passen."""
    if not current:
        return None
    w = db.one("SELECT id FROM workouts WHERE profile_id=? AND started_at >= ? LIMIT 1",
               (profile_id, current["started_at"]))
    if w:
        return ("Du hast im Fasten trainiert. Plane das Eiweiß direkt danach oder im Essensfenster ein "
                "– die Tagesmenge bleibt gleich.")
    return None


def overview(profile_id, profile):
    current = running(profile_id)
    entries = history(profile_id)
    last_end = entries[0]["ended_at"] if entries else None
    return {"kind": profile.get("fasting_kind"), "target_hours": target_hours(profile),
            "running": current, "history": entries, "last_ended_at": None if current else last_end,
            "streak": streak(entries, stats.today()), "hint": hint(profile_id, current)}
