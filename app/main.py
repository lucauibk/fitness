"""FastAPI-App der Fitness-Anwendung: liefert die Oberfläche und die JSON-Daten."""
import hashlib
import ipaddress
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from . import db, stats  # noqa: E402

STATIC = Path(__file__).parent.parent / "static"
log = logging.getLogger("fitness")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    log.info("Fitness-App bereit (%s)", db.DB_PATH)
    yield


app = FastAPI(title="Fitness", lifespan=lifespan)

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


# ------------------------------------------------------ Fehlermeldungen
FIELD_NAMES = {"name": "Name", "label": "Bezeichnung", "reps": "Wiederholungen",
               "weight": "Gewicht", "kg": "Gewicht", "date": "Datum", "rpe": "Anstrengung",
               "target_sets": "Sätze", "target_reps_min": "Wiederholungen (von)",
               "target_reps_max": "Wiederholungen (bis)",
               "target_weight": "Gewicht", "weekday": "Wochentag", "muscle_group": "Muskelgruppe",
               "exercise_id": "Übung", "split_day_id": "Trainingstag", "split_id": "Split",
               "profile_id": "Profil", "unit": "Einheit", "position": "Reihenfolge"}
# Felder, die per PATCH vorhanden sein duerfen, aber nie null werden
NOT_NULL = ("name", "label", "kg", "date", "exercise_id", "split_id", "split_day_id",
            "profile_id")


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


# ---------------------------------------------------------------- Hilfen
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
    for key in ("archived", "active"):
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


class ProfileIn(BaseModel):
    name: Optional[str] = Field(None, max_length=40)
    color: Optional[str] = Field(None, max_length=20)


class ExerciseIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    muscle_group: Optional[Group] = None
    unit: Optional[Unit] = None
    archived: Optional[bool] = None


class SplitIn(BaseModel):
    profile_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=80)


class DayIn(BaseModel):
    split_id: Optional[int] = None
    label: Optional[str] = Field(None, max_length=40)
    weekday: Optional[int] = Field(None, ge=0, le=6)
    position: Optional[int] = Field(None, ge=0, le=99)


class SplitExerciseIn(BaseModel):
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
@app.get("/api/profiles")
def api_profiles():
    return db.list_rows("profiles")


@app.post("/api/profiles")
def api_add_profile(body: ProfileIn):
    data = _data(body, required=("name",))
    data.setdefault("color", "#2c8a55")
    data["created_at"] = db.now()
    with db.connect() as c:
        cur = c.execute("INSERT INTO profiles (name, color, created_at) VALUES (?,?,?)",
                        (data["name"], data["color"], data["created_at"]))
        new_id = cur.lastrowid
    return db.get_row("profiles", new_id)


@app.patch("/api/profiles/{profile_id}")
def api_update_profile(profile_id: int, body: ProfileIn):
    _found(db.get_row("profiles", profile_id))
    return _found(db.update_row("profiles", profile_id, _data(body)))


# -------------------------------------------------------------- Übungen
@app.get("/api/exercises")
def api_exercises():
    return db.list_rows("exercises", order="muscle_group, name")


@app.post("/api/exercises")
def api_add_exercise(body: ExerciseIn):
    data = _data(body, required=("name",))
    data.setdefault("unit", "kg")
    return db.insert_row("exercises", data)


@app.patch("/api/exercises/{exercise_id}")
def api_update_exercise(exercise_id: int, body: ExerciseIn):
    _found(db.get_row("exercises", exercise_id))
    return _found(db.update_row("exercises", exercise_id, _data(body)))


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
        s["days"] = db.list_rows_where("split_days", "split_id", s["id"], "position, id")
    return out


@app.get("/api/splits/{split_id}")
def api_split(split_id: int, profile_id: int = Depends(current_profile)):
    _owns_split(split_id, profile_id)
    return _found(db.split_detail(split_id))


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
        db.activate_split(rest[0]["id"], profile_id)
    return {"ok": True}


