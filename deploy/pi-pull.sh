#!/usr/bin/env bash
# Deploy direkt auf dem Pi: holt den neuesten Stand von GitHub (main) und startet neu.
# Laeuft alle 5 Minuten per systemd-Timer (fitness-update.timer); macht nichts, wenn sich
# auf main nichts getan hat. Daten (fitness.db, backups/) und .env bleiben unangetastet,
# sie sind in .gitignore. Antwortet der Dienst nach dem Update nicht, geht es zurueck auf
# den vorherigen Stand.
#
# Einmalig einrichten (auf dem Pi, auch wenn ~/fitness noch per rsync kam):
#   curl -fsSL https://raw.githubusercontent.com/lucauibk/fitness/main/deploy/pi-pull.sh | bash
# Danach von Hand sofort aktualisieren:
#   ~/fitness/deploy/pi-pull.sh          (oder: systemctl --user start fitness-update)
#   ~/fitness/deploy/pi-pull.sh --force  (auch ohne neuen Commit neu starten)
set -euo pipefail

REPO="${FITNESS_REPO:-https://github.com/lucauibk/fitness.git}"
BRANCH="${FITNESS_BRANCH:-main}"
DIR="${FITNESS_DIR:-$HOME/fitness}"
PORT=8001
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

log() { echo "[fitness-update] $*"; }
command -v git >/dev/null || { log "git fehlt: sudo apt install -y git"; exit 1; }

# Nur ein Lauf gleichzeitig (Timer und Handaufruf)
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/fitness-update.lock"
flock -n 9 || { log "laeuft schon"; exit 0; }

mkdir -p "$DIR"
cd "$DIR"

# Verzeichnis, das bisher per rsync vom Mac kam: zu einem Git-Checkout machen.
# reset --hard faesst nur versionierte Dateien an; fitness.db, .env, .venv bleiben.
if [ ! -d .git ]; then
  log "richte Git-Checkout in $DIR ein"
  git init -q
  git remote add origin "$REPO"
  git fetch -q --depth 50 origin "$BRANCH"
  git reset -q --hard "origin/$BRANCH"
  git branch -q -M "$BRANCH"
  FORCE=1
fi

git fetch -q --depth 50 origin "$BRANCH"
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"
# Nur bei neuem Commit. Lokale Aenderungen (z. B. per pi-deploy.sh vom Mac) bleiben bis dahin.
[ "$OLD" = "$NEW" ] && [ "$FORCE" = 0 ] && exit 0
# Ein Stand, der schon einmal nicht gestartet ist, wird nicht alle 5 Minuten neu probiert.
FAILED=.git/fitness-failed
if [ "$FORCE" = 0 ] && [ -f "$FAILED" ] && [ "$(cat "$FAILED")" = "$NEW" ]; then
  exit 0
fi

REQ_CHANGED=1
[ "$OLD" != "$NEW" ] && git diff --quiet "$OLD" "$NEW" -- requirements.txt && REQ_CHANGED=0
[ -d .venv ] || REQ_CHANGED=1

restart() {
  [ -f .env ] || cp .env.example .env
  [ -d .venv ] || python3 -m venv .venv
  [ "$REQ_CHANGED" = 1 ] && .venv/bin/pip install -q -r requirements.txt
  mkdir -p ~/.config/systemd/user
  cp deploy/fitness.user.service ~/.config/systemd/user/fitness.service
  cp deploy/fitness-coach.service deploy/fitness-coach.timer \
     deploy/fitness-update.service deploy/fitness-update.timer ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable fitness >/dev/null 2>&1 || true
  systemctl --user enable --now fitness-coach.timer fitness-update.timer >/dev/null 2>&1 || true
  systemctl --user restart fitness
  for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

log "$(git log -1 --format='%h %s' "$OLD" 2>/dev/null || echo '-') → $(git log -1 --format='%h %s' "$NEW")"
git reset -q --hard "$NEW"
if restart; then
  rm -f "$FAILED"
  log "✓ läuft ($(git log -1 --format='%h %s'))"
  exit 0
fi

log "✗ Dienst antwortet nicht, zurück auf ${OLD:0:7}"
journalctl --user -u fitness -n 30 --no-pager || true
if [ "$OLD" != "$NEW" ]; then
  echo "$NEW" > "$FAILED"
  git reset -q --hard "$OLD"
  REQ_CHANGED=1
  restart && log "✓ alter Stand läuft wieder" || log "✗ auch der alte Stand startet nicht"
fi
exit 1
