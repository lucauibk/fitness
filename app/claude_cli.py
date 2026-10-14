"""Claude Code als Kommandozeile – fuer den taeglichen Coach und die Auswertung nach dem Training.

Claude laeuft ohne Werkzeuge (--tools "") in einem leeren Verzeichnis und kann daher nur Text
zurueckgeben. Anmeldung: CLAUDE_CODE_OAUTH_TOKEN in .env (einmalig per `claude setup-token`)."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time

log = logging.getLogger(__name__)
RETRY_PAUSE = 30


def ask(text, system, model, timeout=300):
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    with tempfile.TemporaryDirectory() as cwd:
        res = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", model, "--tools", "",
             "--system-prompt", system, "--strict-mcp-config", "--no-session-persistence"],
            input=text, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if res.returncode != 0:
        # Claude schreibt Fehler oft nach stdout (JSON), nicht nach stderr
        detail = (res.stderr.strip() or res.stdout.strip())[:300]
        raise RuntimeError(f"claude Exit {res.returncode}: {detail}")
    out = json.loads(res.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude meldet Fehler: {str(out.get('result'))[:300]}")
    return out.get("result", "")


def ask_with_retry(text, system, model, timeout=300, attempts=2, pause=RETRY_PAUSE):
    """Ueberlastung oder Netz-Haenger sind meist nach kurzer Pause vorbei."""
    for i in range(1, attempts + 1):
        try:
            return ask(text, system, model, timeout)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            if i == attempts:
                raise
            log.warning("Versuch %d fehlgeschlagen (%s), neuer Versuch in %ds", i, e, pause)
            time.sleep(pause)
