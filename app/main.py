"""FastAPI-App der Fitness-Anwendung: liefert die Oberfläche und die JSON-Daten."""
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from . import (activity, ai, analysis, checker, daily_view, db, export,  # noqa: E402
               readiness, session_feedback, stats)
from .common import FIELD_NAMES, NOT_NULL, _data, _found, current_profile  # noqa: E402,F401
from .planner import generate  # noqa: E402
from .planner.profile_model import injuries_from  # noqa: E402

STATIC = Path(__file__).parent.parent / "static"
log = logging.getLogger("fitness")


BACKUP_INTERVAL_S = 24 * 3600


async def _daily_backup():
    """Taegliche Sicherung, solange der Dienst laeuft. Ohne das haengt die gesamte
    Trainingshistorie an einer einzelnen SD-Karte im Pi."""
    while True:
        try:
            db.backup()
            log.info("Backup geschrieben")
        except Exception:
            log.exception("Backup fehlgeschlagen")
        await asyncio.sleep(BACKUP_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    log.info("Fitness-App bereit (%s)", db.DB_PATH)
    backup_task = asyncio.create_task(_daily_backup())
    yield
    backup_task.cancel()


app = FastAPI(title="Fitness", lifespan=lifespan)

from .routes import body as body_routes, daily, library, nutrition as nutrition_routes, planner as planner_routes  # noqa: E402

for _router in (daily.router, library.router, nutrition_routes.router, body_routes.router,
                planner_routes.router):
    app.include_router(_router)

# ------------------------------------------------------- Host-Pruefung
# Schutz gegen DNS-Rebinding: eine fremde Webseite koennte sonst ueber einen eigenen
# Hostnamen Anfragen an die API im Tailnet schicken.
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def host_allowed(host):
    extra = [h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    if "*" in extra:
        return True
    host = (host or "").lower()
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.strip("[]")
    if name in ("localhost", *extra) or name.endswith(".ts.net") or name.endswith(".local"):
        return True
    if "." not in name and name:
        return True  # nackter Rechnername (MagicDNS "raspberrypi")
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip in TAILSCALE_NET


@app.middleware("http")
async def check_host(request: Request, call_next):
    if not host_allowed(request.headers.get("host", "")):
        log.warning("Anfrage mit unbekanntem Host abgelehnt: %s", request.headers.get("host"))
        return PlainTextResponse("Unbekannter Host", status_code=400)
    response = await call_next(request)
    # Ohne Cache-Control cacht Safari (vor allem die Homescreen-App) nach eigener Heuristik
    # und zeigt nach einem Deploy tagelang den alten Stand.
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif not request.url.path.endswith((".png", ".svg")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    err = exc.errors()[0] if exc.errors() else {}
    field = next((str(p) for p in reversed(err.get("loc", ())) if isinstance(p, str)), "")
    label = FIELD_NAMES.get(field, field or "Eingabe")
    kind = err.get("type", "")
    if kind.startswith("greater_than") or kind.startswith("less_than"):
        msg = f"{label}: Wert außerhalb des erlaubten Bereichs."
    elif kind == "literal_error":
        msg = f"{label}: ungültiger Wert."
    elif kind == "missing":
        msg = f"{label} fehlt."
    else:
        msg = f"{label}: ungültige Eingabe."
    return JSONResponse({"detail": msg}, status_code=400)


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error(request: Request, exc: sqlite3.IntegrityError):
    text = str(exc)
    if "exercises.name" in text:
        return JSONResponse({"detail": "Eine Übung mit diesem Namen gibt es schon."}, status_code=409)
    if "profiles.name" in text:
        return JSONResponse({"detail": "Ein Profil mit diesem Namen gibt es schon."}, status_code=409)
    return JSONResponse({"detail": "Eintrag widerspricht vorhandenen Daten."}, status_code=409)


def _check_range(data, old=None):
    """"von" darf nicht über "bis" liegen – auch nicht, wenn per PATCH nur eines der
    beiden Felder geändert wird."""
    merged = {**(old or {}), **data}
    lo, hi = merged.get("target_reps_min"), merged.get("target_reps_max")
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(400, "Wiederholungen: „von“ darf nicht größer als „bis“ sein.")
    return data


def _owns_split(split_id, profile_id):
    split = _found(db.get_row("splits", split_id))
    if split["profile_id"] != profile_id:
        raise HTTPException(403, "Dieser Split gehört zu einem anderen Profil.")
    return split


def _owns_day(day_id, profile_id):
    day = _found(db.get_row("split_days", day_id))
    _owns_split(day["split_id"], profile_id)
    return day


# ------------------------------------------------------------- Modelle
Group = Literal["brust", "ruecken", "beine", "schultern", "arme", "rumpf"]
Unit = Literal["kg", "koerper"]


Goal = Literal["aufbau", "kraft", "abnehmen", "fitness"]
Experience = Literal["einsteiger", "fortgeschritten", "erfahren"]
Focus = Literal["brust", "ruecken", "schultern", "arme", "beine", "po", "rumpf"]
Pattern = Literal["h_push", "v_push", "chest_iso", "h_pull", "v_pull", "rear_delt", "lateral",
                  "squat", "hinge", "lunge", "leg_iso_front", "leg_iso_back", "glutes",
                  "calves", "biceps", "triceps", "core"]


class ProfileIn(BaseModel):
    name: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = Field(None, max_length=20)
    birth_year: Optional[int] = Field(None, ge=1920, le=2020)
    sex: Optional[Literal["weiblich", "männlich", "divers"]] = None
    height_cm: Optional[float] = Field(None, ge=100, le=250, allow_inf_nan=False)
    goal: Optional[Goal] = None
    experience: Optional[Experience] = None
    days_per_week: Optional[int] = Field(None, ge=2, le=6)
    session_minutes: Optional[int] = Field(None, ge=30, le=120)
    limitations: Optional[str] = Field(None, max_length=500)
    train_weekdays: Optional[List[Annotated[int, Field(ge=0, le=6)]]] = Field(None, max_length=7)
    focus: Optional[List[Focus]] = Field(None, max_length=3)
    activity_level: Optional[Literal["niedrig", "mittel", "hoch"]] = None
    nutrition_auto: Optional[bool] = None
    kcal_goal: Optional[int] = Field(None, ge=800, le=8000)
    protein_goal: Optional[int] = Field(None, ge=0, le=500)
    carbs_goal: Optional[int] = Field(None, ge=0, le=1500)
    fat_goal: Optional[int] = Field(None, ge=0, le=500)
    water_goal_ml: Optional[int] = Field(None, ge=500, le=8000)


class ExerciseIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    muscle_group: Optional[Group] = None
    unit: Optional[Unit] = None
    archived: Optional[bool] = None
    pattern: Optional[Pattern] = None
    instructions: Optional[str] = Field(None, max_length=2000)
    mistakes: Optional[str] = Field(None, max_length=1000)
    equipment: Optional[str] = Field(None, max_length=80)
    video_query: Optional[str] = Field(None, max_length=120)


class SplitIn(BaseModel):
    profile_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=80)


class DayIn(BaseModel):
    split_id: Optional[int] = None
    label: Optional[str] = Field(None, max_length=40)
    weekdays: Optional[List[Annotated[int, Field(ge=0, le=6)]]] = Field(None, max_length=7)
    position: Optional[int] = Field(None, ge=0, le=99)


class SplitExerciseIn(BaseModel):
    rest_seconds: Optional[int] = Field(None, ge=30, le=300)
    split_day_id: Optional[int] = None
    exercise_id: Optional[int] = None
    position: Optional[int] = Field(None, ge=0, le=99)
    target_sets: Optional[int] = Field(None, ge=1, le=20)
    target_reps_min: Optional[int] = Field(None, ge=1, le=100)
    target_reps_max: Optional[int] = Field(None, ge=1, le=100)
    target_weight: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    notes: Optional[str] = Field(None, max_length=200)


class SetIn(BaseModel):
    exercise_id: int
    set_no: Optional[int] = Field(None, ge=1, le=50)
    reps: Optional[int] = Field(None, ge=0, le=1000)
    weight: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)
    rpe: Optional[float] = Field(None, ge=0, le=10, allow_inf_nan=False)
    warmup: Optional[bool] = False


class WorkoutIn(BaseModel):
    profile_id: int
    split_day_id: Optional[int] = None
    title: Optional[str] = Field(None, max_length=80)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)
    client_id: Optional[str] = Field(None, max_length=60)
    sets: List[SetIn] = []


