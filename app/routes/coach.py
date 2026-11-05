"""Coach-Chat (Logik in app/coach_chat.py). Die Antwort kommt als Server-Sent-Events, damit
der Text waehrend des Schreibens erscheint – bis zum ersten Wort vergehen auf dem Pi gut 5 s."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import coach_chat
from ..common import FIELD_NAMES, current_profile

router = APIRouter(prefix="/api")
FIELD_NAMES.update({"text": "Nachricht"})


class SessionSet(BaseModel):
    reps: Optional[int] = Field(None, ge=0, le=200)
    weight: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    rpe: Optional[float] = Field(None, ge=1, le=10, allow_inf_nan=False)
    done: bool = False
    warmup: bool = False


class SessionExercise(BaseModel):
    exercise_id: int
    reps_min: Optional[int] = Field(None, ge=1, le=100)
    reps_max: Optional[int] = Field(None, ge=1, le=100)
    sets: list[SessionSet] = Field(default_factory=list, max_length=20)


class SessionBlock(BaseModel):
    week: Optional[int] = Field(None, ge=1, le=52)
    weeks: Optional[int] = Field(None, ge=1, le=52)
    rir: Optional[float] = Field(None, ge=0, le=10, allow_inf_nan=False)
    deload: Optional[bool] = None


class SessionIn(BaseModel):
    """Entwurf der laufenden Einheit (liegt nur auf dem Geraet) – Namen holt der Server selbst."""
    title: Optional[str] = Field(None, max_length=80)
    started_at: Optional[str] = Field(None, max_length=40)
    block: Optional[SessionBlock] = None
    notes: Optional[str] = Field(None, max_length=500)
    current: Optional[int] = Field(None, ge=-1, le=30)
    exercises: list[SessionExercise] = Field(default_factory=list, max_length=30)


class ChatIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=coach_chat.MAX_QUESTION)
    session: Optional[SessionIn] = None


class SeenIn(BaseModel):
    id: int = Field(..., ge=0)


@router.get("/coach/messages")
def get_messages(profile_id: int = Depends(current_profile),
                 before: Optional[int] = Query(None, ge=1)):
    return {"messages": coach_chat.messages(profile_id, before),
            "unread": coach_chat.unread(profile_id), "page": coach_chat.PAGE}


@router.post("/coach/seen")
def post_seen(body: SeenIn, profile_id: int = Depends(current_profile)):
    coach_chat.mark_seen(profile_id, body.id)
    return {"unread": coach_chat.unread(profile_id)}


@router.post("/coach/messages/{message_id}/actions/{index}/{what}")
def post_action(message_id: int, index: int, what: str, profile_id: int = Depends(current_profile)):
    """Vorschlag aus dem Chat uebernehmen (apply) oder zuruecknehmen (undo)."""
    if what not in ("apply", "undo"):
        raise HTTPException(404, "Unbekannte Aktion.")
    try:
        msg = coach_chat.apply_action(profile_id, message_id, index, undo=what == "undo")
    except coach_chat.ActionError as e:
        raise HTTPException(409, str(e)) from e
    return {"message": msg}


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/coach/chat")
def post_chat(body: ChatIn, profile_id: int = Depends(current_profile)):
    question = body.text.strip()
    if not question:
        raise HTTPException(400, "Bitte eine Nachricht eingeben.")
    gen = coach_chat.answer(profile_id, question, body.session.model_dump() if body.session else None)
    # Schon hier anlaufen lassen: "besetzt" soll ein echter 429 sein und kein Event im Stream, und
    # ein gestarteter Generator gibt die Sperre auch frei, wenn die Verbindung vorher abreisst.
    try:
        first = next(gen)
    except coach_chat.Busy as e:
        raise HTTPException(429, "Der Coach antwortet gerade schon – einen Moment.") from e

    def events():
        try:
            for kind, payload in _chain(first, gen):
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                else:
                    yield _sse(kind, {"message": payload})
        finally:
            gen.close()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _chain(first, gen):
    yield first
    yield from gen
