"""Gemeinsame Helfer fuer alle API-Bereiche: Profil-Pruefung, Eingabe-Bereinigung,
deutsche Feldnamen fuer Fehlermeldungen und idempotentes Speichern ueber client_id."""
from typing import Optional

from fastapi import HTTPException, Query

from . import db

FIELD_NAMES = {"name": "Name", "label": "Bezeichnung", "reps": "Wiederholungen",
               "weight": "Gewicht", "kg": "Gewicht", "date": "Datum", "rpe": "Anstrengung",
               "target_sets": "Sätze", "target_reps_min": "Wiederholungen (von)",
               "target_reps_max": "Wiederholungen (bis)",
               "target_weight": "Gewicht", "weekdays": "Wochentage", "muscle_group": "Muskelgruppe",
               "exercise_id": "Übung", "split_day_id": "Trainingstag", "split_id": "Split",
               "profile_id": "Profil", "unit": "Einheit", "position": "Reihenfolge",
               "birth_year": "Geburtsjahr", "sex": "Geschlecht", "height_cm": "Größe",
               "goal": "Ziel", "experience": "Erfahrung", "days_per_week": "Trainingstage",
               "session_minutes": "Dauer", "limitations": "Einschränkungen",
               "pattern": "Bewegungsmuster", "option": "Split",
               "train_weekdays": "Trainingstage", "focus": "Fokus"}
# Felder, die per PATCH vorhanden sein duerfen, aber nie null werden
NOT_NULL = ("name", "label", "kg", "date", "exercise_id", "split_id", "split_day_id",
            "profile_id")



# Weitere Bereiche melden ihre Feldnamen hier an (FIELD_NAMES.update(...)).

def _found(row):
    if row is None:
        raise HTTPException(404, "Nicht gefunden.")
    return row


def _data(body, required=()):
    # exclude_unset: im PATCH nur mitgeschickte Felder aendern, explizites null leert eins.
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if isinstance(value, str):
            data[key] = value.strip()
    for key in ("archived", "active", "nutrition_auto"):
        if key in data and data[key] is not None:
            data[key] = int(data[key])
    for key in required:
        if data.get(key) in (None, ""):
            raise HTTPException(400, f"{FIELD_NAMES.get(key, key)} fehlt.")
    for key in NOT_NULL:
        if key in data and data[key] in (None, ""):
            raise HTTPException(400, f"{FIELD_NAMES.get(key, key)} darf nicht leer sein.")
    if data.get("exercise_id") is not None and not db.exists("exercises", data["exercise_id"]):
        raise HTTPException(400, "Diese Übung gibt es nicht (mehr).")
    if data.get("split_day_id") is not None and not db.exists("split_days", data["split_day_id"]):
        raise HTTPException(400, "Diesen Trainingstag gibt es nicht (mehr).")
    return data


def current_profile(profile: Optional[int] = Query(None)) -> int:
    """Alle Daten sind an ein Profil gebunden. Die Prüfung passiert hier, damit nie
    versehentlich die Sätze der anderen Person in einer Auswertung landen."""
    if profile is None:
        raise HTTPException(400, "Kein Profil gewählt.")
    if not db.exists("profiles", profile):
        raise HTTPException(400, "Dieses Profil gibt es nicht (mehr).")
    return profile




def owned(table, row_id, profile_id, what="Eintrag"):
    """Zeile holen und pruefen, dass sie zum Profil gehoert."""
    row = _found(db.get_row(table, row_id))
    if row.get("profile_id") != profile_id:
        raise HTTPException(403, f"{what} gehört zu einem anderen Profil.")
    return row


def insert_once(table, data):
    """Einfuegen mit client_id: kommt derselbe Eintrag aus dem Ausgang ein zweites Mal,
    wird der vorhandene zurueckgegeben. Rueckgabe: (zeile, neu_angelegt)."""
    cid = data.get("client_id")
    if cid:
        have = db.one(f"SELECT id FROM {table} WHERE client_id=?", (cid,))
        if have:
            return db.get_row(table, have["id"]), False
    return db.insert_row(table, data), True