class BodyweightIn(BaseModel):
    profile_id: int
    date: Optional[str] = None
    kg: float = Field(..., gt=0, le=500, allow_inf_nan=False)


# ------------------------------------------------------------- Profile
def _profile_in(data):
    """Listen aus dem Formular in Text fuer die Datenbank."""
    if "train_weekdays" in data:
        wd = data["train_weekdays"]
        if wd is not None and wd and not 2 <= len(set(wd)) <= 6:
            raise HTTPException(400, "Trainingstage: bitte 2 bis 6 Tage wählen.")
        data["train_weekdays"] = db.format_weekdays(wd)
        if wd:
            data["days_per_week"] = len(set(wd))
    if "focus" in data:
        data["focus"] = ",".join(dict.fromkeys(data["focus"] or [])) or None
    return data


@app.get("/api/profiles")
def api_profiles():
    return [db.profile_out(p) for p in db.list_rows("profiles")]


@app.post("/api/profiles")
def api_add_profile(body: ProfileIn):
    data = _data(body, required=("name",))
    data.setdefault("color", "#2c8a55")
    data["created_at"] = db.now()
    with db.connect() as c:
        cur = c.execute("INSERT INTO profiles (name, color, created_at) VALUES (?,?,?)",
                        (data["name"], data["color"], data["created_at"]))
        new_id = cur.lastrowid
    return db.profile_out(db.get_row("profiles", new_id))


