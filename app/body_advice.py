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
MAX_ITEMS = 3
MAX_CHARS = 320

SYSTEM = (
    "Du gibst Tipps ausschließlich zu den Körpermessungen einer Person: Wie entwickeln sich die "
    "Werte, passt das zum Ziel, wie belastbar sind die Messungen, wie misst man besser (gleiche "
    "Tageszeit, gleiche Bedingungen, sinnvoller Messabstand, fehlende Werte wie Umfänge). "
    "Einzelmessungen schwanken; Körperfett und Muskelmasse aus Impedanzwaagen sind nur Tendenzen. "
    "Du bist vorsichtig und ehrlich. Antworte NUR mit JSON ohne Text davor oder danach: "
    '{"gut": ["..."], "schlecht": ["..."], "verbessern": ["..."]}. '
    f"Regeln: je höchstens {MAX_ITEMS} kurze Sätze auf Deutsch (du-Form), jeder konkret und aus den "
    "Zahlen begründet. Keine Zahlen erfinden, keine Diagnosen, keine Supplement- oder "
    "Medikamentenempfehlungen. Keine Tipps zu Training, Ernährung oder Schlaf – nur zu den "
    "Messwerten und zum Messen selbst. Bei dünner Datenlage ehrlich sagen, dass ein Urteil noch zu "
    "früh ist. Wenn nichts schlecht läuft, lass die Liste leer."
)


def _signed(v):
    return f"{v:+.1f}".replace(".", ",")


def prompt(profile, trend, latest, labels):
    """Der Text an die KI, oder None, wenn es nichts zu bewerten gibt."""
    lines = []
    for key, m in trend.get("metrics", {}).items():
        parts = [f"aktuell {latest[key]['value']}, geglättet {m['smoothed']}"]
        for label, val in (("seit letzter Messung", (m["since_last"] or {}).get("delta")),
                           ("4 Wochen", m["w4"]), ("12 Wochen", m["w12"])):
            if val is not None:
                parts.append(f"{label} {_signed(val)}")
        if m["noisy"]:
            parts.append("Waage ungenau, nur Tendenz")
        if m["stale"]:
            parts.append("lange nicht gemessen")
        lines.append(f"- {labels[key]}: {', '.join(parts)}")
    if not lines:
        return None
    verdict = trend.get("verdict")
    return (f"Ziel: {profile.get('goal') or 'fitness'}; Geschlecht: {profile.get('sex') or 'unbekannt'}; "
            f"Größe: {profile.get('height_cm') or 'unbekannt'} cm.\n"
            + (f"Regelbasierte Einordnung: {verdict['text']}\n" if verdict else "")
            + "Zahlen (Änderungen als Differenz des geglätteten Werts):\n" + "\n".join(lines))


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