# -------------------------------------------------------- Trainingstage
@app.post("/api/split-days")
def api_add_day(body: DayIn, profile_id: int = Depends(current_profile)):
    data = _data(body, required=("split_id", "label"))
    _owns_split(data["split_id"], profile_id)
    if data.get("position") is None:
        siblings = db.list_rows_where("split_days", "split_id", data["split_id"])
        data["position"] = len(siblings)
    return db.insert_row("split_days", data)


@app.patch("/api/split-days/{day_id}")
def api_update_day(day_id: int, body: DayIn, profile_id: int = Depends(current_profile)):
    _owns_day(day_id, profile_id)
    data = _data(body)
    data.pop("split_id", None)
    return _found(db.update_row("split_days", day_id, data))


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
    return db.insert_row("split_exercises", data)


@app.patch("/api/split-exercises/{item_id}")
def api_update_split_exercise(item_id: int, body: SplitExerciseIn,
                              profile_id: int = Depends(current_profile)):
    item = _found(db.get_row("split_exercises", item_id))
    _owns_day(item["split_day_id"], profile_id)
    data = _data(body)
    data.pop("split_day_id", None)
    _check_range(data, item)
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
    return stats.today_view(profile_id)


@app.get("/api/day/{day_id}")
def api_day(day_id: int, profile_id: int = Depends(current_profile)):
    _owns_day(day_id, profile_id)
    split = db.split_detail(db.get_row("split_days", day_id)["split_id"])
    day = next(d for d in split["days"] if d["id"] == day_id)
    return stats.day_plan(profile_id, day)


# ------------------------------------------------------------ Einheiten
@app.get("/api/workouts")
def api_workouts(profile_id: int = Depends(current_profile), limit: int = Query(30, ge=1, le=200)):
    return db.list_workouts(profile_id, limit)


@app.get("/api/workouts/{workout_id}")
def api_workout(workout_id: int, profile_id: int = Depends(current_profile)):
    w = _found(db.get_workout(workout_id))
    if w["profile_id"] != profile_id:
        raise HTTPException(403, "Diese Einheit gehört zu einem anderen Profil.")
    return w


@app.post("/api/workouts")
def api_add_workout(body: WorkoutIn):
    if not db.exists("profiles", body.profile_id):
        raise HTTPException(400, "Dieses Profil gibt es nicht (mehr).")
    if body.split_day_id is not None:
        _owns_day(body.split_day_id, body.profile_id)
    payload = body.model_dump()
    for s in payload["sets"]:
        if not db.exists("exercises", s["exercise_id"]):
            raise HTTPException(400, "Diese Übung gibt es nicht (mehr).")
    # Sätze ohne Wiederholungen sind nicht absolviert – die schickt die Oberfläche gar
    # nicht erst, hier zur Sicherheit noch einmal.
    payload["sets"] = [s for s in payload["sets"] if s.get("reps")]
    workout, created = db.save_workout(payload)
    return {"workout": workout, "created": created}


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
    day = body.date or stats.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as e:
        raise HTTPException(400, "Datum: ungültige Eingabe.") from e
    return db.set_bodyweight(body.profile_id, day, body.kg)


# ---------------------------------------------------------- Fortschritt
@app.get("/api/progress")
def api_progress(profile_id: int = Depends(current_profile)):
    return stats.progress(profile_id)


@app.get("/api/history/{exercise_id}")
def api_history(exercise_id: int, profile_id: int = Depends(current_profile)):
    _found(db.get_row("exercises", exercise_id))
    return stats.history(profile_id, exercise_id)


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
    assets = ["/"] + ["/" + str(p.relative_to(STATIC)) for p in files if p.name != "index.html"]
    body = ((STATIC / "sw.js").read_text()
            .replace('"__VERSION__"', json.dumps(digest.hexdigest()[:12]))
            .replace("__ASSETS__", json.dumps(assets)))
    return Response(body, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