@app.get("/api/profiles/{profile_id}")
def api_profile(profile_id: int):
    return db.profile_out(_found(db.get_row("profiles", profile_id)))


@app.patch("/api/profiles/{profile_id}")
def api_update_profile(profile_id: int, body: ProfileIn):
    _found(db.get_row("profiles", profile_id))
    data = _profile_in(_data(body))
    if "experience" in data and data["experience"] != db.get_row("profiles", profile_id).get("experience"):
        data["experience_auto"] = None      # von Hand geaendert: die App stuft nicht mehr selbst um
    return db.profile_out(_found(db.update_row("profiles", profile_id, data)))


@app.get("/api/export/{kind}.csv")
def api_export(kind: Literal[tuple(export.EXPORTS)], profile_id: int = Depends(current_profile)):  # type: ignore[valid-type]
    filename, text = export.to_csv(kind, profile_id)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# -------------------------------------------------------------- Übungen
@app.get("/api/exercises")
def api_exercises():
    return db.list_rows("exercises", order="muscle_group, name")


def _with_kind(data):
    if "pattern" in data:
        data["kind"] = (None if data["pattern"] is None else
                        "compound" if data["pattern"] in db.COMPOUND_PATTERNS else "isolation")
    return data


@app.post("/api/exercises")
def api_add_exercise(body: ExerciseIn):
    data = _with_kind(_data(body, required=("name",)))
    data.setdefault("unit", "kg")
    return db.insert_row("exercises", data)


@app.patch("/api/exercises/{exercise_id}")
def api_update_exercise(exercise_id: int, body: ExerciseIn):
    _found(db.get_row("exercises", exercise_id))
    return _found(db.update_row("exercises", exercise_id, _with_kind(_data(body))))


@app.delete("/api/exercises/{exercise_id}")
def api_delete_exercise(exercise_id: int):
    used = db.one("SELECT COUNT(*) AS n FROM sets WHERE exercise_id=?", (exercise_id,))
    if used and used["n"]:
        # Trainierte Sätze sollen nicht verschwinden – die Übung wird nur ausgeblendet.
        db.update_row("exercises", exercise_id, {"archived": 1})
        return {"ok": True, "archived": True}
    with db.connect() as c:
        c.execute("DELETE FROM split_exercises WHERE exercise_id=?", (exercise_id,))
    db.delete_row("exercises", exercise_id)
    return {"ok": True, "archived": False}


# --------------------------------------------------------------- Splits
@app.get("/api/splits")
def api_splits(profile_id: int = Depends(current_profile)):
    out = db.list_rows_where("splits", "profile_id", profile_id, "id")
    for s in out:
        s["days"] = [db.day_out(d) for d in
                     db.list_rows_where("split_days", "split_id", s["id"], "position, id")]
    return out


@app.get("/api/splits/{split_id}")
def api_split(split_id: int, profile_id: int = Depends(current_profile)):
    _owns_split(split_id, profile_id)
    split = _found(db.split_detail(split_id))
    _with_minutes(split, profile_id)
    return split


