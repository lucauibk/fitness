"""Ernaehrung: Tagesbilanz, Ziele, Lebensmittel-Suche, Eintraege."""
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, nutrition
from ..common import FIELD_NAMES, _found, current_profile, insert_once, owned
from .daily import _day

router = APIRouter(prefix="/api")
FIELD_NAMES.update({"grams": "Menge", "meal": "Mahlzeit", "food_id": "Lebensmittel",
                    "protein": "Eiweiß", "carbs": "Kohlenhydrate", "fat": "Fett",
                    "barcode": "Barcode", "brand": "Marke", "serving_g": "Portion",
                    "activity_level": "Aktivität", "kcal_goal": "Kalorienziel", "ml": "Menge",
                    "water_goal_ml": "Trinkziel"})
Meal = Literal["fruehstueck", "mittag", "abend", "snack"]
Nutrient = Field(None, ge=0, le=100, allow_inf_nan=False)


def _profile(profile_id):
    return db.get_row("profiles", profile_id)


@router.get("/nutrition/goals")
def get_goals(profile_id: int = Depends(current_profile)):
    profile = _profile(profile_id)
    out = nutrition.goals(profile)
    out["water"] = nutrition.water_goals(profile)
    return out


@router.get("/nutrition/day")
def get_day(profile_id: int = Depends(current_profile), date: Optional[str] = None):
    profile = _profile(profile_id)
    out = nutrition.day(profile_id, _day(date))
    out["goals"] = nutrition.goals(profile)
    out["water_goal"] = nutrition.water_goals(profile)["goal"]
    return out


# ---------------------------------------------------------------- Wasser
class WaterIn(BaseModel):
    profile_id: int
    ml: int = Field(..., ge=-2000, le=2000)
    date: Optional[str] = None
    client_id: Optional[str] = Field(None, max_length=60)


@router.post("/water-log")
def add_water(body: WaterIn):
    profile_id = current_profile(body.profile_id)
    row, created = insert_once("water_log", {
        "profile_id": profile_id, "date": _day(body.date), "ml": body.ml,
        "client_id": body.client_id})
    return {"entry": row, "created": created}


# ---------------------------------------------------------- Lebensmittel
def _local(q, profile_id):
    """Eigene und schon verwendete Lebensmittel, sortiert nach Relevanz und Haeufigkeit.
    Gefiltert wird in Python statt per LIKE, weil SQLite Umlaute und Gross/klein nur fuer
    ASCII vergleicht – die Tabelle bleibt auf dem Pi klein genug dafuer."""
    found = [f for f in db.rows("SELECT * FROM foods") if nutrition.matches(f, q)]
    uses = {r["food_id"]: r["n"] for r in db.rows(
        "SELECT food_id, COUNT(*) AS n FROM food_log WHERE profile_id=? GROUP BY food_id",
        (profile_id,))}
    last = {r["food_id"]: r["grams"] for r in db.rows(
        "SELECT food_id, grams FROM food_log WHERE id IN"
        " (SELECT MAX(id) FROM food_log WHERE profile_id=? GROUP BY food_id)", (profile_id,))}
    for f in found:
        f["uses"] = uses.get(f["id"], 0)
        f["last_grams"] = last.get(f["id"])
    return nutrition.rank(found, q, uses)[:15]


@router.get("/foods/search/local")
def search_local(q: str = Query(..., min_length=2, max_length=80),
                 profile_id: int = Depends(current_profile)):
    return {"local": _local(q.strip(), profile_id)}


@router.get("/foods/search")
def search(q: str = Query(..., min_length=2, max_length=80)):
    """Nur Open Food Facts – die gespeicherten Lebensmittel liefert /foods/search/local."""
    q = q.strip()
    online, error = [], None
    try:
        known = {r["barcode"] for r in db.rows("SELECT barcode FROM foods WHERE barcode IS NOT NULL")}
        online = [f for f in nutrition.search_off(q) if f.get("barcode") not in known]
    except nutrition.FoodError as e:
        error = str(e)
    return {"online": online, "error": error}


@router.get("/foods/barcode/{code}")
def barcode(code: str):
    if not re.fullmatch(r"\d{6,14}", code):
        raise HTTPException(400, "Barcode: ungültige Eingabe.")
    have = db.one("SELECT * FROM foods WHERE barcode=?", (code,))
    if have:
        return {"food": have}
    try:
        food = nutrition.product_off(code)
    except nutrition.FoodError as e:
        raise HTTPException(503, str(e)) from e
    if not food:
        raise HTTPException(404, "Produkt nicht gefunden – du kannst es als eigenes Lebensmittel anlegen.")
    return {"food": nutrition.remember(food)}


@router.get("/foods/portion/{code}")
def portion(code: str):
    """Portion eines Suchtreffers nachladen, wenn er angetippt wird."""
    if not re.fullmatch(r"\d{6,14}", code):
        raise HTTPException(400, "Barcode: ungültige Eingabe.")
    try:
        found = nutrition.portion_off(code)
    except nutrition.FoodError as e:
        raise HTTPException(503, str(e)) from e
    # Frueher uebernommene Produkte hatten nie eine Portion – bei der Gelegenheit ergaenzen.
    # Selbst angelegte Lebensmittel bleiben, wie sie eingetragen wurden.
    with db.connect() as c:
        c.execute("UPDATE foods SET serving_g=COALESCE(serving_g, ?),"
                  " serving_label=COALESCE(serving_label, ?), pack_g=COALESCE(pack_g, ?)"
                  " WHERE barcode=? AND source='off'",
                  (found["serving_g"], found["serving_label"], found["pack_g"], code))
    return found


