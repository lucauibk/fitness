"""Ernaehrung: Tagesziele aus Profil und Gewicht, Lebensmittel aus Open Food Facts.

Open Food Facts ist eine freie Datenbank (openfoodfacts.org). Es werden nur der
Suchbegriff bzw. der Barcode uebertragen. Ein Produkt landet erst in der lokalen
Tabelle `foods`, wenn es ausgewaehlt wird – danach ist es auch offline verfuegbar."""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from . import db
from .activity import latest_weight

log = logging.getLogger(__name__)
OFF = "https://world.openfoodfacts.org"
UA = "FitnessPi/1.0 (private Homeserver-App)"
TIMEOUT = 12   # Open Food Facts ist bei Einzelprodukten manchmal langsam

MEALS = {"fruehstueck": "Frühstück", "mittag": "Mittagessen", "abend": "Abendessen",
         "snack": "Snacks"}
ACTIVITY = {"niedrig": 1.3, "mittel": 1.45, "hoch": 1.6}
GOAL_DELTA = {"abnehmen": -400, "aufbau": 250, "kraft": 150, "fitness": 0}


# ------------------------------------------------------------- Ziele
def bmr(weight, height, age, sex):
    """Mifflin-St Jeor. Ohne Geschlecht der Mittelwert beider Formeln."""
    base = 10 * weight + 6.25 * height - 5 * age
    if sex == "männlich":
        return base + 5
    if sex == "weiblich":
        return base - 161
    return base - 78


def activity_factor(level, training_days):
    f = ACTIVITY.get(level or "", None)
    if f is None:
        days = training_days or 3
        f = 1.35 if days <= 2 else 1.5 if days <= 4 else 1.65
    return f


def computed_goals(profile):
    """Berechnete Ziele oder None, wenn Angaben fehlen (was genau, steht in missing)."""
    weight = latest_weight(profile["id"])
    missing = []
    if not weight:
        missing.append("Körpergewicht")
    if not profile.get("height_cm"):
        missing.append("Größe")
    if not profile.get("birth_year"):
        missing.append("Geburtsjahr")
    if missing:
        return None, missing
    age = date.today().year - int(profile["birth_year"])
    days = len(db.parse_weekdays(profile.get("train_weekdays"))) or profile.get("days_per_week")
    tdee = bmr(weight, profile["height_cm"], age, profile.get("sex")) \
        * activity_factor(profile.get("activity_level"), days)
    goal = profile.get("goal") or "fitness"
    kcal = round((tdee + GOAL_DELTA.get(goal, 0)) / 10) * 10
    protein = round(weight * (2.2 if goal == "abnehmen" else 2.0))
    fat = round(kcal * 0.25 / 9)
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat,
            "tdee": round(tdee), "weight": weight}, []


def goals(profile):
    calc, missing = computed_goals(profile)
    auto = profile.get("nutrition_auto") in (None, 1)
    if auto and calc:
        active = {k: calc[k] for k in ("kcal", "protein", "carbs", "fat")}
    else:
        active = {"kcal": profile.get("kcal_goal"), "protein": profile.get("protein_goal"),
                  "carbs": profile.get("carbs_goal"), "fat": profile.get("fat_goal")}
        if not active["kcal"] and calc:
            active = {k: calc[k] for k in ("kcal", "protein", "carbs", "fat")}
    return {"auto": auto, "goals": active, "computed": calc, "missing": missing,
            "activity_level": profile.get("activity_level")}


def day(profile_id, day_iso):
    entries = db.rows("SELECT * FROM food_log WHERE profile_id=? AND date=? ORDER BY id",
                      (profile_id, day_iso))
    totals = {k: round(sum(e[k] or 0 for e in entries), 1) for k in ("kcal", "protein", "carbs", "fat")}
    meals = {k: {"label": v, "entries": [e for e in entries if e["meal"] == k],
                 "kcal": round(sum(e["kcal"] or 0 for e in entries if e["meal"] == k))}
             for k, v in MEALS.items()}
    return {"date": day_iso, "meals": meals, "totals": totals}


def portion(food, grams):
    f = grams / 100
    return {k: round((food.get(k) or 0) * f, 1) for k in ("kcal", "protein", "carbs", "fat")}


# ------------------------------------------------------ Open Food Facts
class FoodError(Exception):
    pass


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise FoodError(f"Lebensmittel-Datenbank antwortet nicht (HTTP {e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log.warning("Open Food Facts nicht erreichbar: %s", e)
        raise FoodError("Lebensmittel-Datenbank nicht erreichbar – der Pi hat vermutlich kein Internet.") from e


def _num(v):
    try:
        v = float(v)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _brand(value, name=""):
    """Marke aus Open Food Facts. Die Liste enthaelt oft auch Produktnamen – der erste
    Eintrag, der nicht den Produktnamen enthaelt, ist meist die eigentliche Marke."""
    items = value if isinstance(value, list) else (value or "").split(",")
    items = [i.strip() for i in items if i and i.strip()]
    low = (name or "").lower()
    pick = next((i for i in items if not low or low not in i.lower()), items[0] if items else "")
    return pick[:60] or None


def _from_off(p):
    n = p.get("nutriments") or {}
    kcal = _num(n.get("energy-kcal_100g"))
    kj = _num(n.get("energy-kj_100g"))
    if kj is None:
        kj = _num(n.get("energy_100g"))   # "energy" ist in Open Food Facts immer kJ
    if kcal is None and kj is not None:
        kcal = round(kj / 4.184, 1)
    name = (p.get("product_name_de") or p.get("product_name") or "").strip()
    if kcal is None or not name or kcal > 950:
        return None
    return {
        "barcode": str(p.get("code") or "").strip() or None,
        "name": name[:120],
        "brand": _brand(p.get("brands"), name),
        "kcal": kcal,
        "protein": _num(n.get("proteins_100g")),
        "carbs": _num(n.get("carbohydrates_100g")),
        "fat": _num(n.get("fat_100g")),
        "serving_g": _num(p.get("serving_quantity")),
        "source": "off",
    }


FIELDS = "code,product_name,product_name_de,brands,nutriments,serving_quantity"


SEARCH = "https://search.openfoodfacts.org/search"


def search_off(query, limit=20):
    """Volltextsuche. Zuerst der neue Such-Dienst (schnell, kaum Limits), bei Problemen
    die klassische Suche als Rueckfall."""
    try:
        data = _get(f"{SEARCH}?" + urllib.parse.urlencode({
            "q": query, "page_size": limit, "langs": "de", "fields": FIELDS})) or {}
        products = data.get("hits", [])
    except FoodError:
        data = _get(f"{OFF}/cgi/search.pl?" + urllib.parse.urlencode({
            "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
            "page_size": limit, "fields": FIELDS, "lc": "de", "cc": "de"})) or {}
        products = data.get("products", [])
    out, seen = [], set()
    for p in products:
        food = _from_off(p)
        if food and food["barcode"] not in seen:
            seen.add(food["barcode"])
            out.append(food)
    return out


def product_off(barcode):
    data = _get(f"{OFF}/api/v2/product/{urllib.parse.quote(barcode)}.json?fields={FIELDS}")
    if not data or data.get("status") != 1:
        return None
    return _from_off(data.get("product") or {})


def remember(food):
    """Produkt lokal ablegen (einmal pro Barcode) und die gespeicherte Zeile liefern."""
    if food.get("barcode"):
        have = db.one("SELECT * FROM foods WHERE barcode=?", (food["barcode"],))
        if have:
            return have
    return db.insert_row("foods", {**food, "created_at": db.now()})
