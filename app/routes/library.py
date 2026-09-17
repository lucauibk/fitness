"""Uebungsbibliothek: Details mit Anleitung und eigener Bestleistung."""
from fastapi import APIRouter, Depends

from .. import db, stats
from ..common import _found, current_profile
from ..exercise_info import MUSCLES

router = APIRouter(prefix="/api")


def _split(value):
    return [m for m in (value or "").split(",") if m]


@router.get("/muscles")
def muscles():
    return MUSCLES


@router.get("/exercises/{exercise_id}")
def exercise_detail(exercise_id: int, profile_id: int = Depends(current_profile)):
    ex = _found(db.get_row("exercises", exercise_id))
    ex["instructions"] = [s for s in (ex.get("instructions") or "").split("\n") if s.strip()]
    ex["mistakes"] = [s for s in (ex.get("mistakes") or "").split("\n") if s.strip()]
    ex["muscles_primary"] = _split(ex.get("muscles_primary"))
    ex["muscles_secondary"] = _split(ex.get("muscles_secondary"))
    ex["video_url"] = ("https://www.youtube.com/results?search_query="
                       + ((ex.get("video_query") or f"{ex['name']} richtige Ausführung")
                          .replace(" ", "+")))
    ex["best"] = next((b for b in stats.best_per_exercise(profile_id)
                       if b["exercise_id"] == exercise_id), None)
    ex["history"] = stats.history(profile_id, exercise_id)[-12:]
    ex["used_in"] = db.rows(
        "SELECT DISTINCT s.name AS split, d.label AS day FROM split_exercises se"
        " JOIN split_days d ON d.id = se.split_day_id JOIN splits s ON s.id = d.split_id"
        " WHERE se.exercise_id=? AND s.profile_id=?", (exercise_id, profile_id))
    return ex
