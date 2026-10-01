"""Block-Ansicht des Plans: Woche X von Y, Soll/Ist-Volumen und was sich geaendert hat."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from .. import db
from ..common import current_profile
from ..planner import autoregulate, blocks, explain
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
