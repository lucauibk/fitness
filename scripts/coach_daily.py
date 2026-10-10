"""Taeglicher Coach-Lauf: pro Profil Plan + letzte Trainings an Claude Code geben und die
Vorschlaege als Hinweise in der App ablegen. Aendert keine Plaene.

    PYTHONPATH=. .venv/bin/python scripts/coach_daily.py [--dry-run]

Auf dem Pi startet ihn der systemd-Timer fitness-coach.timer. Claude laeuft ohne Werkzeuge
(--tools "") in einem leeren Verzeichnis und kann daher nur Text zurueckgeben.
Anmeldung: CLAUDE_CODE_OAUTH_TOKEN in .env (einmalig per `claude setup-token`)."""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile

from app import coach, db

log = logging.getLogger("coach")
MODEL = os.environ.get("COACH_MODEL", "claude-sonnet-5-5")
TIMEOUT = 300


def ask_claude(text):
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    with tempfile.TemporaryDirectory() as cwd:
        res = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", MODEL, "--tools", "",
             "--strict-mcp-config", "--no-session-persistence"],
            input=text, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError(f"claude Exit {res.returncode}: {res.stderr.strip()[:300]}")
    out = json.loads(res.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude meldet Fehler: {str(out.get('result'))[:300]}")
    return out.get("result", "")


def main(dry_run=False):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='coach_notes'"):
        log.error("Tabelle coach_notes fehlt – die App einmal starten, sie legt sie an.")
        return 1
    failed = 0
    for p in db.rows("SELECT id FROM profiles ORDER BY id"):
        pid = p["id"]
        ctx = coach.context(pid)
        if not ctx:
            log.info("Profil %s: kein Plan, übersprungen", pid)
            continue
        try:
            notes = coach.parse(ask_claude(coach.prompt(ctx)), pid)
        except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            log.error("Profil %s: %s", pid, e)
            failed += 1
            continue
        if dry_run:
            print(json.dumps(notes, ensure_ascii=False, indent=2))
            continue
        coach.expire(pid)
        coach.store(pid, notes)
        log.info("Profil %s: %d Hinweis(e) abgelegt", pid, len(notes))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
