"""Ernaehrung: Tagesziele aus Profil und Gewicht, Lebensmittel aus Open Food Facts.

Open Food Facts ist eine freie Datenbank (openfoodfacts.org). Es werden nur der
Suchbegriff bzw. der Barcode uebertragen. Ein Produkt landet erst in der lokalen
Tabelle `foods`, wenn es ausgewaehlt wird – danach ist es auch offline verfuegbar."""
import json
import logging
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from difflib import SequenceMatcher

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
    # Portion des Lebensmittels mitliefern, damit ein Eintrag in Portionen bearbeitet werden kann
    entries = db.rows("SELECT l.*, f.serving_g, f.serving_label, f.pack_g FROM food_log l"
                      " LEFT JOIN foods f ON f.id = l.food_id"
                      " WHERE l.profile_id=? AND l.date=? ORDER BY l.id", (profile_id, day_iso))
    totals = {k: round(sum(e[k] or 0 for e in entries), 1) for k in ("kcal", "protein", "carbs", "fat")}
    meals = {k: {"label": v, "entries": [e for e in entries if e["meal"] == k],
                 "kcal": round(sum(e["kcal"] or 0 for e in entries if e["meal"] == k))}
             for k, v in MEALS.items()}
    water = db.one("SELECT COALESCE(SUM(ml), 0) AS ml FROM water_log WHERE profile_id=? AND date=?",
                   (profile_id, day_iso))["ml"]
    return {"date": day_iso, "meals": meals, "totals": totals, "water_ml": max(0, water)}


# --------------------------------------------------------------- Wasser
def _water_goal_computed(profile):
    """Trinkmenge in ml: Faustregel ~33 ml/kg Koerpergewicht (EFSA/ACSM-Bereich 30-35 ml/kg),
    plus Zuschlag je nach Aktivitaet – Schwitzen durch Training erhoeht den Bedarf spuerbar.
    Ohne Gewicht ein allgemeiner Richtwert nach Geschlecht (EFSA: ~2,5 l Maenner, ~2,0 l Frauen)."""
    weight = latest_weight(profile["id"])
    days = len(db.parse_weekdays(profile.get("train_weekdays"))) or profile.get("days_per_week") or 0
    level = profile.get("activity_level")
    if level:
        bonus = {"niedrig": 0, "mittel": 350, "hoch": 700}.get(level, 350)
    else:
        bonus = 700 if days >= 5 else 350 if days >= 3 else 0
    base = weight * 33 if weight else {"männlich": 2500, "weiblich": 2000}.get(profile.get("sex"), 2200)
    return int(round((base + bonus) / 50) * 50)


def water_goals(profile):
    """Trinkziel: wie bei den Kalorienzielen berechnet, solange kein eigener Wert gesetzt ist."""
    computed = _water_goal_computed(profile)
    manual = profile.get("water_goal_ml")
    auto = manual is None
    return {"auto": auto, "goal": manual if manual is not None else computed, "computed": computed,
            "from_weight": latest_weight(profile["id"]) is not None}


def portion(food, grams):
    f = grams / 100
    return {k: round((food.get(k) or 0) * f, 1) for k in ("kcal", "protein", "carbs", "fat")}


# ------------------------------------------------------ Open Food Facts
class FoodError(Exception):
    pass


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            raise FoodError("Open Food Facts bremst gerade (zu viele Anfragen) – "
                            "in einer Minute noch einmal versuchen.") from e
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


