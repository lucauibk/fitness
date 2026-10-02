"""Datenexport als CSV – je Bereich eine Datei, fuer Excel/Numbers im deutschen Format
(Semikolon, Dezimalkomma, UTF-8 mit BOM). Die Daten liegen sonst nur in fitness.db auf dem Pi."""
import csv
import io

from . import db, stats

# Bereich -> (Dateiname, Spaltenueberschriften, SQL mit einem ?-Platzhalter fuer das Profil)
EXPORTS = {
    "saetze": ("saetze.csv",
               ["Datum", "Einheit", "Übung", "Satz", "Wiederholungen", "Gewicht (kg)", "RPE", "Aufwärmsatz"],
               "SELECT COALESCE(w.started_at, w.finished_at), COALESCE(d.label, w.title, 'Training'), e.name,"
               " s.set_no, s.reps, s.weight, s.rpe, CASE WHEN s.warmup THEN 'ja' ELSE '' END"
               " FROM sets s JOIN workouts w ON w.id = s.workout_id"
               " JOIN exercises e ON e.id = s.exercise_id"
               " LEFT JOIN split_days d ON d.id = w.split_day_id"
               " WHERE w.profile_id=? ORDER BY w.started_at, w.id, s.id"),
    "cardio": ("cardio.csv",
               ["Datum", "Aktivität", "Minuten", "Strecke (km)", "kcal", "Puls ⌀", "Notiz"],
               "SELECT date, activity, minutes, distance_km, kcal, avg_hr, notes"
               " FROM cardio WHERE profile_id=? ORDER BY date, id"),
    "koerper": ("koerper.csv",
                ["Datum", "Quelle", "Gewicht (kg)", "Körperfett (%)", "Muskelmasse (kg)", "Taille (cm)",
                 "Hüfte (cm)", "Brust (cm)", "Arm (cm)", "Oberschenkel (cm)"],
                # Gewicht allein (Waage) und volle Messungen in einer Tabelle
                "SELECT date, 'Gewicht', kg, NULL, NULL, NULL, NULL, NULL, NULL, NULL"
                " FROM bodyweight WHERE profile_id=:p"
                " UNION ALL SELECT date, source, weight_kg, body_fat_pct, muscle_mass_kg, waist_cm,"
                " hip_cm, chest_cm, arm_cm, thigh_cm FROM body_measurements WHERE profile_id=:p"
                " ORDER BY 1"),
    "ernaehrung": ("ernaehrung.csv",
                   ["Datum", "Mahlzeit", "Lebensmittel", "Gramm", "kcal", "Eiweiß (g)", "Kohlenhydrate (g)", "Fett (g)"],
                   "SELECT date, meal, name, grams, kcal, protein, carbs, fat"
                   " FROM food_log WHERE profile_id=? ORDER BY date, id"),
}


def _cell(v):
    if isinstance(v, float):
        return f"{v:g}".replace(".", ",")
    return "" if v is None else v


def to_csv(kind, profile_id):
    filename, header, sql = EXPORTS[kind]
    params = {"p": profile_id} if ":p" in sql else (profile_id,)
    with db.connect() as c:
        rows = c.execute(sql, params).fetchall()
    if kind == "saetze":        # Zeitstempel sind UTC – im Export steht der Wiener Kalendertag
        rows = [(stats._local_date(r[0]) or r[0], *r[1:]) for r in rows]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    writer.writerows([_cell(v) for v in row] for row in rows)
    return filename, "﻿" + out.getvalue()
