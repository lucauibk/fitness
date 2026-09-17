"""Koerpermessungen inkl. Einlesen einer Studio-Koerperanalyse per Screenshot."""
import base64
import binascii
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import activity, ai, body, db
from ..common import FIELD_NAMES, current_profile, owned
from .daily import _day

router = APIRouter(prefix="/api")
FIELD_NAMES.update(body.LABELS)
FIELD_NAMES.update({"image": "Bild", "media_type": "Bildformat"})

Num = Field(None, allow_inf_nan=False)
MAX_IMAGE_B64 = 6_000_000


class MeasurementIn(BaseModel):
    profile_id: int
    date: Optional[str] = None
    source: Literal["manuell", "vicoach"] = "manuell"
    notes: Optional[str] = Field(None, max_length=300)
    client_id: Optional[str] = Field(None, max_length=60)
    weight_kg: Optional[float] = Num
    body_fat_pct: Optional[float] = Num
    muscle_mass_kg: Optional[float] = Num
    water_pct: Optional[float] = Num
    visceral_fat: Optional[float] = Num
    bmr_kcal: Optional[float] = Num
    bio_age: Optional[float] = Num
    waist_cm: Optional[float] = Num
    hip_cm: Optional[float] = Num
    chest_cm: Optional[float] = Num
    arm_cm: Optional[float] = Num
    thigh_cm: Optional[float] = Num
    neck_cm: Optional[float] = Num


class ScanIn(BaseModel):
    image: str = Field(..., max_length=MAX_IMAGE_B64)
    media_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"


@router.get("/measurements")
def list_measurements(profile_id: int = Depends(current_profile)):
    out = body.history(profile_id)
    out["ai_available"] = ai.available()
    return out


@router.post("/measurements")
def add_measurement(data: MeasurementIn):
    profile_id = current_profile(data.profile_id)
    payload = data.model_dump()
    payload["date"] = _day(data.date)
    values, dropped = body.clean(payload)
    if dropped:
        raise HTTPException(400, f"{', '.join(dropped)}: Wert außerhalb des üblichen Bereichs.")
    if not values:
        raise HTTPException(400, "Bitte mindestens einen Messwert eintragen.")
    before = activity.reached_keys(profile_id)
    row, created = body.save(profile_id, payload)
    return {"measurement": row, "created": created,
            "milestones": activity.new_milestones(before, profile_id) if created else []}


@router.delete("/measurements/{mid}")
def delete_measurement(mid: int, profile_id: int = Depends(current_profile)):
    owned("body_measurements", mid, profile_id, "Diese Messung")
    db.delete_row("body_measurements", mid)
    return {"ok": True}


@router.post("/measurements/scan")
def scan(data: ScanIn, profile_id: int = Depends(current_profile)):
    """Nur ein Vorschlag – gespeichert wird erst, wenn der User bestaetigt."""
    if not ai.available():
        raise HTTPException(400, "Zum Einlesen braucht es einen KI-Schlüssel (ANTHROPIC_API_KEY).")
    raw = data.image.split(",", 1)[1] if data.image.startswith("data:") else data.image
    try:
        size = len(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, "Bild: ungültige Eingabe.") from e
    if size < 1000:
        raise HTTPException(400, "Bild: zu klein.")
    res = ai.read_measurement(raw, data.media_type)
    if "error" in res:
        raise HTTPException(422, res["error"])
    return res