def _grams(text):
    """Grammangabe aus einem Freitext wie "400 g", "0,5 l" oder "1 Riegel (40 g)"."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\b", (text or "").lower())
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value * {"kg": 1000, "g": 1, "ml": 1, "cl": 10, "l": 1000}[m.group(2)], 1) or None


def _serving_label(text):
    """Portions-Text aus Open Food Facts, sofern er mehr sagt als nur die Grammzahl."""
    text = " ".join((text or "").split())[:60]
    return text if re.search(r"[^\d\s.,gml()]", text.lower()) else None


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
        "serving_g": _num(p.get("serving_quantity")) or None,
        "serving_label": _serving_label(p.get("serving_size")),
        "pack_g": _num(p.get("product_quantity")) or _grams(p.get("quantity")),
        "source": "off",
        # nur fuer die Reihenfolge der Treffer, wird nicht gespeichert
        "dach": bool(DACH & set(p.get("countries_tags") or [])),
    }


FIELDS = ("code,product_name,product_name_de,brands,nutriments,serving_quantity,serving_size,"
          "quantity,product_quantity,countries_tags")
SERVING_FIELDS = "code,serving_quantity,serving_size,product_quantity"
DACH = {"en:germany", "en:austria", "en:switzerland"}
SEARCH = "https://search.openfoodfacts.org/search"
DETAIL_TIMEOUT = 4   # Portionen nachladen darf die Suche nicht lange aufhalten
_servings = {}       # Barcode -> Portionsangaben, damit gleiche Suchen nichts nachladen


# ------------------------------------------------------------ Relevanz
def norm(text):
    """Vergleichsform: klein, ohne Akzente/Umlaute, ohne Apostrophe und Satzzeichen.
    "Müller" und "Mueller" ergeben beide "muller", "McDonald's" wird zu "mcdonalds"."""
    text = (text or "").lower().replace("ß", "ss")
    text = re.sub(r"(?<=[aou])e", "", text)   # ae/oe/ue wie ä/ö/ü – auf beiden Seiten gleich
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"['’`´]", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def compact(text):
    return norm(text).replace(" ", "")


def _similar(a, b):
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.8)


def matches(food, query):
    """Kommen alle Suchwoerter in Name oder Marke vor (als Wortanfang oder Teil)?"""
    hay = norm(f"{food.get('name') or ''} {food.get('brand') or ''}")
    return all(w in hay for w in norm(query).split())


def score(food, query, uses=0):
    """Relevanz eines Treffers: Name vor Marke, exakt vor Wortanfang vor Teilwort.
    Marken werden ohne Leer- und Satzzeichen und etwas unscharf verglichen, damit
    "macdonalds" auch "McDonald's" findet."""
    q = norm(query)
    words = q.split()
    if not words:
        return 0
    name, brand = norm(food.get("name")), compact(food.get("brand"))
    tokens = f"{name} {norm(food.get('brand'))}".split()
    s = 0.0
    if name == q:
        s += 100
    elif name.startswith(q):
        s += 60
    elif re.search(rf"\b{re.escape(q)}", name):
        s += 40
    elif q in name:
        s += 20
    hits = 0
    for w in words:
        if any(t.startswith(w) for t in tokens):
            hits += 1
        elif any(w in t or _similar(w, t) for t in tokens if len(w) >= 4):
            hits += 0.6
    s += 40 * hits / len(words)
    long_words = [w for w in words if len(w) >= 4]
    if brand and len(brand) >= 4 and (brand in compact(query) or _similar(brand, compact(query))
                                      or any(_similar(brand, w) for w in long_words)):
        s += 45
    if food.get("dach"):
        s += 25
    if food.get("serving_g"):
        s += 5
    s += min(uses, 10) * 4   # oft gegessen -> weiter oben
    return s - len(name) / 40   # bei Gleichstand das kuerzere, allgemeinere Produkt zuerst


def rank(foods, query, uses=None):
    uses = uses or {}
    return sorted(foods, key=lambda f: -score(f, query, uses.get(f.get("id"), 0)))


# --------------------------------------------------------------- Suche
def brand_slugs(query):
    """Moegliche Marken-Kuerzel von Open Food Facts ("McDonald's" heisst dort mcdonald-s)."""
    words = norm(query).split()
    if not words or len(words) > 2 or len("".join(words)) < 3:
        return []
    base = "-".join(words)
    slugs = [base, "".join(words)]
    if base.endswith("s") and len(base) > 4:
        slugs.append(base[:-1] + "-s")   # Apostroph-s: mcdonalds -> mcdonald-s
    if base.startswith("mac"):
        slugs += [f"mc{x[3:]}" for x in list(slugs)]
    return list(dict.fromkeys(slugs))


def _search_hits(q, limit):
    data = _get(f"{SEARCH}?" + urllib.parse.urlencode({
        "q": q, "page_size": limit, "langs": "de", "fields": FIELDS})) or {}
    return data.get("hits", [])


def _main_search(query, limit):
    """Volltextsuche. Zuerst der neue Such-Dienst (schnell, kaum Limits), bei Problemen
    die klassische Suche als Rueckfall."""
    try:
        return _search_hits(query, limit)
    except FoodError:
        data = _get(f"{OFF}/cgi/search.pl?" + urllib.parse.urlencode({
            "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
            "page_size": limit, "fields": FIELDS, "lc": "de", "cc": "de"})) or {}
        return data.get("products", [])


def _brand_search(query):
    """Alle Produkte einer Marke aus DE/AT/CH – die Volltextsuche findet Marken nur mit
    exakter Schreibweise. Fehler hier sind egal, die Hauptsuche liefert trotzdem."""
    slugs = brand_slugs(query)
    if not slugs:
        return []
    tags = " OR ".join(f'"{s}"' for s in slugs)
    countries = " OR ".join(f'"{c}"' for c in sorted(DACH))
    try:
        return _search_hits(f"brands_tags:({tags}) AND countries_tags:({countries})", 50)
    except FoodError:
        return []


def _serving(barcode):
    """Portion aus der Produktseite – der Such-Index kennt sie nicht."""
    if barcode not in _servings:   # FoodError (z. B. HTTP 429) wird nicht gemerkt
        if len(_servings) > 5000:
            _servings.clear()
        data = _get(f"{OFF}/api/v2/product/{urllib.parse.quote(barcode)}.json?fields={SERVING_FIELDS}",
                    timeout=DETAIL_TIMEOUT) or {}
        p = data.get("product") or {}
        _servings[barcode] = {"serving_g": _num(p.get("serving_quantity")) or None,
                              "serving_label": _serving_label(p.get("serving_size")),
                              "pack_g": _num(p.get("product_quantity")) or None}
    return _servings[barcode]


def _add_servings(foods):
    def one(food):
        try:
            extra = _serving(food["barcode"])
        except FoodError:
            return
        for k, v in extra.items():
            if v and not food.get(k):
                food[k] = v
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(one, [f for f in foods if f.get("barcode") and not f.get("serving_g")]))


def search_off(query, limit=25, details=5):
    """Treffer aus Open Food Facts, nach Relevanz sortiert, die ersten mit Portion.
    Portionen nur fuer wenige Treffer nachladen: Open Food Facts begrenzt Produktabrufe
    (HTTP 429), die restlichen holt die App erst beim Antippen (portion_off)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        main = pool.submit(_main_search, query, 50)
        brand = pool.submit(_brand_search, query)
        products = brand.result() + main.result()   # main.result() wirft FoodError weiter
    out, seen = [], set()
    for p in products:
        food = _from_off(p)
        if not food:
            continue
        key = food["barcode"] or compact(f"{food['name']} {food['brand'] or ''}")
        alt = compact(f"{food['name']} {food['brand'] or ''}")
        if key in seen or alt in seen:
            continue
        seen.update((key, alt))
        out.append(food)
    out = rank(out, query)[:limit]
    _add_servings(out[:details])
    return rank(out, query)   # mit Portion zaehlt ein Treffer etwas mehr


