# Fitness

Persönlicher Trainings-Begleiter nach dem Vorbild von MCI – Personal Training AI, als eigener
Dienst auf dem Homeserver. Läuft auch ohne Verbindung: Plan und letzte Werte bleiben
sichtbar, Einträge werden lokal gesammelt und nachgesendet, sobald wieder Netz da ist.

## Bereiche

| Tab | Inhalt |
|---|---|
| Heute | Tages-Serie, Check-in (Schlaf, Energie, Stress, Muskelkater) → Readiness, an die Erholung angepasstes Training, Mobility-Routine, Kalorien |
| Training | Einheit (mit Pausen-Timer, Bewertung beim Beenden) · Plan (Splits, Assistent, Plan-Check) · Übungen (Anleitung, Muskel-Grafik, Video-Suche) · Cardio |
| Ernährung | Kalorien/Makros gegen das Ziel, Mahlzeiten, Suche und Barcode über Open Food Facts, eigene Lebensmittel |
| Fortschritt | Übersicht (Bestleistungen, Volumen) · Körper (Gewicht, Körperfett, Maße, ViCoach) · Erfolge (Serien, Abzeichen) · Verlauf |
| Profil | Daten, Ernährungsziel, Plan-Assistent |

## Plan-Ersteller (Blöcke statt Einmal-Plan)

Der Plan ist ein **Trainingsblock** aus 5–6 Wochen (Einsteiger 6, sonst 5): Woche 1 startet nahe
der kleinsten wirksamen Menge je Muskel, jede Woche kommen Sätze dazu, die letzte Woche ist ein
Deload. Danach beginnt ein neuer Block, Isolationsübungen wechseln, Grundübungen bleiben.

- **Volumen:** Landmarken MV/MEV/MAV/MRV je Muskel (`app/planner/muscles.py`), Fokus-Muskeln
  Richtung MRV, alle anderen auf Erhaltung (Renaissance Periodization; Schoenfeld et al.).
- **Auswahl:** Bewegungsmuster → Übung nach SFR (Reiz/Ermüdung), Schwierigkeit, Gerät und
  Beschwerden (Knie, Rücken, Schulter, Ellbogen, Handgelenk, Hüfte). 106 Studio-Übungen.
- **Progression:** Double Progression mit Ziel-RIR, RPE, Plateau-Erkennung (3 Einheiten ohne
  Fortschritt), Rücksetzen nach zwei Fehlschlägen, Gewichtssprung je Gerät (`progression.py`).
- **Anpassung:** Wochen-Check beim Öffnen von „Heute“ (`autoregulate.py`): verpasste Sätze werden
  nachgeholt, bei steigender Anstrengung wird der Deload vorgezogen, war alles leicht, kommt ein
  Satz dazu. Ausgelassene Tage rutschen auf den nächsten Tag (`stats.schedule_day`).
- **Erklärt:** jede Änderung steht als Satz im Plan unter „Was sich geändert hat“.

Quellen: [Volumen-Landmarken](https://thestrengthequation.com/post/volume-landmarks.html),
[Dynamic Double Progression](https://www.jpshealthandfitness.com.au/the-utility-of-dynamic-autoregulated-double-progression/),
[Deloading (Bell et al.)](https://shura.shu.ac.uk/35313/3/Bell-APracticalApproach(AM).pdf),
[Trainingsfrequenz-Metaanalyse](https://www.researchgate.net/publication/301578131_Effects_of_Resistance_Training_Frequency_on_Measures_of_Muscle_Hypertrophy_A_Systematic_Review_and_Meta-Analysis).

## Körperanalyse aus dem Studio (ViCoach)

ViCoach hat keine Schnittstelle und keinen Export. Stattdessen: in ViCoach einen Screenshot
vom Messergebnis machen → Fortschritt → Körper → „ViCoach-Screenshot“. Die KI liest die Werte,
man prüft sie im Formular und speichert erst dann. Das Bild wird nicht gespeichert.

## Datenschutz

- Alle Daten liegen in `fitness.db` auf dem Pi.
- An **Anthropic** (nur mit `ANTHROPIC_API_KEY`, nur auf Knopfdruck): Plan-Check, KI-Verfeinerung
  (Plan + Eckdaten ohne Namen) und der ViCoach-Screenshot.
- An **Open Food Facts**: nur Suchbegriff bzw. Barcode.

## Start (lokal)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --port 8001
```

## Tests

```
.venv/bin/pytest
```

Reine Rechenlogik (Readiness-Score, Plan-Generator, Progression, Blockwochen, Serien-Zaehlung,
Koerperfett-Schaetzung, Einheits-Bewertung) plus die Sicherheitsgrenze fuer KI-Antworten
(`ai.validate_plan`).
Laeuft gegen eine eigene, temporaere SQLite-Datei – `fitness.db` bleibt unangetastet.

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