def _with_minutes(split, profile_id):
    """Geschaetzte Dauer je Tag aus den Saetzen der aktuellen Blockwoche – die Dauer wird nicht abgefragt,
    sie ergibt sich aus dem Volumen."""
    from .planner import blocks, volume_plan as vp
    block = blocks.for_split(split["id"])
    goal = (block or {}).get("goal") or (db.get_row("profiles", profile_id) or {}).get("goal") or "aufbau"
    for day in split["days"]:
        sets = sum(blocks.sets_for(block, e) if block else e["target_sets"] for e in day["exercises"])
        day["minutes"] = vp.session_minutes(sets, goal) if sets else None


@app.post("/api/splits")
def api_add_split(body: SplitIn, profile_id: int = Depends(current_profile)):
    data = _data(body, required=("name",))
    data["profile_id"] = profile_id
    data["created_at"] = db.now()
    with db.connect() as c:
        cur = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,0,?)",
                        (profile_id, data["name"], data["created_at"]))
        new_id = cur.lastrowid
    if len(db.list_rows_where("splits", "profile_id", profile_id)) == 1:
        db.activate_split(new_id, profile_id)
    return db.split_detail(new_id)


@app.patch("/api/splits/{split_id}")
def api_update_split(split_id: int, body: SplitIn, profile_id: int = Depends(current_profile)):
    _owns_split(split_id, profile_id)
    data = _data(body)
    data.pop("profile_id", None)  # Ein Split wechselt nicht den Besitzer
    return _found(db.update_row("splits", split_id, data))


@app.post("/api/splits/{split_id}/activate")
def api_activate_split(split_id: int, profile_id: int = Depends(current_profile)):
    _owns_split(split_id, profile_id)
    db.activate_split(split_id, profile_id)
    return {"ok": True}


@app.delete("/api/splits/{split_id}")
def api_delete_split(split_id: int, profile_id: int = Depends(current_profile)):
    _owns_split(split_id, profile_id)
    was_active = db.get_row("splits", split_id)["active"]
    db.delete_row("splits", split_id)
    rest = db.list_rows_where("splits", "profile_id", profile_id)
    if was_active and rest:
        db.activate_split(rest[-1]["id"], profile_id)       # der zuletzt angelegte Plan rueckt nach
    return {"ok": True}


# -------------------------------------------------------- Trainingstage
@app.post("/api/split-days")
def api_add_day(body: DayIn, profile_id: int = Depends(current_profile)):
    data = _data(body, required=("split_id", "label"))
    _owns_split(data["split_id"], profile_id)
    if data.get("position") is None:
        siblings = db.list_rows_where("split_days", "split_id", data["split_id"])
        data["position"] = len(siblings)
    if "weekdays" in data:
        data["weekdays"] = db.format_weekdays(data["weekdays"])
    return db.day_out(db.insert_row("split_days", data))


@app.patch("/api/split-days/{day_id}")
def api_update_day(day_id: int, body: DayIn, profile_id: int = Depends(current_profile)):
    _owns_day(day_id, profile_id)
    data = _data(body)
    data.pop("split_id", None)
    if "weekdays" in data:
        data["weekdays"] = db.format_weekdays(data["weekdays"])
    return db.day_out(_found(db.update_row("split_days", day_id, data)))


@app.delete("/api/split-days/{day_id}")
def api_delete_day(day_id: int, profile_id: int = Depends(current_profile)):
    _owns_day(day_id, profile_id)
    db.delete_row("split_days", day_id)
    return {"ok": True}


@app.post("/api/split-exercises")
def api_add_split_exercise(body: SplitExerciseIn, profile_id: int = Depends(current_profile)):
    data = _data(body, required=("split_day_id", "exercise_id"))
    _owns_day(data["split_day_id"], profile_id)
    if data.get("position") is None:
        siblings = db.list_rows_where("split_exercises", "split_day_id", data["split_day_id"])
        data["position"] = len(siblings)
    data.setdefault("target_sets", 3)
    data.setdefault("target_reps_min", 8)
    data.setdefault("target_reps_max", 12)
    _check_range(data)
    data["base_sets"] = data["target_sets"]
    return db.insert_row("split_exercises", data)