def portion_off(barcode):
    """Portionsangaben eines einzelnen Produkts, ohne es lokal zu speichern."""
    return _serving(barcode)


def product_off(barcode):
    data = _get(f"{OFF}/api/v2/product/{urllib.parse.quote(barcode)}.json?fields={FIELDS}")
    if not data or data.get("status") != 1:
        return None
    return _from_off(data.get("product") or {})


def remember(food):
    """Produkt lokal ablegen (einmal pro Barcode) und die gespeicherte Zeile liefern."""
    food = {k: v for k, v in food.items() if k != "dach"}
    if food.get("barcode"):
        have = db.one("SELECT * FROM foods WHERE barcode=?", (food["barcode"],))
        if have:
            return have
    elif food.get("source", "eigen") == "eigen":
        # Ohne Barcode entstand frueher bei jedem Anlegen eine Dublette. Gleicher Name und
        # gleiche Marke = dasselbe Lebensmittel: die neuen Werte uebernehmen.
        have = next((f for f in db.rows("SELECT * FROM foods WHERE source='eigen' AND barcode IS NULL")
                     if f["name"].casefold() == food["name"].casefold()
                     and (f["brand"] or "").casefold() == (food.get("brand") or "").casefold()), None)
        if have:
            return db.update_row("foods", have["id"], {k: v for k, v in food.items()
                                                       if k not in ("source", "barcode")})
    return db.insert_row("foods", {**food, "created_at": db.now()})
