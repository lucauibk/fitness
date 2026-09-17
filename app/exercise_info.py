"""Anleitungen fuer die mitgelieferten Uebungen (Uebungsbibliothek).

Muskel-Schluessel passen zur Muskel-Grafik im Frontend (static/js/muscles.js).
Eingespielt wird nur in leere Felder – eigene Aenderungen bleiben erhalten.
"""

MUSCLES = {
    "brust": "Brust", "schulter_vorne": "vordere Schulter", "schulter_seite": "seitliche Schulter",
    "schulter_hinten": "hintere Schulter", "bizeps": "Bizeps", "trizeps": "Trizeps",
    "unterarm": "Unterarme", "bauch": "gerader Bauch", "seitl_bauch": "seitlicher Bauch",
    "latissimus": "Latissimus", "oberer_ruecken": "oberer Rücken", "unterer_ruecken": "unterer Rücken",
    "gesaess": "Gesäß", "quadrizeps": "Oberschenkel vorne", "beinbeuger": "Oberschenkel hinten",
    "adduktoren": "Adduktoren", "waden": "Waden", "herz": "Herz-Kreislauf",
}

# Name: (Geraet, Hauptmuskeln, Hilfsmuskeln, Schritte, typische Fehler)
INFO = {
    "Bankdrücken": ("Langhantel, Flachbank", "brust", "schulter_vorne,trizeps", [
        "Auf die Bank legen, Augen unter der Stange, Füße fest am Boden.",
        "Schulterblätter zusammen und nach unten ziehen, leichtes Hohlkreuz.",
        "Stange etwas breiter als schulterbreit greifen und aus der Ablage heben.",
        "Kontrolliert zur unteren Brust absenken, Ellbogen etwa 45° vom Körper.",
        "Kraftvoll nach oben drücken, bis die Arme fast gestreckt sind.",
    ], ["Stange auf der Brust abfedern lassen.", "Ellbogen weit nach außen (90°).",
        "Po hebt von der Bank ab."]),
    "Schrägbankdrücken Kurzhantel": ("Kurzhanteln, Schrägbank 30–45°", "brust", "schulter_vorne,trizeps", [
        "Bank auf 30–45° stellen, Kurzhanteln auf den Oberschenkeln aufsetzen.",
        "Beim Zurücklegen die Hanteln neben die obere Brust bringen.",
        "Schulterblätter zurückziehen, Handflächen leicht zueinander drehen.",
        "Hanteln nach oben drücken, oben leicht zusammenführen.",
        "Langsam bis auf Brusthöhe absenken.",
    ], ["Bank zu steil – dann arbeitet vor allem die Schulter.",
        "Hanteln oben zusammenschlagen.", "Zu tief absenken, Schultern rollen nach vorn."]),
    "Brustpresse": ("Maschine", "brust", "schulter_vorne,trizeps", [
        "Sitz so einstellen, dass die Griffe auf Höhe der mittleren Brust sind.",
        "Rücken an die Lehne, Schulterblätter zurückziehen.",
        "Griffe nach vorn drücken, ohne die Ellbogen ganz durchzustrecken.",
        "Langsam zurückführen, bis ein leichter Zug in der Brust spürbar ist.",
    ], ["Sitz zu hoch oder zu tief eingestellt.", "Schultern heben sich nach vorn.",
        "Gewicht fällt ungebremst zurück."]),
    "Butterfly": ("Maschine", "brust", "schulter_vorne", [
        "Sitz so einstellen, dass die Arme etwa auf Schulterhöhe sind.",
        "Ellbogen leicht gebeugt, Brust nach vorn, Rücken an der Lehne.",
        "Arme in einem Bogen vor der Brust zusammenführen.",
        "Kurz halten und langsam öffnen, bis die Brust gedehnt ist.",
    ], ["Arme ganz gestreckt – belastet die Ellbogen.", "Zu weit nach hinten öffnen.",
        "Schwung aus dem Oberkörper."]),
    "Kabelzug-Fliegende": ("Kabelzug", "brust", "schulter_vorne", [
        "Rollen etwa auf Schulterhöhe, einen Schritt vor das Gerät stellen.",
        "Leicht nach vorn lehnen, Ellbogen leicht gebeugt.",
        "Griffe in einem Bogen vor der Brust zusammenführen.",
        "Kontrolliert zurück, bis die Brust gedehnt ist.",
    ], ["Aus den Armen drücken statt im Bogen führen.", "Hohlkreuz beim Zusammenführen."]),
    "Liegestütze": ("Körpergewicht", "brust", "trizeps,schulter_vorne,bauch", [
        "Hände etwas breiter als schulterbreit, Körper bildet eine Linie.",
        "Bauch und Po anspannen.",
        "Brust bis kurz über den Boden absenken, Ellbogen etwa 45°.",
        "Kraftvoll hochdrücken.",
        "Zu schwer? Knie aufsetzen oder Hände erhöht abstützen.",
    ], ["Hüfte hängt durch.", "Nur halbe Bewegung.", "Kopf nach vorn gestreckt."]),
    "Dips": ("Barren", "brust", "trizeps,schulter_vorne", [
        "An den Holmen hochstützen, Arme gestreckt.",
        "Oberkörper leicht nach vorn neigen (mehr Brust) oder aufrecht (mehr Trizeps).",
        "Absenken, bis die Oberarme etwa waagrecht sind.",
        "Wieder hochdrücken.",
    ], ["Zu tief – belastet die Schultern.", "Schultern ziehen zu den Ohren.",
        "Schwung aus den Beinen."]),
    "Klimmzüge": ("Klimmzugstange", "latissimus", "bizeps,oberer_ruecken", [
        "Stange etwas breiter als schulterbreit im Obergriff fassen.",
        "Schulterblätter nach unten ziehen, Bauch anspannen.",
        "Brust Richtung Stange ziehen, bis das Kinn darüber ist.",
        "Kontrolliert bis in die gestreckte Position ablassen.",
        "Zu schwer? Gummiband oder Klimmzug-Maschine nutzen.",
    ], ["Schwung aus den Beinen (Kipping).", "Nur halbe Wiederholungen.",
        "Kinn nach vorn recken statt Brust hochziehen."]),
    "Latzug": ("Kabelzug", "latissimus", "bizeps,oberer_ruecken", [
        "Oberschenkel unter die Polster klemmen, Stange breit greifen.",
        "Leicht nach hinten lehnen, Brust raus.",
        "Stange zur oberen Brust ziehen, Ellbogen nach unten und hinten.",
        "Langsam nach oben führen, bis die Arme gestreckt sind.",
    ], ["Stange in den Nacken ziehen.", "Stark nach hinten schaukeln.",
        "Mit den Armen statt mit dem Rücken ziehen."]),
    "Enger Latzug": ("Kabelzug, V-Griff", "latissimus", "bizeps,oberer_ruecken", [
        "V-Griff einhängen, Oberschenkel fixieren.",
        "Oberkörper leicht zurückneigen.",
        "Griff zur Brust ziehen, Ellbogen eng am Körper nach unten.",
        "Kontrolliert nach oben strecken.",
    ], ["Rundrücken beim Ziehen.", "Gewicht nach oben reißen lassen."]),
    "Rudern Kabel": ("Kabelzug, sitzend", "oberer_ruecken", "latissimus,bizeps,schulter_hinten", [
        "Füße auf die Stützen, Knie leicht gebeugt, Rücken gerade.",
        "Griff zum Bauchnabel ziehen, Schulterblätter zusammen.",
        "Kurz halten, Brust bleibt aufrecht.",
        "Arme langsam nach vorn strecken, ohne den Rücken rund zu machen.",
    ], ["Oberkörper schwingt vor und zurück.", "Schultern ziehen hoch.",
        "Rundrücken am Ende der Bewegung."]),
    "Rudern Maschine": ("Maschine", "oberer_ruecken", "latissimus,bizeps,schulter_hinten", [
        "Sitz so einstellen, dass die Griffe auf Brusthöhe sind, Brust ans Polster.",
        "Griffe nach hinten ziehen, Ellbogen am Körper vorbei.",
        "Schulterblätter zusammendrücken und kurz halten.",
        "Kontrolliert nach vorn lassen.",
    ], ["Brust löst sich vom Polster.", "Nur aus den Armen ziehen."]),
    "Langhantelrudern": ("Langhantel", "oberer_ruecken", "latissimus,bizeps,unterer_ruecken", [
        "Hüftbreit stehen, Stange schulterbreit greifen.",
        "Aus der Hüfte nach vorn beugen, Rücken gerade, Oberkörper etwa 45°.",
        "Stange zum unteren Brustbein bzw. Bauchnabel ziehen.",
        "Kontrolliert ablassen, Rücken bleibt fest.",
    ], ["Rundrücken.", "Oberkörper richtet sich beim Ziehen auf.", "Zu schweres Gewicht mit Schwung."]),
    "Kreuzheben": ("Langhantel", "unterer_ruecken,gesaess,beinbeuger", "quadrizeps,oberer_ruecken,unterarm", [
        "Füße hüftbreit, Stange über dem Mittelfuß.",
        "Hüfte beugen, Stange knapp außerhalb der Knie greifen, Rücken gerade.",
        "Brust raus, Lat anspannen, Stange nah am Körper.",
        "Mit den Beinen vom Boden drücken und die Hüfte nach vorn bringen, bis du aufrecht stehst.",
        "Kontrolliert in umgekehrter Reihenfolge ablegen.",
    ], ["Rundrücken beim Anheben.", "Stange weit vom Körper.",
        "Oben ins Hohlkreuz überstrecken."]),
    "Rumänisches Kreuzheben": ("Langhantel oder Kurzhanteln", "beinbeuger,gesaess", "unterer_ruecken", [
        "Aufrecht stehen, Stange vor den Oberschenkeln, Knie leicht gebeugt.",
        "Hüfte nach hinten schieben, Stange gleitet an den Beinen nach unten.",
        "Absenken, bis eine deutliche Dehnung hinten im Oberschenkel spürbar ist.",
        "Hüfte nach vorn bringen und aufrichten.",
    ], ["Rücken wird rund.", "Knie beugen sich zu stark (wird zur Kniebeuge).",
        "Stange entfernt sich vom Körper."]),
    "Hyperextension": ("Hyperextension-Bank", "unterer_ruecken,gesaess", "beinbeuger", [
        "Hüfte knapp über dem Polster, Füße fixiert.",
        "Arme vor der Brust kreuzen.",
        "Oberkörper mit geradem Rücken absenken.",
        "Bis zur Linie mit den Beinen aufrichten – nicht überstrecken.",
    ], ["Oben ins Hohlkreuz.", "Schwung statt Kontrolle."]),
    "Kniebeuge": ("Langhantel, Rack", "quadrizeps,gesaess", "beinbeuger,unterer_ruecken,bauch", [
        "Stange auf den oberen Rücken legen, Füße etwa schulterbreit, Zehen leicht nach außen.",
        "Bauch anspannen, tief einatmen.",
        "Hüfte nach hinten und unten, Knie zeigen in Richtung der Zehen.",
        "So tief gehen, wie es mit geradem Rücken geht (Oberschenkel mindestens waagrecht).",
        "Aus den Fersen kraftvoll hochdrücken.",
    ], ["Knie fallen nach innen.", "Fersen heben ab.", "Rücken rundet sich unten."]),
    "Beinpresse": ("Maschine", "quadrizeps,gesaess", "beinbeuger", [
        "Rücken und Po fest an die Lehne, Füße schulterbreit auf die Platte.",
        "Sicherung lösen, Platte kontrolliert absenken.",
        "So weit beugen, bis die Knie etwa 90° haben und der Po noch aufliegt.",
        "Über die ganze Fußsohle zurückdrücken, Knie nicht ganz durchstrecken.",
    ], ["Po hebt ab (Rundrücken).", "Knie durchgedrückt.", "Knie fallen nach innen."]),
    "Ausfallschritte": ("Kurzhanteln oder Körpergewicht", "quadrizeps,gesaess", "beinbeuger,adduktoren", [
        "Aufrecht stehen, Hanteln seitlich.",
        "Großen Schritt nach vorn machen.",
        "Hinteres Knie kontrolliert Richtung Boden senken.",
        "Über die vordere Ferse zurück in den Stand drücken, Seite wechseln.",
    ], ["Vorderes Knie knickt nach innen.", "Schritt zu kurz.", "Oberkörper kippt nach vorn."]),
    "Bulgarian Split Squat": ("Kurzhanteln, Bank", "quadrizeps,gesaess", "beinbeuger,adduktoren", [
        "Rücken zur Bank, hinteren Fuß mit dem Rist auf die Bank legen.",
        "Vorderer Fuß so weit vorn, dass das Knie beim Absenken etwa über dem Fuß bleibt.",
        "Senkrecht nach unten gehen, bis der vordere Oberschenkel waagrecht ist.",
        "Über die vordere Ferse hochdrücken.",
    ], ["Vorderer Fuß zu nah an der Bank.", "Hüfte dreht sich weg."]),
    "Beinstrecker": ("Maschine", "quadrizeps", "", [
        "Kniegelenk auf Höhe der Drehachse, Polster über den Knöcheln.",
        "Griffe fassen, Rücken an die Lehne.",
        "Beine strecken, oben kurz halten.",
        "Langsam absenken.",
    ], ["Schwung beim Hochkommen.", "Po hebt vom Sitz ab."]),
    "Beinbeuger": ("Maschine, liegend", "beinbeuger", "waden", [
        "Bauchlings auflegen, Polster knapp über den Fersen.",
        "Hüfte ans Polster drücken.",
        "Fersen Richtung Po ziehen.",
        "Kontrolliert strecken.",
    ], ["Hüfte hebt ab.", "Gewicht fällt zurück."]),
    "Beinbeuger sitzend": ("Maschine", "beinbeuger", "waden", [
        "Knie auf Höhe der Drehachse, Oberschenkelpolster fest schließen.",
        "Unterschenkel liegen auf dem unteren Polster.",
        "Fersen nach unten und hinten ziehen.",
        "Langsam zurückführen.",
    ], ["Oberschenkelpolster zu locker.", "Nur halbe Bewegung."]),
    "Hip Thrust": ("Langhantel, Bank", "gesaess", "beinbeuger,quadrizeps", [
        "Oberer Rücken an der Bankkante, Stange (gepolstert) über der Hüfte.",
        "Füße hüftbreit, Schienbeine oben senkrecht.",
        "Hüfte nach oben drücken, bis Oberkörper und Oberschenkel eine Linie bilden.",
        "Po oben fest anspannen, dann kontrolliert absenken.",
    ], ["Oben ins Hohlkreuz statt Po anspannen.", "Füße zu weit weg (Beinbeuger übernehmen)."]),
    "Kabel-Kickback": ("Kabelzug, Fußschlaufe", "gesaess", "beinbeuger", [
        "Schlaufe um den Knöchel, leicht nach vorn lehnen und festhalten.",
        "Bein gestreckt nach hinten führen, Po anspannen.",
        "Kurz halten, langsam zurück.",
    ], ["Hohlkreuz beim Zurückführen.", "Schwung aus dem Oberkörper."]),
    "Wadenheben": ("Maschine oder Stufe", "waden", "", [
        "Fußballen auf die Kante, Fersen frei.",
        "Fersen so tief wie möglich absenken.",
        "Auf die Zehenspitzen hochdrücken und oben kurz halten.",
    ], ["Wippen statt voller Bewegung.", "Knie beugen sich mit."]),
    "Wadenheben sitzend": ("Maschine", "waden", "", [
        "Polster auf die Oberschenkel knapp über den Knien.",
        "Fußballen auf die Plattform, Fersen tief.",
        "Fersen hochdrücken, kurz halten, langsam absenken.",
    ], ["Zu kleine Bewegung.", "Schwung."]),
    "Schulterdrücken": ("Langhantel oder Maschine", "schulter_vorne,schulter_seite", "trizeps", [
        "Aufrecht stehen oder sitzen, Stange auf Höhe der oberen Brust.",
        "Bauch und Po anspannen.",
        "Gewicht senkrecht über den Kopf drücken, Kopf leicht zurück.",
        "Kontrolliert zur Brust absenken.",
    ], ["Starkes Hohlkreuz.", "Stange vor dem Körper statt über dem Kopf."]),
    "Kurzhantel-Schulterdrücken": ("Kurzhanteln, Bank mit Lehne", "schulter_vorne,schulter_seite", "trizeps", [
        "Lehne fast senkrecht, Hanteln auf Schulterhöhe.",
        "Handflächen nach vorn oder leicht zueinander.",
        "Hanteln nach oben drücken, bis die Arme fast gestreckt sind.",
        "Langsam bis auf Ohrhöhe absenken.",
    ], ["Rücken löst sich von der Lehne.", "Hanteln zu weit nach außen."]),
    "Seitheben": ("Kurzhanteln", "schulter_seite", "oberer_ruecken", [
        "Aufrecht stehen, Hanteln seitlich, Ellbogen leicht gebeugt.",
        "Arme seitlich bis etwa Schulterhöhe heben.",
        "Kleiner Finger leicht höher als der Daumen.",
        "Langsam absenken.",
    ], ["Schwung aus dem Körper.", "Schultern ziehen zu den Ohren.", "Zu schweres Gewicht."]),
    "Seitheben Kabel": ("Kabelzug", "schulter_seite", "oberer_ruecken", [
        "Seitlich zum Gerät stehen, Rolle unten, Griff mit der entfernten Hand.",
        "Arm seitlich bis Schulterhöhe führen.",
        "Langsam zurück – das Kabel hält die Spannung.",
    ], ["Oberkörper kippt weg.", "Arm zu weit über Schulterhöhe."]),
    "Frontheben": ("Kurzhanteln", "schulter_vorne", "brust", [
        "Aufrecht stehen, Hanteln vor den Oberschenkeln.",
        "Arme gestreckt nach vorn bis Schulterhöhe heben.",
        "Langsam absenken.",
    ], ["Schwung aus dem Rücken.", "Zu hoch über Schulterhöhe."]),
    "Face Pulls": ("Kabelzug, Seil", "schulter_hinten", "oberer_ruecken", [
        "Rolle auf Kopfhöhe, Seil mit beiden Händen greifen.",
        "Zum Gesicht ziehen, Ellbogen hoch und nach außen.",
        "Hände am Ende neben den Ohren, Schulterblätter zusammen.",
        "Langsam zurück.",
    ], ["Ellbogen zu tief (wird zu Rudern).", "Mit Schwung ziehen."]),
    "Reverse Butterfly": ("Maschine", "schulter_hinten", "oberer_ruecken", [
        "Brust ans Polster, Griffe auf Schulterhöhe.",
        "Arme leicht gebeugt in einem Bogen nach hinten führen.",
        "Kurz halten, langsam zurück.",
    ], ["Schulterblätter ziehen zu stark zusammen (Trapez übernimmt).", "Schwung."]),
    "Bizepscurls Kurzhantel": ("Kurzhanteln", "bizeps", "unterarm", [
        "Aufrecht stehen, Hanteln seitlich, Handflächen nach vorn.",
        "Ellbogen bleiben am Körper.",
        "Hanteln zur Schulter beugen, oben kurz anspannen.",
        "Langsam ganz strecken.",
    ], ["Ellbogen wandern nach vorn.", "Schwung aus dem Rücken.", "Nur halbe Bewegung."]),
    "Hammercurls": ("Kurzhanteln", "bizeps,unterarm", "", [
        "Hanteln mit Daumen nach oben halten (neutraler Griff).",
        "Ellbogen am Körper, Hanteln Richtung Schulter beugen.",
        "Langsam absenken.",
    ], ["Schwung.", "Handgelenke knicken ab."]),
    "SZ-Curls": ("SZ-Stange", "bizeps", "unterarm", [
        "Stange an den schrägen Griffen fassen, Arme gestreckt.",
        "Ellbogen fest, Stange zur Brust beugen.",
        "Langsam ablassen.",
    ], ["Hüfte schiebt nach vorn.", "Ellbogen weichen nach hinten aus."]),
    "Trizepsdrücken Kabel": ("Kabelzug, Seil oder Stange", "trizeps", "", [
        "Vor dem Gerät stehen, Rolle oben, Ellbogen am Körper.",
        "Unterarme nach unten strecken, bis die Arme gerade sind.",
        "Kurz halten, langsam bis etwa 90° zurück.",
    ], ["Ellbogen wandern nach vorn.", "Oberkörper drückt mit."]),
    "Trizeps über Kopf": ("Kurzhantel oder Kabel", "trizeps", "", [
        "Hantel mit beiden Händen über dem Kopf halten.",
        "Ellbogen zeigen nach vorn und bleiben eng.",
        "Hantel hinter den Kopf absenken.",
        "Arme wieder strecken.",
    ], ["Ellbogen gehen weit auseinander.", "Hohlkreuz."]),
    "Enges Bankdrücken": ("Langhantel, Flachbank", "trizeps", "brust,schulter_vorne", [
        "Wie Bankdrücken, aber schulterbreit greifen.",
        "Ellbogen eng am Körper absenken.",
        "Zur unteren Brust führen und hochdrücken.",
    ], ["Zu eng greifen – belastet die Handgelenke.", "Ellbogen gehen nach außen."]),
    "Unterarmstütz (Plank)": ("Körpergewicht", "bauch", "seitl_bauch,schulter_vorne,gesaess", [
        "Unterarme unter den Schultern, Beine gestreckt.",
        "Körper bildet eine gerade Linie von Kopf bis Ferse.",
        "Bauch und Po anspannen, ruhig weiteratmen.",
        "Position halten.",
    ], ["Hüfte hängt durch.", "Po zu hoch.", "Luft anhalten."]),
    "Crunches": ("Körpergewicht", "bauch", "", [
        "Auf den Rücken legen, Beine angestellt.",
        "Hände locker an den Schläfen.",
        "Schultern vom Boden heben, Bauch einrollen.",
        "Langsam zurück, ohne ganz abzulegen.",
    ], ["Am Kopf ziehen.", "Schwung aus dem Oberkörper."]),
    "Cable Crunch": ("Kabelzug, Seil", "bauch", "", [
        "Vor dem Gerät knien, Seil neben dem Kopf halten.",
        "Oberkörper einrollen, Ellbogen Richtung Knie.",
        "Hüfte bleibt ruhig, langsam zurück.",
    ], ["Aus der Hüfte beugen statt einrollen.", "Mit den Armen ziehen."]),
    "Beinheben hängend": ("Klimmzugstange", "bauch", "unterarm", [
        "An der Stange hängen, Schultern aktiv.",
        "Beine (gebeugt oder gestreckt) nach oben heben, Becken einrollen.",
        "Kontrolliert absenken, nicht schwingen.",
    ], ["Schwung.", "Nur die Beine heben, Becken bleibt starr."]),
    "Russian Twist": ("Körpergewicht oder Gewichtsscheibe", "seitl_bauch", "bauch", [
        "Sitzen, Oberkörper leicht zurück, Füße angehoben oder aufgesetzt.",
        "Gewicht vor dem Körper halten.",
        "Oberkörper abwechselnd nach links und rechts drehen.",
    ], ["Rundrücken.", "Nur die Arme bewegen."]),
    "Käfer (Dead Bug)": ("Körpergewicht", "bauch", "seitl_bauch", [
        "Auf dem Rücken, Arme nach oben, Beine im 90°-Winkel.",
        "Unteren Rücken fest an den Boden drücken.",
        "Gegenüberliegenden Arm und Bein langsam strecken.",
        "Zurück und Seite wechseln.",
    ], ["Hohlkreuz.", "Zu schnell."]),
    "Pallof Press": ("Kabelzug", "seitl_bauch", "bauch", [
        "Seitlich zum Gerät stehen, Griff mit beiden Händen vor der Brust.",
        "Arme gerade nach vorn drücken, ohne dass der Körper sich dreht.",
        "Kurz halten, zurück zur Brust.",
    ], ["Oberkörper dreht zum Gerät.", "Zu nah am Gerät."]),
    "Rudergerät": ("Rudergerät", "herz", "oberer_ruecken,quadrizeps", [
        "Füße festschnallen, Griff fassen.",
        "Zuerst mit den Beinen abdrücken, dann leicht zurücklehnen, zuletzt ziehen.",
        "In umgekehrter Reihenfolge zurück.",
    ], ["Nur mit den Armen ziehen.", "Rundrücken."]),
    "Laufband": ("Laufband", "herz", "quadrizeps,waden", [
        "Mit langsamem Tempo beginnen, dann steigern.",
        "Aufrecht laufen, nicht am Geländer festhalten.",
        "Zum Ende hin langsam auslaufen.",
    ], ["Dauerhaft am Geländer halten.", "Zu große Schritte."]),
}


def apply(c):
    """In leere Felder schreiben. Laeuft bei jedem Start, aendert aber nie Eingetragenes."""
    for name, (equipment, primary, secondary, steps, mistakes) in INFO.items():
        c.execute(
            "UPDATE exercises SET"
            " instructions = COALESCE(instructions, ?),"
            " mistakes = COALESCE(mistakes, ?),"
            " muscles_primary = COALESCE(muscles_primary, ?),"
            " muscles_secondary = COALESCE(muscles_secondary, ?),"
            " equipment = COALESCE(equipment, ?)"
            " WHERE name = ?",
            ("\n".join(steps), "\n".join(mistakes), primary, secondary or None, equipment, name))