@app.patch("/api/split-exercises/{item_id}")
def api_update_split_exercise(item_id: int, body: SplitExerciseIn,
                              profile_id: int = Depends(current_profile)):
    item = _found(db.get_row("split_exercises", item_id))
    _owns_day(item["split_day_id"], profile_id)
    data = _data(body)
    data.pop("split_day_id", None)
    _check_range(data, item)
    if data.get("target_sets"):
        data["base_sets"] = data["target_sets"]     # eine Aenderung im Plan ist die neue Woche 1
    if data.get("exercise_id") and data["exercise_id"] != item["exercise_id"]:
        data["generated"] = None                    # selbst gewaehlt: die App wechselt sie nicht mehr
    return _found(db.update_row("split_exercises", item_id, data))


@app.delete("/api/split-exercises/{item_id}")
def api_delete_split_exercise(item_id: int, profile_id: int = Depends(current_profile)):
    item = _found(db.get_row("split_exercises", item_id))
    _owns_day(item["split_day_id"], profile_id)
    db.delete_row("split_exercises", item_id)
    return {"ok": True}


# --------------------------------------------------------------- Heute
@app.get("/api/today")
def api_today(profile_id: int = Depends(current_profile)):
    return daily_view.build(profile_id)


@app.get("/api/day/{day_id}")
def api_day(day_id: int, profile_id: int = Depends(current_profile)):
    _owns_day(day_id, profile_id)
    split = db.split_detail(db.get_row("split_days", day_id)["split_id"])
    day = next(d for d in split["days"] if d["id"] == day_id)
    checkin = readiness.get(profile_id, stats.today().isoformat())
    return daily_view.adjusted_day(profile_id, day, checkin)


# ------------------------------------------------------------ Einheiten
@app.get("/api/workouts")
def api_workouts(profile_id: int = Depends(current_profile), limit: int = Query(30, ge=1, le=200)):
    return db.list_workouts(profile_id, limit)


@app.get("/api/workouts/{workout_id}")
def api_workout(workout_id: int, profile_id: int = Depends(current_profile)):
    w = _found(db.get_workout(workout_id))
    if w["profile_id"] != profile_id:
        raise HTTPException(403, "Diese Einheit gehört zu einem anderen Profil.")
    return session_feedback.for_workout(profile_id, w)


@app.post("/api/workouts")
def api_add_workout(body: WorkoutIn):
    if not db.exists("profiles", body.profile_id):
        raise HTTPException(400, "Dieses Profil gibt es nicht (mehr).")
    payload = body.model_dump()
    # Eine Einheit kann Stunden im Ausgang liegen. Wurde der Trainingstag inzwischen
    # geloescht, zaehlt sie trotzdem – nur ohne Bezug zum Tag. Ein fremder Tag bleibt verboten.
    if body.split_day_id is not None:
        if db.exists("split_days", body.split_day_id):
            _owns_day(body.split_day_id, body.profile_id)
        else:
            log.info("Trainingstag %s gibt es nicht mehr – Einheit ohne Tag gespeichert",
                     body.split_day_id)
            payload["split_day_id"] = None
    # Dasselbe fuer inzwischen geloeschte Uebungen: deren Saetze fallen weg, der Rest bleibt.
    gone = {s["exercise_id"] for s in payload["sets"] if not db.exists("exercises", s["exercise_id"])}
    if gone:
        log.info("Übungen %s gibt es nicht mehr – ihre Sätze werden nicht gespeichert", sorted(gone))
    # Sätze ohne Wiederholungen sind nicht absolviert – die schickt die Oberfläche gar
    # nicht erst, hier zur Sicherheit noch einmal.
    payload["sets"] = [s for s in payload["sets"] if s.get("reps") and s["exercise_id"] not in gone]
    if gone and not payload["sets"]:
        raise HTTPException(400, "Die Übungen dieser Einheit gibt es nicht mehr.")
    before = activity.reached_keys(body.profile_id)
    workout, created = db.save_workout(payload)
    changes = []
    if created:
        try:
            from .planner import explain
            changes = explain.after_workout(body.profile_id, workout)
        except Exception:   # noqa: BLE001 – die Erklaerung ist Beiwerk, die Einheit ist gespeichert
            log.exception("Plan-Hinweise nach der Einheit fehlgeschlagen")
    return {"workout": workout, "created": created, "plan_changes": changes,
            "milestones": activity.new_milestones(before, body.profile_id) if created else [],
            # Beim zweiten Eintreffen derselben client_id nicht noch einmal urteilen.
            "feedback": session_feedback.evaluate(body.profile_id, workout) if created else None}


