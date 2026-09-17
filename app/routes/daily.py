"""Tagesgeschaeft: Check-in, Cardio, Mobility, Erfolge."""
from datetime import date, timedelta
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import activity, db, mobility, readiness
from ..common import FIELD_NAMES, current_profile, insert_once, owned
from ..stats import today

router = APIRouter(prefix="/api")
FIELD_NAMES.update({"sleep": "Schlaf", "stress": "Stress", "energy": "Energie",
                    "soreness": "Muskelkater", "activity": "Aktivität", "minutes": "Dauer",
                    "distance_km": "Strecke", "avg_hr": "Puls", "routine": "Routine"})

Scale = Field(..., ge=1, le=5)


def _day(value):
    if not value:
        return today().isoformat()
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(400, "Datum: ungültige Eingabe.") from e
    if d > today() + timedelta(days=1):
        raise HTTPException(400, "Datum liegt in der Zukunft.")
    return d.isoformat()


# ------------------------------------------------------------ Check-in
class CheckinIn(BaseModel):
    profile_id: int
    date: Optional[str] = None
    sleep: int = Scale
    energy: int = Scale
    stress: int = Scale
    soreness: Dict[str, int] = Field(default_factory=dict)
    client_id: Optional[str] = Field(None, max_length=60)


@router.get("/checkin")
def get_checkin(profile_id: int = Depends(current_profile), day: Optional[str] = None):
    return {"checkin": readiness.get(profile_id, _day(day))}


@router.post("/checkin")
def post_checkin(body: CheckinIn):
    profile_id = current_profile(body.profile_id)
    for g, v in body.soreness.items():
        if g not in readiness.GROUPS or not 0 <= v <= 3:
            raise HTTPException(400, "Muskelkater: ungültiger Wert.")
    return {"checkin": readiness.save(profile_id, _day(body.date), body.sleep, body.energy,
                                      body.stress, body.soreness, body.client_id)}


# -------------------------------------------------------------- Cardio
class CardioIn(BaseModel):
    profile_id: int
    date: Optional[str] = None
    activity: Literal[tuple(activity.ACTIVITIES)]  # type: ignore[valid-type]
    minutes: float = Field(..., gt=0, le=600, allow_inf_nan=False)
    distance_km: Optional[float] = Field(None, ge=0, le=500, allow_inf_nan=False)
    kcal: Optional[float] = Field(None, ge=0, le=10000, allow_inf_nan=False)
    avg_hr: Optional[int] = Field(None, ge=30, le=240)
    notes: Optional[str] = Field(None, max_length=300)
    client_id: Optional[str] = Field(None, max_length=60)


@router.get("/cardio/activities")
def cardio_activities():
    return [{"key": k, "label": label, "met": met} for k, (label, met) in activity.ACTIVITIES.items()]


@router.get("/cardio")
def list_cardio(profile_id: int = Depends(current_profile), limit: int = Query(50, ge=1, le=500)):
    rows = db.rows("SELECT * FROM cardio WHERE profile_id=? ORDER BY date DESC, id DESC LIMIT ?",
                   (profile_id, limit))
    for r in rows:
        r["label"] = activity.ACTIVITIES.get(r["activity"], (r["activity"], 0))[0]
    return rows


@router.post("/cardio")
def add_cardio(body: CardioIn):
    profile_id = current_profile(body.profile_id)
    before = activity.reached_keys(profile_id)
    data = body.model_dump()
    data["date"] = _day(body.date)
    data["notes"] = (data["notes"] or "").strip() or None
    if data["kcal"] is None:
        data["kcal"] = activity.cardio_kcal(body.activity, body.minutes,
                                            activity.latest_weight(profile_id))
    row, created = insert_once("cardio", data)
    return {"cardio": row, "created": created,
            "milestones": activity.new_milestones(before, profile_id) if created else []}


@router.delete("/cardio/{cardio_id}")
def delete_cardio(cardio_id: int, profile_id: int = Depends(current_profile)):
    owned("cardio", cardio_id, profile_id, "Diese Cardio-Einheit")
    db.delete_row("cardio", cardio_id)
    return {"ok": True}


# ------------------------------------------------------------ Mobility
class MobilityIn(BaseModel):
    profile_id: int
    routine: str = Field(..., max_length=30)
    date: Optional[str] = None
    client_id: Optional[str] = Field(None, max_length=60)


@router.get("/mobility")
def list_mobility():
    return mobility.all_routines()


@router.get("/mobility/{key}")
def get_mobility(key: str):
    r = mobility.routine(key)
    if not r:
        raise HTTPException(404, "Diese Routine gibt es nicht.")
    return r


@router.post("/mobility/log")
def log_mobility(body: MobilityIn):
    profile_id = current_profile(body.profile_id)
    if not mobility.routine(body.routine):
        raise HTTPException(400, "Routine: ungültiger Wert.")
    before = activity.reached_keys(profile_id)
    row, created = insert_once("mobility_log", {"profile_id": profile_id, "routine": body.routine,
                                                "date": _day(body.date), "client_id": body.client_id})
    return {"log": row, "created": created,
            "milestones": activity.new_milestones(before, profile_id) if created else []}


# -------------------------------------------------------------- Erfolge
@router.get("/achievements")
def achievements(profile_id: int = Depends(current_profile)):
    return activity.achievements(profile_id)
