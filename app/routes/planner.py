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
    days: int = Field(3, ge=2, le=6)              # gewuenschte Einheiten pro Woche
    available: Optional[list[int]] = None          # freie Wochentage (0 = Montag); leer = jeder Tag
    goal: Optional[str] = None
    minutes: Optional[int] = Field(None, ge=0, le=120)
    focus: Optional[list[str]] = None
    experience: Optional[str] = None
    split: Optional[str] = None          # gewaehlte Aufteilung; sonst die empfohlene


def _options(profile, sessions, weekdays, goal, experience, minutes, focus=None):
    return generate.split_options(profile, {"days": sessions, "weekdays": weekdays, "goal": goal,
                                            "experience": experience, "minutes": minutes, "focus": focus})


@router.post("/create")
def create_plan(body: CreateIn, profile_id: int = Depends(current_profile)):
    """Ein Tipp: aus der Tageszahl (und dem, was im Profil steht) wird ein aktiver Plan mit Block.
    Alles Weitere sind Voreinstellungen, die sich spaeter anpassen lassen."""
    if body.experience in generate.EXPERIENCE:
        db.update_row("profiles", profile_id, {"experience": body.experience, "experience_auto": None})
    profile = db.profile_out(db.get_row("profiles", profile_id))
    minutes = body.minutes if body.minutes is not None else (profile.get("session_minutes") or 60)
    overrides = {"days": body.days, "weekdays": [], "goal": body.goal, "minutes": minutes,
                 "focus": body.focus}
    available = db.parse_weekdays(body.available) if body.available is not None else db.parse_weekdays(profile.get("train_weekdays"))
    sessions = max(2, min(body.days, len(available) or 7))      # nie mehr Einheiten als freie Tage
    weekdays = generate.pick_days(available, sessions)
    overrides.update({"days": sessions, "weekdays": weekdays})
    options = _options(profile, sessions, weekdays, body.goal, body.experience, minutes, body.focus)
    keys = [o["key"] for o in options]
    key = body.split if body.split in keys else (keys[0] if keys else generate.DEFAULT_SPLIT.get(sessions))
    if not key:
        raise HTTPException(400, "Für diese Tageszahl gibt es keinen passenden Split.")
    # Jeder neue Plan nimmt andere Isolationsuebungen als der vorige (Grunduebungen bleiben)
    variation = db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"]
    plan = generate.generate(profile, key, overrides, variation=variation)
    if any(b - a == 1 for a, b in zip(weekdays, weekdays[1:])) and key.startswith("gk"):
        plan["basis"].append("Einige Trainingstage liegen direkt hintereinander – bei Ganzkörper-Einheiten "
                             "ist das nicht ideal. Mit einem freien Tag dazwischen läuft es besser.")
    db.update_row("profiles", profile_id, {"days_per_week": sessions,
                                           "train_weekdays": db.format_weekdays(available)})
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


@router.get("/suggest")
def suggest(available: str = "", minutes: Optional[int] = None, goal: Optional[str] = None,
            experience: Optional[str] = None, days: Optional[int] = None,
            profile_id: int = Depends(current_profile)):
    """Empfehlung fuer die Einheiten pro Woche und die dafuer gewaehlten Tage – fuer die Anzeige im Assistenten."""
    profile = db.profile_out(db.get_row("profiles", profile_id))
    free = db.parse_weekdays(available)
    n = generate.recommended_days(experience or profile.get("experience"),
                                  goal or profile.get("goal") or "aufbau",
                                  minutes if minutes is not None else profile.get("session_minutes") or 60,
                                  len(free) or 7)
    chosen = max(2, min(days or n, len(free) or 7))
    weekdays = generate.pick_days(free, chosen)
    mins = minutes if minutes is not None else profile.get("session_minutes") or 60
    options = _options(profile, chosen, weekdays, goal, experience, mins)
    return {"recommended": n, "max": len(free) or 7, "weekdays": weekdays,
            "splits": [{"key": o["key"], "name": o["name"], "text": o["text"], "recommended": o["recommended"],
                        "pro": o.get("pro", [])[:2], "contra": o.get("contra", [])[:1]} for o in options]}
