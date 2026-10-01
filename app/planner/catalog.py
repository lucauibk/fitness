"""Katalog einspielen: neue Uebungen anlegen, Eigenschaften nur in leere Felder schreiben.
Eigene Aenderungen und bestehende Ids bleiben unangetastet."""
from .catalog_data import EXERCISES
from .muscles import COMPOUND


def apply(c):
    for ex in EXERCISES:
        kind = "compound" if ex["pattern"] in COMPOUND else "isolation"
        c.execute("INSERT OR IGNORE INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                  (ex["name"], ex["muscle_group"], ex["unit"]))
        c.execute(
            "UPDATE exercises SET"
            " pattern = COALESCE(pattern, ?), kind = COALESCE(kind, ?),"
            " equipment_type = COALESCE(equipment_type, ?), difficulty = COALESCE(difficulty, ?),"
            " sfr = COALESCE(sfr, ?), contraindications = COALESCE(contraindications, ?)"
            " WHERE name = ?",
            (ex["pattern"], kind, ex["equipment_type"], ex["difficulty"], ex["sfr"],
             ex["contraindications"], ex["name"]))
