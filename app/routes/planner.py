"""Block-Ansicht des Plans: Woche X von Y, Soll/Ist-Volumen und was sich geaendert hat."""
from datetime import date, timedelta

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..common import current_profile
from ..planner import autoregulate, blocks, explain, generate, store
from ..planner.muscles import GROUPS, LABEL

router = APIRouter(prefix="/api/planner")


@router.get("/block")
def get_block(profile_id: int = Depends(current_profile)):
    split = db.active_split(profile_id)
    block = blocks.ensure(profile_id, split) if split else None
    if not block:
        return {"block": None, "volume": [], "events": []}
    st = blocks.state(block)
    week = st["week"]
    start = date.fromisoformat(block["start_date"]) + timedelta(weeks=week - 1)
    planned = autoregulate.planned_sets(block, split, week)
    actual, _ = autoregulate.actual_sets(profile_id, start, start + timedelta(days=6))
    return {
        "block": st,
        "volume": [{"group": g, "label": LABEL[g], "planned": planned[g], "done": actual[g],
                    "mav": GROUPS[g][3]} for g in GROUPS if planned[g] or actual[g]],
        "weeks": [{"week": w + 1, "sets": sum(autoregulate.planned_sets(block, split, w + 1).values())}
                  for w in range(block["weeks"])],
        "events": explain.recent(profile_id, 20),
    }


@router.post("/review")
def run_review(profile_id: int = Depends(current_profile)):
    return autoregulate.review(profile_id)


@router.post("/new-block")
def new_block(profile_id: int = Depends(current_profile)):
    """Neuen Block jetzt starten (statt das Blockende abzuwarten)."""
    split = db.active_split(profile_id)
    block = blocks.ensure(profile_id, split) if split else None
    if not block:
        return {"block": None}
    nb, _ = autoregulate.rollover(profile_id, block, split, date.today())
    return {"block": blocks.state(nb)}


class CreateIn(BaseModel):
    days: int = Field(3, ge=2, le=6)
    goal: Optional[str] = None
    minutes: Optional[int] = Field(None, ge=0, le=120)
    focus: Optional[list[str]] = None


@router.post("/create")
def create_plan(body: CreateIn, profile_id: int = Depends(current_profile)):
    """Ein Tipp: aus der Tageszahl (und dem, was im Profil steht) wird ein aktiver Plan mit Block.
    Alles Weitere sind Voreinstellungen, die sich spaeter anpassen lassen."""
    profile = db.profile_out(db.get_row("profiles", profile_id))
    minutes = body.minutes if body.minutes is not None else (profile.get("session_minutes") or 60)
    overrides = {"days": body.days, "weekdays": [], "goal": body.goal, "minutes": minutes,
                 "focus": body.focus}
    key = generate.DEFAULT_SPLIT.get(body.days)
    if not key:
        raise HTTPException(400, "Für diese Tageszahl gibt es keinen passenden Split.")
    plan = generate.generate(profile, key, overrides)
    db.update_row("profiles", profile_id, {"days_per_week": body.days})
    split = store.save_plan(profile_id, plan, activate=True)
    return {"split": split, "plan": {"name": plan["name"], "basis": plan["basis"]}}


class PainIn(BaseModel):
    area: str


@router.post("/pain")
def pain(body: PainIn, profile_id: int = Depends(current_profile)):
    try:
        return autoregulate.report_pain(profile_id, body.area)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