@router.get("/foods/recent")
def recent(profile_id: int = Depends(current_profile)):
    """Zuletzt und oft gegessene Lebensmittel – auch offline aus dem Vorrat nutzbar."""
    return db.rows(
        "SELECT f.*, COUNT(l.id) AS uses, MAX(l.date) AS last_date,"
        " (SELECT grams FROM food_log x WHERE x.food_id=f.id AND x.profile_id=? ORDER BY x.id DESC LIMIT 1) AS last_grams"
        " FROM food_log l JOIN foods f ON f.id = l.food_id"
        " WHERE l.profile_id=? GROUP BY f.id ORDER BY MAX(l.id) DESC LIMIT 30",
        (profile_id, profile_id))


class FoodIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    brand: Optional[str] = Field(None, max_length=60)
    barcode: Optional[str] = Field(None, pattern=r"^\d{6,14}$")
    kcal: float = Field(..., ge=0, le=950, allow_inf_nan=False)
    protein: Optional[float] = Nutrient
    carbs: Optional[float] = Nutrient
    fat: Optional[float] = Nutrient
    serving_g: Optional[float] = Field(None, gt=0, le=2000, allow_inf_nan=False)
    serving_label: Optional[str] = Field(None, max_length=60)
    pack_g: Optional[float] = Field(None, gt=0, le=5000, allow_inf_nan=False)
    # Online gefundenes Produkt uebernehmen statt eigenes anlegen
    source: Literal["eigen", "off"] = "eigen"


@router.post("/foods")
def add_food(body: FoodIn):
    data = body.model_dump()
    data["name"] = data["name"].strip()
    data["serving_label"] = (data["serving_label"] or "").strip() or None
    return nutrition.remember(data)


def _own_food(food_id):
    """Nur selbst angelegte Lebensmittel sind aenderbar – Open-Food-Facts-Produkte gehoeren
    der Datenbank dort, und beide Profile teilen sich den Vorrat."""
    food = _found(db.get_row("foods", food_id))
    if food.get("source") != "eigen":
        raise HTTPException(403, "Nur selbst angelegte Lebensmittel lassen sich ändern.")
    return food


@router.patch("/foods/{food_id}")
def patch_food(food_id: int, body: FoodIn):
    _own_food(food_id)
    data = body.model_dump(exclude={"source", "barcode"})
    data["name"] = data["name"].strip()
    data["serving_label"] = (data["serving_label"] or "").strip() or None
    # Eingetragene Mahlzeiten behalten ihre Werte (food_log friert sie ein).
    return db.update_row("foods", food_id, data)


@router.delete("/foods/{food_id}")
def delete_food(food_id: int):
    _own_food(food_id)
    with db.connect() as c:
        # Alte Eintraege bleiben mit Name und Werten erhalten, nur der Verweis faellt weg.
        c.execute("UPDATE food_log SET food_id=NULL WHERE food_id=?", (food_id,))
    db.delete_row("foods", food_id)
    return {"deleted": food_id}


# ------------------------------------------------------------ Eintraege
class LogIn(BaseModel):
    profile_id: int
    food_id: int
    grams: float = Field(..., gt=0, le=5000, allow_inf_nan=False)
    meal: Meal
    date: Optional[str] = None
    client_id: Optional[str] = Field(None, max_length=60)


class LogPatch(BaseModel):
    grams: Optional[float] = Field(None, gt=0, le=5000, allow_inf_nan=False)
    meal: Optional[Meal] = None


@router.post("/food-log")
def add_log(body: LogIn):
    profile_id = current_profile(body.profile_id)
    food = db.get_row("foods", body.food_id)
    if not food:
        raise HTTPException(400, "Dieses Lebensmittel gibt es nicht (mehr).")
    row, created = insert_once("food_log", {
        "profile_id": profile_id, "date": _day(body.date), "meal": body.meal,
        "food_id": food["id"], "name": food["name"], "grams": body.grams,
        "client_id": body.client_id, **nutrition.portion(food, body.grams)})
    return {"entry": row, "created": created}


@router.patch("/food-log/{entry_id}")
def patch_log(entry_id: int, body: LogPatch, profile_id: int = Depends(current_profile)):
    entry = owned("food_log", entry_id, profile_id, "Dieser Eintrag")
    data = body.model_dump(exclude_unset=True)
    if data.get("grams"):
        food = db.get_row("foods", entry["food_id"]) if entry["food_id"] else None
        if food:
            data.update(nutrition.portion(food, data["grams"]))
        else:   # Lebensmittel geloescht: anteilig aus dem alten Eintrag rechnen
            f = data["grams"] / entry["grams"]
            data.update({k: round((entry[k] or 0) * f, 1) for k in ("kcal", "protein", "carbs", "fat")})
    data = {k: v for k, v in data.items() if v is not None}
    with db.connect() as c:
        if data:
            c.execute(f"UPDATE food_log SET {', '.join(f'{k}=?' for k in data)} WHERE id=?",
                      [*data.values(), entry_id])
    return _found(db.get_row("food_log", entry_id))


@router.delete("/food-log/{entry_id}")
def delete_log(entry_id: int, profile_id: int = Depends(current_profile)):
    owned("food_log", entry_id, profile_id, "Dieser Eintrag")
    db.delete_row("food_log", entry_id)
    return {"ok": True}
