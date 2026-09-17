"""Mobility-Routinen: kurze, feste Abfolgen zum Aufwaermen und zur Erholung.
Jede Bewegung hat entweder eine Dauer (Sekunden) oder Wiederholungen."""

ROUTINES = {
    "warmup_oben": {
        "title": "Warm-up Oberkörper", "minutes": 6,
        "text": "Vor Push-, Pull- und Oberkörper-Tagen.",
        "moves": [
            ("Armkreisen", 30, None, "Große Kreise, erst vorwärts, dann rückwärts."),
            ("Band-Pull-Aparts", None, 15, "Band vor der Brust auseinanderziehen, Schulterblätter zusammen."),
            ("Katze-Kuh", None, 10, "Im Vierfüßlerstand Rücken abwechselnd rund und hohl machen."),
            ("Brustwirbelsäule drehen", None, 8, "Vierfüßlerstand, eine Hand am Kopf, Ellbogen zur Decke drehen. Je Seite."),
            ("Liegestütz an der Wand", None, 12, "Langsam, volle Bewegung."),
            ("Schulter-Dislocates", None, 10, "Stab oder Band breit greifen, gestreckt über den Kopf nach hinten führen."),
        ],
    },
    "warmup_unten": {
        "title": "Warm-up Unterkörper", "minutes": 7,
        "text": "Vor Bein- und Unterkörper-Tagen.",
        "moves": [
            ("Beinpendel vorne/hinten", None, 12, "An der Wand abstützen, Bein locker schwingen. Je Seite."),
            ("Beinpendel seitlich", None, 12, "Quer vor dem Körper schwingen. Je Seite."),
            ("Hüftkreisen", 30, None, "Hände an die Hüfte, große Kreise in beide Richtungen."),
            ("Kniebeuge mit Körpergewicht", None, 12, "Tief und langsam, Arme nach vorn."),
            ("Ausfallschritt mit Drehung", None, 6, "Im Ausfallschritt den Oberkörper zum vorderen Bein drehen. Je Seite."),
            ("Glute Bridge", None, 12, "Auf dem Rücken, Hüfte hochdrücken, Po anspannen."),
        ],
    },
    "ganzkoerper": {
        "title": "Ganzkörper-Mobility", "minutes": 10,
        "text": "Für Ganzkörper-Tage oder zwischendurch.",
        "moves": [
            ("Katze-Kuh", None, 10, "Rücken abwechselnd rund und hohl."),
            ("World's Greatest Stretch", None, 5, "Tiefer Ausfallschritt, Ellbogen zum Boden, dann Arm zur Decke drehen. Je Seite."),
            ("Tiefe Hocke halten", 45, None, "Fersen am Boden, Ellbogen drücken die Knie leicht auseinander."),
            ("Hüftbeuger-Dehnung", 40, None, "Kniender Ausfallschritt, Hüfte nach vorn schieben. Je Seite."),
            ("Armkreisen", 30, None, "Große Kreise in beide Richtungen."),
            ("Inchworm", None, 6, "Aus dem Stand mit den Händen nach vorn laufen bis zur Liegestützposition und zurück."),
            ("Glute Bridge", None, 12, "Po oben kurz halten."),
        ],
    },
    "huefte": {
        "title": "Hüfte & unterer Rücken", "minutes": 9,
        "text": "Wenn der untere Rücken fest ist oder nach langem Sitzen.",
        "moves": [
            ("Knie zur Brust", 40, None, "Rückenlage, beide Knie umarmen und sanft schaukeln."),
            ("Taube (Pigeon)", 45, None, "Vorderes Bein angewinkelt vor dem Körper, hinteres gestreckt. Je Seite."),
            ("90/90-Sitz", None, 8, "Beide Knie 90° gebeugt, Knie abwechselnd zur anderen Seite kippen."),
            ("Hüftbeuger-Dehnung", 40, None, "Kniender Ausfallschritt. Je Seite."),
            ("Kind-Position", 45, None, "Knie breit, Arme weit nach vorn, ruhig atmen."),
            ("Glute Bridge", None, 12, "Langsam, Po oben anspannen."),
        ],
    },
    "schultern": {
        "title": "Schultern & Brustwirbelsäule", "minutes": 8,
        "text": "Gegen hochgezogene Schultern und Rundrücken.",
        "moves": [
            ("Wand-Engel", None, 10, "Rücken an der Wand, Arme wie beim Schneeengel an der Wand hoch- und runterführen."),
            ("Brustdehnung am Türrahmen", 40, None, "Unterarm an den Rahmen, Körper leicht wegdrehen. Je Seite."),
            ("Faszienrolle Brustwirbelsäule", 60, None, "Rolle quer unter den oberen Rücken, langsam rollen."),
            ("Band-Pull-Aparts", None, 15, "Schulterblätter zusammen."),
            ("Thread the Needle", None, 8, "Vierfüßlerstand, Arm unter dem Körper durchfädeln. Je Seite."),
            ("Schulter-Dislocates", None, 10, "Stab breit greifen."),
        ],
    },
    "erholung": {
        "title": "Erholung", "minutes": 12,
        "text": "Für Tage mit wenig Energie oder viel Muskelkater – ruhig und locker.",
        "moves": [
            ("Tief atmen", 60, None, "Rückenlage, 4 Sekunden ein, 6 Sekunden aus."),
            ("Knie zur Brust", 45, None, "Sanft schaukeln."),
            ("Liegende Drehung", 45, None, "Knie zur Seite kippen, Schultern bleiben am Boden. Je Seite."),
            ("Kind-Position", 60, None, "Ganz loslassen."),
            ("Katze-Kuh", None, 10, "Langsam und im Atemrhythmus."),
            ("Beinrückseite dehnen", 45, None, "Rückenlage, Bein mit Band oder Handtuch nach oben ziehen. Je Seite."),
            ("Lockeres Gehen", 180, None, "Ein paar Minuten spazieren oder locker auf dem Laufband."),
        ],
    },
}

UPPER_GROUPS = {"brust", "ruecken", "schultern", "arme"}


def suggest(day=None, readiness_key=None):
    """Passende Routine fuer heute: Erholung bei schlechter Readiness, sonst das
    Warm-up zu den Muskelgruppen des Trainingstags (funktioniert auch fuer eigene
    Tagesnamen wie "OK"), ohne Training die Ganzkoerper-Routine."""
    if readiness_key == "erholung":
        return "erholung"
    groups = [e.get("muscle_group") for e in (day or {}).get("exercises", []) if e.get("muscle_group")]
    if not groups:
        return "ganzkoerper"
    upper = sum(g in UPPER_GROUPS for g in groups)
    lower = sum(g == "beine" for g in groups)
    if upper and not lower:
        return "warmup_oben"
    if lower >= max(1, len(groups) // 2) and upper <= 1:
        return "warmup_unten"
    return "ganzkoerper"


def routine(key):
    r = ROUTINES.get(key)
    if not r:
        return None
    return {"key": key, "title": r["title"], "minutes": r["minutes"], "text": r["text"],
            "moves": [{"name": n, "seconds": s, "reps": rep, "text": t}
                      for n, s, rep, t in r["moves"]]}


def all_routines():
    return [{"key": k, "title": r["title"], "minutes": r["minutes"], "text": r["text"],
             "count": len(r["moves"])} for k, r in ROUTINES.items()]
