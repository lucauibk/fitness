"""Taeglicher Coach-Lauf: pro Profil Plan + letzte Trainings an Claude Code geben und die
Vorschlaege als Hinweise in der App ablegen. Aendert keine Plaene.

    PYTHONPATH=. .venv/bin/python scripts/coach_daily.py [--dry-run]

Auf dem Pi startet ihn der systemd-Timer fitness-coach.timer. Claude laeuft ohne Werkzeuge
(--tools "") in einem leeren Verzeichnis und kann daher nur Text zurueckgeben.
Anmeldung: CLAUDE_CODE_OAUTH_TOKEN in .env (einmalig per `claude setup-token`)."""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from app import coach, db

log = logging.getLogger("coach")
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5-5")
TIMEOUT = 300
ATTEMPTS = 2
RETRY_PAUSE = 30


def ask_claude(text):
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    with tempfile.TemporaryDirectory() as cwd:
        res = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", MODEL, "--tools", "",
             "--system-prompt", coach.SYSTEM, "--strict-mcp-config", "--no-session-persistence"],
            input=text, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)
    if res.returncode != 0:
        # Claude schreibt Fehler oft nach stdout (JSON), nicht nach stderr
        detail = (res.stderr.strip() or res.stdout.strip())[:300]
        raise RuntimeError(f"claude Exit {res.returncode}: {detail}")
    out = json.loads(res.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude meldet Fehler: {str(out.get('result'))[:300]}")
    return out.get("result", "")


def ask_with_retry(text, attempts=ATTEMPTS):
    """Ueberlastung oder Netz-Haenger sind meist nach kurzer Pause vorbei."""
    for i in range(1, attempts + 1):
        try:
            return ask_claude(text)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            if i == attempts:
                raise
            log.warning("Versuch %d fehlgeschlagen (%s), neuer Versuch in %ds", i, e, RETRY_PAUSE)
            time.sleep(RETRY_PAUSE)


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
            raw = ask_with_retry(coach.prompt(ctx))
            result = coach.parse(raw, pid)
        except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            log.error("Profil %s: %s", pid, e)
            failed += 1
            continue
        if dry_run:
            analyse = re.search(r"<analyse>(.*?)</analyse>", raw, re.S)
            print(f"--- Profil {pid}\n{analyse.group(1).strip() if analyse else '(keine Analyse)'}")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            continue
        coach.resolve(pid, result["resolved"])
        coach.expire(pid)
        coach.store(pid, result["notes"])
        log.info("Profil %s: %d Hinweis(e) abgelegt, %d erledigt", pid,
                 len(result["notes"]), len(result["resolved"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
