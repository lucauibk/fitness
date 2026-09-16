#!/usr/bin/env bash
# Spielt den aktuellen Stand vom Mac per rsync (ueber Tailscale) auf den Pi und startet neu.
# Daten (fitness.db) und Einstellungen (.env) bleiben auf dem Pi unangetastet.
#
#   ./deploy/pi-deploy.sh            # Host "pi@raspberrypi"
#   PI_HOST=pi@anderer ./deploy/pi-deploy.sh
set -euo pipefail

HOST="${PI_HOST:-pi@raspberrypi}"
DIR="fitness"
PORT=8001
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Code nach $HOST:~/$DIR"
rsync -az --delete \
  --exclude '.venv/' --exclude '*.db' --exclude '*.db-journal' --exclude '*.db-wal' \
  --exclude '*.db-shm' --exclude '*.db.*' --exclude 'backups/' \
  --exclude '.env' --exclude '__pycache__/' --exclude '.DS_Store' --exclude '.git/' \
  "$ROOT/" "$HOST:$DIR/"

COMMIT="$(git -C "$ROOT" log -1 --format='%h %s' 2>/dev/null || true)"
[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ] && COMMIT="$COMMIT (+ lokale Änderungen)"

echo "→ Abhängigkeiten und Neustart"
ssh "$HOST" bash -s <<EOSSH
set -euo pipefail
cd ~/$DIR
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p ~/.config/systemd/user
cp deploy/fitness.user.service ~/.config/systemd/user/fitness.service
systemctl --user daemon-reload
systemctl --user enable fitness >/dev/null 2>&1 || true
systemctl --user restart fitness
for i in \$(seq 1 30); do
  curl -fsS http://127.0.0.1:$PORT/api/status >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS http://127.0.0.1:$PORT/api/status >/dev/null; then
  echo "✓ läuft ($COMMIT)"
else
  echo "✗ Dienst antwortet nicht – letzte Logzeilen:"
  journalctl --user -u fitness -n 30 --no-pager || true
  exit 1
fi
EOSSH