class SetPatch(BaseModel):
    id: int
    reps: Optional[int] = Field(None, ge=0, le=1000)
    weight: Optional[float] = Field(None, ge=0, le=1000, allow_inf_nan=False)


class WorkoutPatch(BaseModel):
    notes: Optional[str] = Field(None, max_length=500)
    sets: Optional[List[SetPatch]] = None


@app.patch("/api/workouts/{workout_id}")
def api_patch_workout(workout_id: int, body: WorkoutPatch, profile_id: int = Depends(current_profile)):
    """Eine vergessene oder falsch getippte Einheit korrigieren, statt sie loeschen und
    neu anlegen zu muessen. Aendert nur Werte bestehender Saetze, keine Struktur."""
    w = _found(db.get_row("workouts", workout_id))
    if w["profile_id"] != profile_id:
        raise HTTPException(403, "Diese Einheit gehört zu einem anderen Profil.")
    with db.connect() as c:
        if body.notes is not None:
            c.execute("UPDATE workouts SET notes=? WHERE id=?", (body.notes, workout_id))
        for s in body.sets or []:
            row = c.execute("SELECT workout_id FROM sets WHERE id=?", (s.id,)).fetchone()
            if not row or row[0] != workout_id:
                raise HTTPException(400, "Dieser Satz gehört nicht zu dieser Einheit.")
            c.execute("UPDATE sets SET reps=?, weight=? WHERE id=?", (s.reps, s.weight, s.id))
    return session_feedback.for_workout(profile_id, db.get_workout(workout_id))


@app.delete("/api/workouts/{workout_id}")
def api_delete_workout(workout_id: int, profile_id: int = Depends(current_profile)):
    w = _found(db.get_row("workouts", workout_id))
    if w["profile_id"] != profile_id:
        raise HTTPException(403, "Diese Einheit gehört zu einem anderen Profil.")
    db.delete_row("workouts", workout_id)
    return {"ok": True}


# -------------------------------------------------------- Körpergewicht
@app.get("/api/bodyweight")
def api_bodyweight(profile_id: int = Depends(current_profile)):
    return stats.bodyweight(profile_id)


@app.post("/api/bodyweight")
def api_add_bodyweight(body: BodyweightIn):
    if not db.exists("profiles", body.profile_id):
        raise HTTPException(400, "Dieses Profil gibt es nicht (mehr).")
    # Dieselbe Pruefung wie bei Check-in/Cardio/Mobility (routes/daily.py) – auch hier
    # sollen keine Zukunftsdaten reinrutschen.
    day = daily._day(body.date)
    return db.set_bodyweight(body.profile_id, day, body.kg)


# ---------------------------------------------------------- Fortschritt
@app.get("/api/progress")
def api_progress(profile_id: int = Depends(current_profile)):
    return stats.progress(profile_id)


@app.get("/api/history/{exercise_id}")
def api_history(exercise_id: int, profile_id: int = Depends(current_profile)):
    _found(db.get_row("exercises", exercise_id))
    return stats.history(profile_id, exercise_id)


class ApplyIn(BaseModel):
    action: Dict[str, object]


@app.get("/api/plan/analyze")
def api_plan_analyze(profile_id: int = Depends(current_profile),
                     split: Optional[int] = Query(None)):
    if split is not None:
        _owns_split(split, profile_id)
    return {**analysis.analyze(profile_id, split), "ai_available": ai.available()}


@app.post("/api/plan/apply")
def api_plan_apply(body: ApplyIn, profile_id: int = Depends(current_profile)):
    try:
        analysis.apply(profile_id, body.action)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e) or "Ungültige Eingabe.") from e
    return {"ok": True, **analysis.analyze(profile_id)}


@app.post("/api/check/ai")
def api_check_ai(profile_id: int = Depends(current_profile)):
    return checker.ai_review(profile_id)


# -------------------------------------------------------- Plan-Assistent
class WizardIn(BaseModel):
    option: str = Field(..., max_length=20)
    goal: Optional[Goal] = None
    experience: Optional[Experience] = None
    days: Optional[int] = Field(None, ge=2, le=6)
    minutes: Optional[int] = Field(None, ge=0, le=120)   # 0 = ohne Zeitgrenze
    weekdays: Optional[List[Annotated[int, Field(ge=0, le=6)]]] = Field(None, max_length=7)
    focus: Optional[List[Focus]] = Field(None, max_length=3)


