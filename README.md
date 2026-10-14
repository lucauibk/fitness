# Fitness

Persönlicher Trainings-Begleiter nach dem Vorbild von MCI – Personal Training AI, als eigener
Dienst auf dem Homeserver. Läuft auch ohne Verbindung: Plan und letzte Werte bleiben
sichtbar, Einträge werden lokal gesammelt und nachgesendet, sobald wieder Netz da ist.

## Bereiche

| Tab | Inhalt |
|---|---|
| Heute | Tages-Serie, Check-in (Schlaf, Energie, Stress, Muskelkater) → Readiness, an die Erholung angepasstes Training, Mobility-Routine, Kalorien |
| Training | Einheit (mit Pausen-Timer, Bildschirm bleibt an, Bewertung beim Beenden) · Plan (Splits, Assistent, Plan-Check) · Übungen (Anleitung, Muskel-Grafik, Video-Suche) · Cardio |
| Ernährung | Kalorien/Makros gegen das Ziel, Mahlzeiten, Suche und Barcode über Open Food Facts, eigene Lebensmittel |
| Fortschritt | Übersicht (Bestleistungen, Volumen) · Körper (Gewicht, Körperfett, Maße, ViCoach) · Erfolge (Serien, Abzeichen) · Verlauf |
| Profil | Daten, Ernährungsziel, Plan-Assistent |

## Plan-Ersteller (ein Tipp, dann lernt die App)

**Erstellen:** Training → Plan → „Neuen Plan erstellen“ (oben, immer sichtbar). Ein Bildschirm, alles vorbelegt:
Erfahrung, Ziel, **freie Tage** (nicht dasselbe wie Trainingstage), **Einheiten pro Woche** (Empfehlung nach Erfahrung,
Ziel und Dauer, höchstens so viele wie freie Tage), Dauer. Fokus-Muskeln und Beschwerden sind eingeklappt. Aus den freien
Tagen wählt die App die mit den gleichmäßigsten Abständen (Mi–So und 3× → Mi · Fr · So), die übrigen bleiben Reserve zum
Nachholen. Der Plan ist sofort aktiv.

- **Split nach Tagen:** 2 Ganzkörper A/B · 3 Ganzkörper A/B/C · 4 Ober/Unter ×2 · 5 Ober/Unter + Push/Pull/Beine · 6 PPL ×2
  (jeder Muskel ≥ 2× pro Woche). Die Einheiten sind eine Warteschlange: ausgelassene Tage rücken nach, ein trainierter
  anderer Tag zählt, Muskeln bekommen mindestens einen Tag Pause (nach langen Einheiten zwei).
- **Block:** Volumen steigt von Woche zu Woche (MEV → MAV, Fokus Richtung MRV). Entlastung nur **bei Bedarf** – bei zwei
  Ermüdungssignalen (steigende Anstrengung, sinkende Leistung, Stillstand, anhaltender Muskelkater), sonst läuft der
  Block bis zu 8 Wochen weiter. Neuer Block = frische Isolationsübungen, Grundübungen bleiben.
- **Auswahl:** Bewegungsmuster → Übung nach Reiz/Ermüdung, Gerät, Schwierigkeit, Belastung in der gedehnten Position und
  Beschwerden. 106 Studio-Übungen. „Schmerz bei dieser Übung“ ersetzt belastende Übungen im Plan.
- **Kaltstart:** Startgewicht als Schätzung aus Körpergewicht, Geschlecht, Alter und Gerät (±25 %), besser aus einer
  ähnlichen Übung. Die erste Einheit kalibriert („leicht / passt / zu schwer“). Erfahrung wird aus den Daten angehoben.
- **Progression:** Anfänger wiederholungsbasiert (lineare Steigerung, Plateau nach zwei Stillständen, keine RPE-Regeln),
  sonst Double Progression mit RPE, Plateau nach drei Einheiten, Rücksetzen nach zwei Fehlschlägen, Wiedereinstieg nach
  Pausen (> 14 Tage 93 %, > 28 Tage 87 % der letzten Last).
