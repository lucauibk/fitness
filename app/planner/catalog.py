"""Katalog einspielen: neue Uebungen einmalig anlegen, Eigenschaften nur in leere Felder schreiben.
Eigene Aenderungen, bestehende Ids und geloeschte Uebungen bleiben unangetastet."""
from .catalog_data import EXERCISES, LENGTHENED
from .muscles import COMPOUND

FLAG = "catalog_v3"


def apply(c):
    first = not c.execute("SELECT 1 FROM settings WHERE key=?", (FLAG,)).fetchone()
    for ex in EXERCISES:
        if first:   # nur einmal anlegen – was spaeter geloescht wird, kommt nicht wieder
            c.execute("INSERT OR IGNORE INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                      (ex["name"], ex["muscle_group"], ex["unit"]))
        kind = "compound" if ex["pattern"] in COMPOUND else "isolation"
        c.execute(
            "UPDATE exercises SET"
            " pattern = COALESCE(pattern, ?), kind = COALESCE(kind, ?),"
            " equipment_type = COALESCE(equipment_type, ?), difficulty = COALESCE(difficulty, ?),"
            " sfr = COALESCE(sfr, ?), contraindications = COALESCE(contraindications, ?)"
            " WHERE name = ?",
            (ex["pattern"], kind, ex["equipment_type"], ex["difficulty"], ex["sfr"],
             ex["contraindications"], ex["name"]))
    for name in LENGTHENED:
        c.execute("UPDATE exercises SET lengthened = 1 WHERE name = ? AND lengthened IS NULL", (name,))
    if first:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, datetime('now'))", (FLAG,))