class PlanExerciseIn(BaseModel):
    exercise_id: int
    target_sets: int = Field(..., ge=1, le=6)
    target_reps_min: int = Field(..., ge=1, le=30)
    target_reps_max: int = Field(..., ge=1, le=30)
    rest_seconds: Optional[int] = Field(None, ge=30, le=300)


class PlanDayIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=40)
    weekdays: List[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=list, max_length=7)
    exercises: List[PlanExerciseIn] = Field(..., min_length=1, max_length=12)


class PlanIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    focus: List[str] = Field(default_factory=list)
    option: Optional[str] = None
    goal: Optional[str] = None
    experience: Optional[str] = None
    minutes: Optional[int] = None
    reason: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    basis: List[str] = Field(default_factory=list, max_length=20)
    reasons: List[str] = Field(default_factory=list, max_length=10)
    frequency: Dict[str, float] = Field(default_factory=dict)
    rotate: bool = False
    weekdays_chosen: List[int] = Field(default_factory=list, max_length=7)
    days: List[PlanDayIn] = Field(..., min_length=1, max_length=7)


class SaveIn(BaseModel):
    plan: PlanIn
    activate: bool = True


def _profile(profile_id):
    return db.profile_out(_found(db.get_row("profiles", profile_id)))


def _check(plan, profile_id=None):
    from .planner import validate
    profile = db.get_row("profiles", profile_id) if profile_id else None
    injuries = injuries_from((profile or {}).get("limitations"))
    profile = profile or {}
    return validate.analyze(plan, injuries, None,
                            plan.get("goal") or profile.get("goal") or "aufbau", bool(plan.get("rotate")),
                            plan.get("experience") or profile.get("experience") or "fortgeschritten",
                            plan.get("focus") or db.parse_focus(profile.get("focus")))


def _with_check(preview, profile_id=None):
    return {"preview": preview, "check": _check(preview, profile_id)}


@app.get("/api/wizard/options")
def api_wizard_options(profile_id: int = Depends(current_profile),
                       days: Optional[int] = Query(None, ge=2, le=6),
                       experience: Optional[Experience] = None,
                       goal: Optional[Goal] = None,
                       minutes: Optional[int] = Query(None, ge=0, le=120),
                       weekdays: str = Query("", max_length=20),
                       focus: str = Query("", max_length=80)):
    p = _profile(profile_id)
    wd = db.parse_weekdays(weekdays) or p.get("train_weekdays") or []
    if wd and not 2 <= len(wd) <= 6:
        raise HTTPException(400, "Trainingstage: bitte 2 bis 6 Tage wählen.")
    overrides = {"experience": experience, "goal": goal, "minutes": minutes,
                 "weekdays": wd or None, "days": days,
                 "focus": db.parse_focus(focus) if focus else None}
    return {"options": generate.split_options(p, overrides), "ai_available": ai.available()}


@app.post("/api/wizard/preview")
def api_wizard_preview(body: WizardIn, profile_id: int = Depends(current_profile)):
    try:
        preview = generate.generate(_profile(profile_id), body.option,
                                    body.model_dump(exclude={"option"}))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _with_check(preview, profile_id)


@app.get("/api/wizard/alternatives")
def api_wizard_alternatives(pattern: Pattern, exclude: str = Query("", max_length=200),
                            profile_id: int = Depends(current_profile)):
    ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    injuries = injuries_from((db.get_row("profiles", profile_id) or {}).get("limitations"))
    return generate.alternatives(pattern, ids, injuries)


def _plan_dict(plan: PlanIn):
    """Eingehenden Plan gegen den Katalog pruefen und mit Namen/Muskelgruppen anreichern."""
    data = plan.model_dump()
    catalog = db.rows("SELECT id, name, muscle_group, unit, pattern FROM exercises")
    try:
        return ai.validate_plan(data, data, catalog)
    except ValueError as e:
        raise HTTPException(400, f"Plan ungültig: {e}.") from e


@app.post("/api/wizard/check")
def api_wizard_check(plan: PlanIn, profile_id: int = Depends(current_profile)):
    return _with_check(_plan_dict(plan), profile_id)


def _finding_keys(check):
    # Vergleich ueber Muskelgruppe/Art, nicht ueber den genauen Text (Satzzahlen aendern sich)
    return {(f["level"], f.get("group"), f["text"].split(" ")[0]) for f in check["findings"]}


