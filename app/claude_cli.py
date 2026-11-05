"""Claude Code als Kommandozeile – fuer den taeglichen Coach, die Auswertung nach dem Training
und den Coach-Chat.

Claude laeuft ohne Werkzeuge (--tools "") in einem leeren Verzeichnis und kann daher nur Text
zurueckgeben. Anmeldung: CLAUDE_CODE_OAUTH_TOKEN in .env (einmalig per `claude setup-token`),
also ueber das Claude-Abo. ANTHROPIC_API_KEY wird bewusst NICHT weitergereicht: Die CLI zieht
einen API-Schluessel dem Abo-Token vor, und ohne API-Guthaben scheitert dann jeder Lauf mit
"Credit balance is too low" (so am 15.10.2026 passiert). Der Schluessel bleibt fuer app/ai.py."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

log = logging.getLogger(__name__)
RETRY_PAUSE = 30


def env():
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def command(system, model, *extra):
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return [exe, "-p", "--model", model, "--tools", "", "--system-prompt", system,
            "--strict-mcp-config", "--no-session-persistence", *extra]


def error_text(stdout, stderr=""):
    """Lesbare Fehlermeldung: Claude schreibt Fehler oft als JSON nach stdout. Das Feld result
    traegt die eigentliche Ursache ("Credit balance is too low", Limit erreicht, ...)."""
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            out = json.loads(line)
        except ValueError:
            continue
        if isinstance(out, dict) and out.get("type", "result") == "result":
            return str(out.get("result") or out.get("subtype") or "unbekannter Fehler")[:300]
    return ((stderr or "").strip() or (stdout or "").strip() or "keine Ausgabe")[:300]


def ask(text, system, model, timeout=300):
    with tempfile.TemporaryDirectory() as cwd:
        res = subprocess.run(command(system, model, "--output-format", "json"),
                             input=text, capture_output=True, text=True, timeout=timeout,
                             cwd=cwd, env=env())
    if res.returncode != 0:
        raise RuntimeError(f"claude Exit {res.returncode}: {error_text(res.stdout, res.stderr)}")
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


def _image_message(text, images):
    """Eine Nutzer-Nachricht mit Bildern fuer --input-format stream-json.
    images: [(media_type, base64)] – so kommen Fotos ohne Datei-Werkzeug zu Claude."""
    content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
               for mt, data in images]
    content.append({"type": "text", "text": text})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


def stream(text, system, model, timeout=120, images=None):
    """Antwort Stueck fuer Stueck: liefert ("delta", text) und am Ende ("done", volltext).
    Fehler kommen als RuntimeError mit lesbarer Meldung. images: siehe _image_message."""
    cwd = tempfile.mkdtemp()
    # stderr in eine Datei statt einer Pipe: eine volle, ungelesene Pipe wuerde die CLI blockieren
    err = tempfile.TemporaryFile(mode="w+")
    extra = ("--input-format", "stream-json") if images else ()
    proc = subprocess.Popen(command(system, model, "--output-format", "stream-json", "--verbose",
                                    "--include-partial-messages", *extra),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                            text=True, cwd=cwd, env=env())
    # Haengt die CLI ohne Ausgabe, kaeme die Leseschleife nie zum Pruefen – der Timer beendet
    # den Prozess, stdout schliesst und die Schleife endet.
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    lines = []
    try:
        proc.stdin.write(_image_message(text, images) if images else text)
        proc.stdin.close()
        for line in proc.stdout:
            lines.append(line)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "stream_event":
                delta = (ev.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield "delta", delta["text"]
            elif ev.get("type") == "result":
                if ev.get("is_error"):
                    raise RuntimeError(str(ev.get("result") or ev.get("subtype"))[:300])
                yield "done", ev.get("result") or ""
                return
        proc.wait(timeout=5)
        if not timer.is_alive():
            raise RuntimeError("Der Coach hat zu lange gebraucht.")
        err.seek(0)
        raise RuntimeError(f"claude Exit {proc.returncode}: {error_text(''.join(lines), err.read())}")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        err.close()
        shutil.rmtree(cwd, ignore_errors=True)


def complete(text, system, model, timeout=120, images=None):
    """Ganze Antwort auf einmal (ueber stream, damit Bilder und Zeitlimit gleich funktionieren)."""
    for kind, value in stream(text, system, model, timeout, images):
        if kind == "done":
            return value
    raise RuntimeError("Keine Antwort erhalten.")