- **Wochen-Check** beim Öffnen von „Heute“: verpasste Sätze werden nachgeholt (höchstens +20 % der Wochensätze, nie über
  MRV), war ein Muskel überall leicht, kommt ein Satz dazu, zweiter Stillstand → Variante tauschen. Jede Änderung steht
  unter „Was sich geändert hat“.

Quellen: [Frequenz](https://pubmed.ncbi.nlm.nih.gov/30558493/), [Volumen](https://link.springer.com/article/10.1007/s40279-025-02344-w),
[Volumen-Landmarken](https://thestrengthequation.com/post/volume-landmarks.html),
[Dynamic Double Progression](https://www.jpshealthandfitness.com.au/the-utility-of-dynamic-autoregulated-double-progression/),
[Deloading](https://shura.shu.ac.uk/35313/3/Bell-APracticalApproach(AM).pdf), [RIR-Genauigkeit](https://pmc.ncbi.nlm.nih.gov/articles/PMC5712461/).
Eine fertige Open-Source-Lösung gibt es nicht (geprüft: Liftosaur, wger, OpenGym, streprogen, routine-engine).

## Körperanalyse aus dem Studio (ViCoach)

ViCoach hat keine Schnittstelle und keinen Export. Stattdessen: in ViCoach einen Screenshot
vom Messergebnis machen → Fortschritt → Körper → „ViCoach-Screenshot“. Die KI liest die Werte,
man prüft sie im Formular und speichert erst dann. Das Bild wird nicht gespeichert.

## Datenschutz

- Alle Daten liegen in `fitness.db` auf dem Pi.
- An **Anthropic** (nur mit `ANTHROPIC_API_KEY`, nur auf Knopfdruck): Plan-Check, KI-Verfeinerung
  (Plan + Eckdaten ohne Namen) und der ViCoach-Screenshot.
- An **Anthropic** über Claude Code (nur wenn der Coach-Timer eingerichtet ist, täglich): aktiver Plan,
  Block-Stand, Trainings der letzten sechs Wochen, Check-ins (Schlaf, Stress, Muskelkater), Ziele und
  eingetragene Beschwerden – ohne Namen und Körperdaten.
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

## Täglicher Coach

Jeden Morgen um 6:30 schaut Claude auf dem Pi als persönlicher Coach den aktiven Plan, den Stand im
Block, die Trainings der letzten sechs Wochen und die Check-ins an und legt höchstens drei kurze
**Coach-Hinweise** in „Heute“ ab. Er lobt mit Beleg aus den Daten, spricht Probleme offen an, gibt je
Hinweis eine nächste Handlung und schlägt nichts vor, was die App ohnehin selbst anpasst (Volumen im
Block, Gewichtssteigerung, Entlastung, Wochen-Check). Was weggeklickt wurde, bringt er nicht wieder;
überholte Hinweise räumt er selbst ab, offene verschwinden nach 14 Tagen. Der Coach **ändert nichts**: Claude läuft
ohne Werkzeuge (`--tools ""`) und liefert nur Text, `app/coach.py` schreibt ausschließlich in
`coach_notes`. An Claude gehen Plan, Sätze und Ziele, aber kein Name und keine Körperdaten.

Einrichten (einmalig auf dem Pi): Claude Code installieren
(`curl -fsSL https://claude.ai/install.sh | bash`), `claude setup-token` ausführen und den Token als
`CLAUDE_CODE_OAUTH_TOKEN` in `~/fitness/.env` eintragen. `pi-deploy.sh` aktiviert den Timer.

```
systemctl --user start fitness-coach      # sofort laufen lassen
journalctl --user -u fitness-coach -n 30  # Log
PYTHONPATH=. .venv/bin/python scripts/coach_daily.py --dry-run   # nur anzeigen, nichts speichern
```

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