@app.post("/api/wizard/refine")
def api_wizard_refine(plan: PlanIn, profile_id: int = Depends(current_profile)):
    """KI-Verfeinerung mit Kontrolle: bringt der Vorschlag neue Luecken, gibt es einmal
    Rueckmeldung und einen zweiten Versuch; wird es nicht besser, bleibt der Ausgangsplan."""
    preview = _plan_dict(plan)
    profile, catalog = _profile(profile_id), generate.catalog()
    base_keys = _finding_keys(_check(preview, profile_id))
    feedback = None
    for _ in range(2):
        res = ai.refine_plan(profile, preview, catalog, feedback)
        if res.get("error"):
            return {**_with_check(preview, profile_id), "error": res["error"]}
        check = _check(res["preview"], profile_id)
        new = [f["text"] for f in check["findings"]
               if (f["level"], f.get("group"), f["text"].split(" ")[0]) not in base_keys]
        # (gleiche Schluesselbildung wie _finding_keys)
        if not new:
            return {"preview": res["preview"], "check": check, "error": None}
        feedback = new
    return {**_with_check(preview, profile_id),
            "error": "Die KI-Vorschläge hätten neue Lücken gebracht ("
                     + "; ".join(feedback) + ") – der bisherige Plan bleibt."}


def _start_block(profile_id, split_id, plan):
    from .planner import blocks
    profile = _profile(profile_id)
    age = (date.today().year - int(profile["birth_year"])) if profile.get("birth_year") else None
    blocks.create(profile_id, split_id, plan.get("goal") or profile.get("goal"),
                  plan.get("experience") or profile.get("experience"),
                  plan.get("focus") or db.parse_focus(profile.get("focus")), age)


@app.post("/api/wizard/save")
def api_wizard_save(body: SaveIn, profile_id: int = Depends(current_profile)):
    plan = _plan_dict(body.plan)
    with db.connect() as c:
        split_id = c.execute(
            "INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,0,?)",
            (profile_id, plan["name"].strip(), db.now())).lastrowid
        for pos, day in enumerate(plan["days"]):
            day_id = c.execute(
                "INSERT INTO split_days (split_id, position, label, weekdays) VALUES (?,?,?,?)",
                (split_id, pos, day["label"], db.format_weekdays(day["weekdays"]))).lastrowid
            for ex_pos, ex in enumerate(day["exercises"]):
                c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position,"
                          " target_sets, target_reps_min, target_reps_max, rest_seconds)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (day_id, ex["exercise_id"], ex_pos, ex["target_sets"],
                           ex["target_reps_min"], ex["target_reps_max"], ex.get("rest_seconds")))
    if body.activate:
        db.activate_split(split_id, profile_id)
        _start_block(profile_id, split_id, plan)
    return db.split_detail(split_id)


@app.get("/api/status")
def api_status():
    return {"ok": True, "profiles": len(db.list_rows("profiles")),
            "workouts": len(db.rows("SELECT id FROM workouts"))}


# ------------------------------------------------------- Service Worker
OFFLINE_ASSETS = (".html", ".css", ".js", ".png", ".svg", ".webmanifest")


@app.get("/sw.js")
def service_worker():
    """Der Worker bekommt eine Version aus Größe und Zeitstempel aller Dateien – nach einem
    Deploy ist der alte Zwischenspeicher damit automatisch ungültig."""
    files = sorted(p for p in STATIC.rglob("*")
                   if p.is_file() and p.suffix in OFFLINE_ASSETS and p.name != "sw.js")
    digest = hashlib.sha1()
    for p in files:
        stat = p.stat()
        digest.update(f"{p.relative_to(STATIC)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    # vendor/ (ZXing, ~340 KB) nicht vorladen: scanner.js holt ihn bei Bedarf, und ohne
    # Netz nuetzt der Scanner nichts – die Barcode-Abfrage braucht den Pi.
    assets = ["/"] + ["/" + str(p.relative_to(STATIC)) for p in files
                      if p.name != "index.html" and p.relative_to(STATIC).parts[0] != "vendor"]
    body = ((STATIC / "sw.js").read_text()
            .replace('"__VERSION__"', json.dumps(digest.hexdigest()[:12]))
            .replace("__ASSETS__", json.dumps(assets)))
    return Response(body, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
