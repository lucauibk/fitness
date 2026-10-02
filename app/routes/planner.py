"""Block-Ansicht des Plans: Woche X von Y, Soll/Ist-Volumen und was sich geaendert hat."""
import threading
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

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


Goal = Literal["aufbau", "kraft", "abnehmen", "fitness"]
Experience = Literal["einsteiger", "fortgeschritten", "erfahren"]
FocusKey = Literal["brust", "ruecken", "schultern", "arme", "beine", "po", "rumpf"]
_lock = threading.Lock()                     # ein Plan wird zur Zeit erstellt (Doppeltipp, zwei Tabs)
_done = {}                                   # client_id -> Antwort: derselbe Tipp erzeugt keinen zweiten Plan


class CreateIn(BaseModel):
    days: int = Field(3, ge=2, le=6)                                  # gewuenschte Einheiten pro Woche
    available: list[int] = Field(default_factory=list, max_length=7)  # freie Wochentage (0 = Montag); leer = jeder Tag
    goal: Optional[Goal] = None
    experience: Optional[Experience] = None
    focus: Optional[list[FocusKey]] = Field(None, max_length=3)
    limitations: Optional[str] = Field(None, max_length=500)
    split: Optional[str] = Field(None, max_length=20)                 # gewaehlte Aufteilung; sonst die empfohlene
    client_id: Optional[str] = Field(None, max_length=60)

    @field_validator("available")
    @classmethod
    def _weekdays(cls, v):
        if any(not 0 <= d <= 6 for d in v):
            raise ValueError("Wochentage müssen zwischen 0 (Montag) und 6 (Sonntag) liegen.")
        return sorted(set(v))


def _options(profile, sessions, weekdays, goal, experience, focus=None):
    return generate.split_options(profile, {"days": sessions, "weekdays": weekdays, "goal": goal,
                                            "experience": experience, "focus": focus})


def _effective(profile, body):
    """Profil mit den Angaben aus dem Assistenten – noch nichts gespeichert."""
    focus = body.focus if body.focus is not None else profile.get("focus") or []
    limitations = body.limitations if body.limitations is not None else profile.get("limitations")
    return {**profile, "goal": body.goal or profile.get("goal") or "aufbau",
            "experience": body.experience or profile.get("experience") or "einsteiger",
            "focus": list(focus), "limitations": (limitations or "").strip() or None}


@router.post("/create")
def create_plan(body: CreateIn, profile_id: int = Depends(current_profile)):
    """Aus Erfahrung, Zielen, freien Tagen und gewuenschten Einheiten wird ein aktiver Plan mit Block.
    Erst wird alles berechnet und geprueft, dann in einem Rutsch gespeichert – ein Fehler hinterlaesst
    weder ein halb geaendertes Profil noch einen halben Plan."""
    key_id = (profile_id, body.client_id) if body.client_id else None
    with _lock:
        if key_id and key_id in _done:
            return _done[key_id]
        if 0 < len(body.available) < 2:
            raise HTTPException(400, "Bitte mindestens zwei freie Tage wählen – oder keinen, dann ist jeder Tag möglich.")
        base = db.profile_out(db.get_row("profiles", profile_id))
        profile = _effective(base, body)
        sessions = max(2, min(body.days, len(body.available) or 7))   # nie mehr Einheiten als freie Tage
        weekdays = generate.pick_days(body.available, sessions)
        options = _options(profile, sessions, weekdays, profile["goal"], profile["experience"], profile["focus"])
        keys = [o["key"] for o in options]
        if body.split and body.split not in keys:
            raise HTTPException(400, "Diese Aufteilung passt nicht zu den gewählten Einheiten pro Woche.")
        key = body.split or (keys[0] if keys else generate.DEFAULT_SPLIT.get(sessions))
        if not key:
            raise HTTPException(400, "Für diese Tageszahl gibt es keinen passenden Split.")
        variation = db.one("SELECT COUNT(*) AS n FROM splits WHERE profile_id=?", (profile_id,))["n"]
        try:
            plan = generate.generate(profile, key, {"days": sessions, "weekdays": weekdays}, variation=variation)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if any(b - a == 1 for a, b in zip(weekdays, weekdays[1:])) and key.startswith("gk"):
            plan["basis"].append("Einige Trainingstage liegen direkt hintereinander – bei Ganzkörper-Einheiten "
                                 "ist das nicht ideal. Mit einem freien Tag dazwischen läuft es besser.")
        fields = {"goal": profile["goal"],
                  "focus": ",".join(profile["focus"]) or None, "limitations": profile["limitations"],
                  "days_per_week": sessions, "train_weekdays": db.format_weekdays(weekdays),
                  "available_weekdays": db.format_weekdays(body.available)}
        if profile["experience"] != base.get("experience"):
            fields.update({"experience": profile["experience"], "experience_auto": None})
        db.update_row("profiles", profile_id, fields)
        split = store.save_plan(profile_id, plan, activate=True)
        result = {"split": split, "plan": {"name": plan["name"], "basis": plan["basis"]}}
        if key_id:
            _done[key_id] = result
            if len(_done) > 50:
                _done.pop(next(iter(_done)))
        return result


class PainIn(BaseModel):
    area: str


@router.post("/pain")
def pain(body: PainIn, profile_id: int = Depends(current_profile)):
    try:
        return autoregulate.report_pain(profile_id, body.area)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/suggest")
def suggest(available: str = Query("", max_length=20),
            goal: Optional[Goal] = None, experience: Optional[Experience] = None,
            days: Optional[int] = Query(None, ge=2, le=6), focus: Optional[str] = Query(None, max_length=80),
            profile_id: int = Depends(current_profile)):
    """Empfehlung fuer die Einheiten pro Woche, die Tage und die Aufteilungen – mit denselben Eingaben
    wie /create, damit der vorgewaehlte Chip genau das ist, was erstellt wird."""
    base = db.profile_out(db.get_row("profiles", profile_id))
    free = db.parse_weekdays(available)
    body = CreateIn(days=days or 3, available=free if len(free) >= 2 else [], goal=goal, experience=experience,
                    focus=db.parse_focus(focus) if focus is not None else None)
    profile = _effective(base, body)
    n = generate.recommended_days(profile["experience"], profile["goal"], len(body.available) or 7)
    chosen = max(2, min(days or n, len(body.available) or 7))
    weekdays = generate.pick_days(body.available, chosen)
    options = _options(profile, chosen, weekdays, profile["goal"], profile["experience"], profile["focus"])
    return {"recommended": n, "max": len(body.available) or 7, "weekdays": weekdays,
            "splits": [{"key": o["key"], "name": o["name"], "text": o["text"], "recommended": o["recommended"],
                        "pro": o.get("pro", [])[:2], "contra": o.get("contra", [])[:1]} for o in options]}
