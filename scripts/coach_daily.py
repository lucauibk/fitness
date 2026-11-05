"""Taeglicher Coach-Lauf: pro Profil Plan + letzte Trainings an Claude Code geben und
hoechstens eine Nachricht in den Coach-Chat schreiben. Aendert keine Plaene.

    PYTHONPATH=. .venv/bin/python scripts/coach_daily.py [--dry-run]

Auf dem Pi startet ihn der systemd-Timer fitness-coach.timer. Claude laeuft ohne Werkzeuge
(--tools "") in einem leeren Verzeichnis und kann daher nur Text zurueckgeben.
Anmeldung: CLAUDE_CODE_OAUTH_TOKEN in .env (einmalig per `claude setup-token`)."""
import json
import logging
import os
import re
import subprocess
import sys

from app import claude_cli, coach, coach_chat, db

log = logging.getLogger("coach")
MODEL = os.environ.get("COACH_MODEL", "claude-opus-5-5")
TIMEOUT = 300


def main(dry_run=False):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='coach_messages'"):
        log.error("Tabelle coach_messages fehlt – die App einmal starten, sie legt sie an.")
        return 1
    failed = 0
    for p in db.rows("SELECT id FROM profiles ORDER BY id"):
        pid = p["id"]
        ctx = coach.context(pid)
        if not ctx:
            log.info("Profil %s: kein Plan, übersprungen", pid)
            continue
        try:
            raw = claude_cli.ask_with_retry(coach.prompt(ctx), coach.SYSTEM, MODEL, TIMEOUT)
            result = coach.parse(raw)
        except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            log.error("Profil %s: %s", pid, e)
            failed += 1
            continue
        if dry_run:
            analyse = re.search(r"<analyse>(.*?)</analyse>", raw, re.S)
            print(f"--- Profil {pid}\n{analyse.group(1).strip() if analyse else '(keine Analyse)'}")
            print(f"Impuls: {result['impuls']}\nNachricht: {result['message'] or '(keine)'}")
            print(f"Vorschläge: {coach_chat.validate_actions(result['aktionen'], pid)}")
            continue
        if result["impuls"]:
            coach_chat.set_impulse(pid, result["impuls"])
        if result["message"]:
            coach_chat.add_message(pid, "coach", result["message"], source="daily",
                                   actions=coach_chat.validate_actions(result["aktionen"], pid))
        log.info("Profil %s: Impuls %s, %s", pid, "ja" if result["impuls"] else "fehlt",
                 "Nachricht geschrieben" if result["message"] else "nichts Neues")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
