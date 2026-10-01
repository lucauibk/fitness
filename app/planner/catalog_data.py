"""Studio-Katalog mit Eigenschaften fuer die Auswahl.

Spalten: Name | Gruppe (alt) | Einheit | Muster | Geraet | Schwierigkeit 1-3 | SFR 1-5 | Belastung
SFR = Stimulus-to-Fatigue-Ratio (5 = viel Reiz, wenig Ermuedung). Belastung = Bereiche, die bei
Beschwerden gemieden werden (knee, lower_back, shoulder, elbow, wrist, hip).
Bestehende Uebungen werden nur ergaenzt (Name bleibt die Id), neue nur angelegt, wenn der Name fehlt.
"""

EQUIPMENT = ("barbell", "dumbbell", "machine", "cable", "bodyweight", "smith")

RAW = """
Bankdrücken|brust|kg|h_push|barbell|2|3|shoulder
Schrägbankdrücken Langhantel|brust|kg|h_push|barbell|2|3|shoulder
Schrägbankdrücken Kurzhantel|brust|kg|h_push|dumbbell|1|4|
Flachbankdrücken Kurzhantel|brust|kg|h_push|dumbbell|1|4|
Brustpresse|brust|kg|h_push|machine|1|4|
Brustpresse schräg|brust|kg|h_push|machine|1|4|
Kabel-Brustpresse|brust|kg|h_push|cable|1|4|
Smith-Schrägbankdrücken|brust|kg|h_push|smith|1|4|shoulder
Liegestütze|brust|koerper|h_push|bodyweight|1|3|wrist
Dips|brust|koerper|h_push|bodyweight|3|3|shoulder,elbow
Butterfly|brust|kg|chest_iso|machine|1|5|
Kabelzug-Fliegende|brust|kg|chest_iso|cable|1|5|
Kabel-Fliegende von unten|brust|kg|chest_iso|cable|1|5|
Kabel-Fliegende einarmig|brust|kg|chest_iso|cable|1|4|
Kurzhantel-Fliegende|brust|kg|chest_iso|dumbbell|2|3|shoulder
Klimmzüge|ruecken|koerper|v_pull|bodyweight|3|3|elbow,shoulder
Klimmzüge assistiert (Maschine)|ruecken|kg|v_pull|machine|1|4|
Latzug|ruecken|kg|v_pull|cable|1|4|
Enger Latzug|ruecken|kg|v_pull|cable|1|4|
Latzug Untergriff|ruecken|kg|v_pull|cable|1|4|
Latzug einarmig|ruecken|kg|v_pull|cable|1|4|
Latzug Maschine|ruecken|kg|v_pull|machine|1|4|
Kabel-Pullover|ruecken|kg|v_pull|cable|1|5|
Langhantelrudern|ruecken|kg|h_pull|barbell|3|3|lower_back
T-Bar-Rudern|ruecken|kg|h_pull|barbell|2|3|lower_back
Smith-Rudern|ruecken|kg|h_pull|smith|2|3|lower_back
Rudern Kabel|ruecken|kg|h_pull|cable|1|4|
Rudern Kabel einarmig|ruecken|kg|h_pull|cable|1|4|
Rudern Maschine|ruecken|kg|h_pull|machine|1|4|
Brustgestütztes Rudern|ruecken|kg|h_pull|machine|1|5|
Kurzhantelrudern einarmig|ruecken|kg|h_pull|dumbbell|1|4|
Inverted Row|ruecken|koerper|h_pull|bodyweight|1|4|
Schulterdrücken|schultern|kg|v_push|barbell|2|3|shoulder
Kurzhantel-Schulterdrücken|schultern|kg|v_push|dumbbell|1|4|shoulder
Schulterpresse Maschine|schultern|kg|v_push|machine|1|4|shoulder
Smith-Schulterdrücken|schultern|kg|v_push|smith|1|4|shoulder
Arnold Press|schultern|kg|v_push|dumbbell|2|3|shoulder
Seitheben|schultern|kg|lateral|dumbbell|1|4|
Seitheben Kabel|schultern|kg|lateral|cable|1|5|
Seitheben Maschine|schultern|kg|lateral|machine|1|5|
Seitheben einarmig Kabel|schultern|kg|lateral|cable|1|5|
Face Pulls|schultern|kg|rear_delt|cable|1|4|
Reverse Butterfly|schultern|kg|rear_delt|machine|1|5|
Reverse Fly Kurzhantel|schultern|kg|rear_delt|dumbbell|1|4|
Reverse Fly Kabel|schultern|kg|rear_delt|cable|1|5|
Kniebeuge|beine|kg|squat|barbell|3|2|knee,lower_back
Frontkniebeuge|beine|kg|squat|barbell|3|2|knee,wrist
Smith-Kniebeuge|beine|kg|squat|smith|1|3|knee
Beinpresse|beine|kg|squat|machine|1|4|
Hackenschmidt-Kniebeuge|beine|kg|squat|machine|2|4|knee
Pendulum Squat|beine|kg|squat|machine|2|4|knee
Goblet Squat|beine|kg|squat|dumbbell|1|4|knee
Kreuzheben|ruecken|kg|hinge|barbell|3|1|lower_back,hip
Trap-Bar-Kreuzheben|beine|kg|hinge|barbell|2|2|lower_back
Rumänisches Kreuzheben|beine|kg|hinge|barbell|2|3|lower_back
Rumänisches Kreuzheben Kurzhantel|beine|kg|hinge|dumbbell|2|3|lower_back
Good Morning|beine|kg|hinge|barbell|3|2|lower_back
Kabel-Pull-Through|beine|kg|hinge|cable|1|4|
Ausfallschritte|beine|kg|lunge|dumbbell|2|3|knee
Bulgarian Split Squat|beine|kg|lunge|dumbbell|3|4|knee
Reverse Lunge|beine|kg|lunge|dumbbell|2|4|knee
Step-ups|beine|kg|lunge|dumbbell|1|4|knee
Smith Split Squat|beine|kg|lunge|smith|2|4|knee
Beinstrecker|beine|kg|leg_iso_front|machine|1|5|knee
Beinstrecker einbeinig|beine|kg|leg_iso_front|machine|1|5|knee
Beinbeuger|beine|kg|leg_iso_back|machine|1|5|
Beinbeuger sitzend|beine|kg|leg_iso_back|machine|1|5|
Beinbeuger einbeinig|beine|kg|leg_iso_back|machine|1|4|
Hyperextension|ruecken|koerper|leg_iso_back|bodyweight|1|3|lower_back
Nordic Curl|beine|koerper|leg_iso_back|bodyweight|3|3|
Hip Thrust|beine|kg|glutes|barbell|2|4|
Hip Thrust Maschine|beine|kg|glutes|machine|1|4|
Glute Bridge|beine|koerper|glutes|bodyweight|1|3|
Kabel-Kickback|beine|kg|glutes|cable|1|4|
Abduktoren-Maschine|beine|kg|glutes|machine|1|4|hip
Wadenheben|beine|kg|calves|machine|1|4|
Wadenheben sitzend|beine|kg|calves|machine|1|4|
Wadenheben an der Beinpresse|beine|kg|calves|machine|1|4|
Wadenheben einbeinig|beine|kg|calves|dumbbell|1|3|
Bizepscurls Kurzhantel|arme|kg|biceps|dumbbell|1|4|
Hammercurls|arme|kg|biceps|dumbbell|1|4|
SZ-Curls|arme|kg|biceps|barbell|1|3|elbow,wrist
Kabelcurls|arme|kg|biceps|cable|1|5|
Scott-Curls Maschine|arme|kg|biceps|machine|1|5|
Schrägbank-Curls|arme|kg|biceps|dumbbell|2|5|
Konzentrationscurls|arme|kg|biceps|dumbbell|1|4|
Trizepsdrücken Kabel|arme|kg|triceps|cable|1|5|
Trizepsdrücken Seil|arme|kg|triceps|cable|1|5|
Trizeps über Kopf|arme|kg|triceps|cable|1|5|elbow
Trizeps über Kopf Kurzhantel|arme|kg|triceps|dumbbell|1|4|elbow
Skull Crusher|arme|kg|triceps|barbell|2|3|elbow
Dips Maschine|arme|kg|triceps|machine|1|4|shoulder
Kickbacks Kurzhantel|arme|kg|triceps|dumbbell|1|3|
Enges Bankdrücken|arme|kg|triceps|barbell|2|3|elbow,wrist,shoulder
Unterarmstütz (Plank)|rumpf|koerper|core|bodyweight|1|3|
Seitlicher Unterarmstütz|rumpf|koerper|core|bodyweight|1|3|
Crunches|rumpf|koerper|core|bodyweight|1|3|
Reverse Crunch|rumpf|koerper|core|bodyweight|1|3|
Beinheben hängend|rumpf|koerper|core|bodyweight|2|3|
Knieheben hängend|rumpf|koerper|core|bodyweight|1|3|
Russian Twist|rumpf|kg|core|dumbbell|1|3|lower_back
Käfer (Dead Bug)|rumpf|koerper|core|bodyweight|1|3|
Pallof Press|rumpf|kg|core|cable|1|4|
Cable Crunch|rumpf|kg|core|cable|1|4|
Bauchmaschine|rumpf|kg|core|machine|1|4|
Ab Wheel|rumpf|koerper|core|bodyweight|3|3|lower_back
"""


def parse():
    out = []
    for line in RAW.strip().splitlines():
        name, group, unit, pattern, equip, diff, sfr, contra = line.split("|")
        out.append({"name": name, "muscle_group": group, "unit": unit, "pattern": pattern,
                    "equipment_type": equip, "difficulty": int(diff), "sfr": int(sfr),
                    "contraindications": contra or None})
    return out


EXERCISES = parse()
