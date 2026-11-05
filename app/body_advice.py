"""KI-Tipps zur Koerpermessung ueber Claude Code (`claude -p`, wie der Coach – kein API-Schluessel).

Verschickt werden nur Ziel, Geschlecht, Groesse und die Trend-Zahlen aus body.trend():
keine Namen, keine einzelnen Messtage. Ohne Werkzeuge, nur Text."""
import json
import logging
import os
import subprocess

from . import ai, claude_cli

log = logging.getLogger(__name__)
MODEL = os.environ.get("TIPS_MODEL", "claude-sonnet-5-5")
TIMEOUT = 90
SECTIONS = {"gut": "Was gut läuft", "schlecht": "Was nicht gut läuft",
            "verbessern": "Was du verbessern kannst"}
MAX_ITEMS = 2
MAX_CHARS = 220

SYSTEM = (
    "Du ordnest die Körpermessungen einer Person ein. Sie misst bewusst selten (höchstens einmal "
    "pro Woche, oft nur alle paar Wochen). Beurteile nur, was die Zahlen hergeben: Richtung und "
    "Größe der Veränderung seit der letzten Messung und über den Verlauf, ob Gewicht, Körperfett, "
    "Muskelmasse und Umfänge zusammenpassen (Gewicht und Taille sinken bei stabiler Muskelmasse = "
    "gutes Zeichen) und ob sie zum Ziel passt. Körperfett und Muskelmasse aus Impedanzwaagen sind "
    "ungenau (Muskelmasse etwa ±1 kg, Körperfett etwa ±2 Prozentpunkte): Veränderungen darunter "
    "sind im Rahmen der Messungenauigkeit und keine Aussage. Bei nur zwei Messungen beschreibst du "
    "nur, ohne zu werten. "
    "Gib keine Tipps zum Messen (Uhrzeit, Technik, Häufigkeit) und fordere nie auf, öfter zu "
    "messen. Gib keine Tipps zu Training, Ernährung, Schlaf, Supplements oder Medikamenten und "
    "keine Diagnosen. Unter \"verbessern\" steht höchstens ein Hinweis, was die Messwerte selbst "
    "nahelegen (z. B. dass der Trend passt und nichts nötig ist, oder worauf bei der nächsten "
    "Messung zu achten ist). Keine Zahlen erfinden. "
    "Antworte NUR mit JSON ohne Text davor oder danach: "
    '{"gut": ["..."], "schlecht": ["..."], "verbessern": ["..."]}. '
    f"Je Liste höchstens {MAX_ITEMS} Einträge, jeder Eintrag ein einziger Satz mit höchstens 25 Wörtern "
    "auf Deutsch (du-Form), ohne Klammern und Aufzählungen. Lass eine Liste leer, wenn es nichts "
    "Sinnvolles zu sagen gibt; lieber wenig als Füllsätze."
)


def _signed(v):
    return f"{v:+.1f}".replace(".", ",")


def prompt(profile, trend, latest, labels):
    """Der Text an die KI, oder None, wenn es nichts zu bewerten gibt."""
    lines = []
    for key, m in trend.get("metrics", {}).items():
        parts = [f"aktuell {latest[key]['value']}"]
        if m["since_last"]:
            parts.append(f"seit der letzten Messung ({m['since_last']['days']} Tage her) "
                         f"{_signed(m['since_last']['delta'])}")
        if m["change"]:
            parts.append(f"Verlauf über {m['change']['weeks']} Wochen {_signed(m['change']['delta'])}")
        if m["noisy"]:
            parts.append("Waage ungenau")
        if m["stale"]:
            parts.append("lange nicht gemessen")
        lines.append(f"- {labels[key]}: {', '.join(parts)}")
    if not lines:
        return None
    verdict = trend.get("verdict")
    return (f"Ziel: {profile.get('goal') or 'fitness'}; Geschlecht: {profile.get('sex') or 'unbekannt'}; "
            f"Größe: {profile.get('height_cm') or 'unbekannt'} cm.\n"
            + (f"Regelbasierte Einordnung: {verdict['text']}\n" if verdict else "")
            + "Zahlen:\n" + "\n".join(lines))


def _cut(text):
    """Auf MAX_CHARS kuerzen, wenn moeglich am letzten Satzende statt mitten im Satz."""
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return text
    head = text[:MAX_CHARS]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return head[:end + 1] if end > MAX_CHARS // 2 else head


def parse(text):
    """{"gut": [...], "schlecht": [...], "verbessern": [...]}, begrenzt und bereinigt; ValueError bei Leere."""
    data = ai._json_block(text)
    out = {}
    for key in SECTIONS:
        items = data.get(key) if isinstance(data, dict) else None
        items = items if isinstance(items, list) else []
        out[key] = [_cut(t) for t in items if isinstance(t, str) and t.strip()][:MAX_ITEMS]
    if not any(out.values()):
        raise ValueError("keine Tipps")
    return out


def generate(profile, trend, latest, labels):
    """Tipps oder {"error": ...}. Laeuft synchron (Aufruf aus einer normalen Route)."""
    text = prompt(profile, trend, latest, labels)
    if text is None:
        return {"error": "Es gibt noch keine Messungen für Tipps."}
    try:
        return parse(claude_cli.ask(text, SYSTEM, MODEL, TIMEOUT))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        log.warning("Körper-Tipps fehlgeschlagen: %s", e)
        return {"error": "Die Tipps sind gerade nicht erreichbar – bitte später nochmal versuchen."}
    except (ValueError, json.JSONDecodeError):
        return {"error": "Die Tipps konnten nicht gelesen werden – bitte nochmal versuchen."}
