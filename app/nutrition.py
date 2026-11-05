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
# Alltags-PAL OHNE Sport (DGE/WHO-Bereiche): sitzend 1,2-1,4, viel auf den Beinen 1,5-1,6,
# koerperliche Arbeit 1,7+. Sport kommt getrennt dazu (training_kcal), damit 1 oder 6 Einheiten
# pro Woche nicht denselben Faktor ergeben. Unbekannt: leicht aktiv.
ACTIVITY = {"niedrig": 1.3, "mittel": 1.5, "hoch": 1.7}
PAL_DEFAULT = 1.4
STRENGTH_MET = 5.0          # Krafttraining, kraeftig (Compendium of Physical Activities)
DEFAULT_SESSION_MIN = 60
DEFAULT_SESSIONS = 3
BODY_FAT_MAX_AGE_DAYS = 120  # aeltere Koerperfett-Messungen gelten nicht mehr als aktuell
KATCH_MAX_DEVIATION = 0.20   # weicht Katch-McArdle >20 % von Mifflin ab: Messung unplausibel
# Ziel-Anpassung (ESN: Fettabbau -300..-500 kcal, Muskelaufbau +200..+500 kcal)
CUT_PCT_BW_PER_WEEK = 0.5    # ~0,5 % Koerpergewicht pro Woche
KCAL_PER_KG_FAT = 7700
CUT_RANGE = (300, 500)
BULK_PCT, BULK_RANGE = 0.10, (200, 500)
GOAL_FIXED = {"kraft": 150, "fitness": 0}
# Eiweiss in g pro kg Koerpergewicht (ohne Koerperfett) bzw. kg fettfreier Masse (mit).
# Basis: ISSN 2017, Morton et al. 2018 (Plateau ~1,6 g/kg, im Defizit bis ~2,2), Helms 2014.
PROTEIN_PER_KG = {"abnehmen": 2.2, "aufbau": 1.9, "kraft": 1.9, "fitness": 1.6}
PROTEIN_PER_KG_LEAN = {"abnehmen": 2.6, "aufbau": 2.2, "kraft": 2.2, "fitness": 1.8}
PROTEIN_MAX_KCAL_SHARE = 0.35
FAT_MIN_G_PER_KG = 0.8
FAT_KCAL_SHARE = 0.25


# ------------------------------------------------------------- Ziele
def bmr(weight, height, age, sex):
    """Mifflin-St Jeor (genauer als Harris-Benedict, die ueberschaetzt tendenziell).
    Ohne Geschlecht der Mittelwert beider Formeln."""
    base = 10 * weight + 6.25 * height - 5 * age
    if sex == "männlich":
        return base + 5
    if sex == "weiblich":
        return base - 161
    return base - 78


def katch_mcardle(weight, body_fat_pct):
    """Grundumsatz aus der fettfreien Masse – genauer, wenn der Koerperfettanteil bekannt ist."""
    return 370 + 21.6 * weight * (1 - body_fat_pct / 100)


def _body_fat(profile_id):
    """Letzter gemessener (nicht per Navy-Formel geschaetzter) Koerperfettanteil, sonst None."""
    row = db.one("SELECT body_fat_pct, date FROM body_measurements WHERE profile_id=?"
                 " AND body_fat_pct IS NOT NULL AND body_fat_estimated=0 ORDER BY date DESC, id DESC",
                 (profile_id,))
    if not row:
        return None
    age_days = (date.today() - date.fromisoformat(row["date"])).days
    return row["body_fat_pct"] if age_days <= BODY_FAT_MAX_AGE_DAYS else None


def training_kcal(weight, sessions, minutes):
    """Durchschnittlicher Mehrverbrauch pro Tag durch Krafttraining (netto: MET minus
    Ruhe, denn der Alltags-PAL enthaelt den Grundumsatz schon)."""
    return (STRENGTH_MET - 1) * weight * (minutes / 60) * sessions / 7


def goal_adjustment(goal, tdee, weight):
    if goal == "abnehmen":
        cut = CUT_PCT_BW_PER_WEEK / 100 * weight * KCAL_PER_KG_FAT / 7
        return -round(min(max(cut, CUT_RANGE[0]), CUT_RANGE[1]))
    if goal == "aufbau":
        return round(min(max(tdee * BULK_PCT, BULK_RANGE[0]), BULK_RANGE[1]))
    return GOAL_FIXED.get(goal, 0)


def computed_goals(profile):
    """Berechnete Ziele oder None, wenn Angaben fehlen (was genau, steht in missing).
    Aufbau wie bei ESN: Grundumsatz x Alltags-PAL + Sport, dann Ziel-Zu-/Abschlag."""
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
    sex = profile.get("sex")
    base = bmr(weight, profile["height_cm"], age, sex)
    formula, body_fat = "Mifflin-St Jeor", _body_fat(profile["id"])
    if body_fat is not None and 2 <= body_fat <= 60:
        lean = katch_mcardle(weight, body_fat)
        if abs(lean / base - 1) <= KATCH_MAX_DEVIATION:
            base, formula = lean, "Katch-McArdle"
        else:
            body_fat = None
    else:
        body_fat = None
    pal = ACTIVITY.get(profile.get("activity_level") or "", PAL_DEFAULT)
    sessions = len(db.parse_weekdays(profile.get("train_weekdays"))) or profile.get("days_per_week") \
        or DEFAULT_SESSIONS
    minutes = profile.get("session_minutes") or DEFAULT_SESSION_MIN
    sport = training_kcal(weight, sessions, minutes)
    tdee = base * pal + sport
    goal = profile.get("goal") or "fitness"
    adjust = goal_adjustment(goal, tdee, weight)
    kcal = max(round((tdee + adjust) / 10) * 10, round(base / 10) * 10)   # nie unter den Grundumsatz
    per_kg = PROTEIN_PER_KG_LEAN if body_fat is not None else PROTEIN_PER_KG
    ref = weight * (1 - body_fat / 100) if body_fat is not None else weight
    protein = round(min(ref * per_kg.get(goal, per_kg["fitness"]), kcal * PROTEIN_MAX_KCAL_SHARE / 4))
    fat = round(max(weight * FAT_MIN_G_PER_KG, kcal * FAT_KCAL_SHARE / 9))
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat,
            "tdee": round(tdee), "weight": weight,
            "breakdown": {"bmr": round(base), "formula": formula, "pal": pal,
                          "daily_kcal": round(base * pal), "training_kcal": round(sport),
                          "sessions": sessions, "adjustment": adjust, "body_fat": body_fat}}, []


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
    burned = db.one("SELECT COALESCE(SUM(kcal), 0) AS kcal FROM cardio WHERE profile_id=? AND date=?",
                    (profile_id, day_iso))["kcal"]
    return {"date": day_iso, "meals": meals, "totals": totals, "water_ml": max(0, water),
            "burned_kcal": round(burned or 0)}


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
