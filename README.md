# Fitness

Trainingsplan, Einheiten mitschreiben und Fortschritt – als eigener kleiner Dienst neben
dem Studienplaner auf dem Homeserver. Läuft auch ohne Verbindung: der Trainingsplan bleibt
sichtbar, Einheiten werden lokal gespeichert und gesammelt nachgesendet, sobald wieder
Netz da ist.

## Start (lokal)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --port 8001
```

## Deploy auf den Pi

```
./deploy/pi-deploy.sh
```

Danach `tailscale serve --bg --https=8443 http://127.0.0.1:8001` einmalig einrichten
(Bestand prüfen mit `tailscale serve status`) → erreichbar unter
`https://raspberrypi.<dein-tailnet>.ts.net:8443`.

## Auf dem Homeserver eintragen

In `~/homeserver/homeserver.py` bei `SERVICES` ergänzen (alle drei Aktionen nötig):

```python
"fitness": {
    "name": "Fitness", "desc": "Trainingsplan und Fortschritt",
    "url": "https://raspberrypi.<tailnet>.ts.net:8443",
    "local": "http://127.0.0.1:8001", "check": "http://127.0.0.1:8001/api/status",
    "actions": {
        "start": ["systemctl", "--user", "start", "fitness"],
        "stop": ["systemctl", "--user", "stop", "fitness"],
        "restart": ["systemctl", "--user", "restart", "fitness"],
    },
},
```

In `~/homeserver/www/view.html` die Port-Whitelist um `8001` erweitern
(`/^http:\/\/(127\.0\.0\.1|localhost):(8000|5001|8001)\/?$/`), damit das Pi-Display die
App zeigen kann.

## Profile

Kein Login – ein Umschalter oben rechts. Jedes Profil hat eigene Splits, Einheiten und
Auswertungen; der Übungs-Katalog ist gemeinsam. Für eine zweite Person: Tailnet-Zugang
freigeben (z. B. `tailscale share` bzw. die Person ins Tailnet einladen), dann auf ihrem
Handy die Adresse öffnen und „Zum Home-Bildschirm hinzufügen" – eigenes Icon, eigenes Profil.

## Aufbau

- `app/db.py` – SQLite (WAL), Profile/Übungen/Splits/Einheiten/Sätze/Körpergewicht.
- `app/stats.py` – Bestleistungen (Epley-1RM), Wochenvolumen, Häufigkeit, Zielvorschläge
  nach doppelter Progression (Wiederholungs-Bereich je Übung; ist er in allen Sätzen
  ausgereizt, wird mehr Gewicht vorgeschlagen).
- `app/main.py` – FastAPI, jede Anfrage ist an ein Profil gebunden (`?profile=`).
- `static/js/outbox.js` – der Ausgang fürs Offline-Speichern: Einheiten/Gewicht werden bei
  fehlendem Netz lokal gesammelt (`client_id` macht das Nachsenden doppelfest) und beim
  nächsten `online`-Ereignis oder Seitenaufruf der Reihe nach gesendet.
- `static/sw.js` – Service Worker, Netz zuerst, sonst letzter bekannter Stand.
