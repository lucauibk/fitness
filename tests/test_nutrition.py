import os
import urllib.parse
from datetime import date

import pytest

from app import db, nutrition


def test_computed_goals_reports_missing_data(profile_id):
    profile = db.get_row("profiles", profile_id)
    calc, missing = nutrition.computed_goals(profile)
    assert calc is None
    assert "Körpergewicht" in missing


def test_computed_goals_with_full_profile(profile_id):
    db.update_row("profiles", profile_id, {
        "birth_year": 1990, "sex": "männlich", "height_cm": 180,
        "goal": "aufbau", "activity_level": "mittel"})
    db.set_bodyweight(profile_id, date.today().isoformat(), 80)
    profile = db.get_row("profiles", profile_id)
    calc, missing = nutrition.computed_goals(profile)
    assert missing == []
    assert calc["kcal"] > 0
    assert calc["protein"] > 0
    assert calc["carbs"] >= 0


def test_water_goal_scales_with_weight(profile_id):
    db.update_row("profiles", profile_id, {"sex": "männlich"})
    db.set_bodyweight(profile_id, date.today().isoformat(), 60)
    light = nutrition.water_goals(db.get_row("profiles", profile_id))["goal"]
    db.set_bodyweight(profile_id, date.today().isoformat(), 100)
    heavy = nutrition.water_goals(db.get_row("profiles", profile_id))["goal"]
    assert heavy > light


def test_water_goal_manual_override_wins(profile_id):
    db.update_row("profiles", profile_id, {"water_goal_ml": 3000})
    result = nutrition.water_goals(db.get_row("profiles", profile_id))
    assert result["auto"] is False
    assert result["goal"] == 3000


# ------------------------------------------------------------- Suche
def _food(name, brand=None, **kw):
    return {"name": name, "brand": brand, "kcal": 100, **kw}


def test_norm_ignores_spelling_details():
    assert nutrition.compact("McDonald's") == nutrition.compact("mcdonalds") == nutrition.compact("Mc Donalds")
    assert nutrition.norm("Müsli") == "musli"
    assert nutrition.norm("Müller") == nutrition.norm("Mueller")


def test_score_prefers_exact_name_brand_and_dach():
    q = "apfel"
    assert [f["name"] for f in nutrition.rank([_food("Apfelmus"), _food("Apfel")], q)] == ["Apfel", "Apfelmus"]
    ranked = nutrition.rank([_food("Scottish Shortbread", "Walkers"), _food("Big Mac", "McDonald's")], "macdonalds")
    assert ranked[0]["name"] == "Big Mac"
    ranked = nutrition.rank([_food("Skyr", "Arla"), _food("Skyr", "Arla UK", dach=True)], "skyr")
    assert ranked[0]["dach"]


def test_score_ignores_short_brand_lookalikes():
    ranked = nutrition.rank([_food("Still Apple", "Mac B"), _food("Big Mac", "McDonald's")], "big mac")
    assert ranked[0]["name"] == "Big Mac"


def test_brand_slugs_cover_apostrophe_and_mac():
    assert "mcdonald-s" in nutrition.brand_slugs("mcdonalds")
    assert "mcdonald-s" in nutrition.brand_slugs("macdonalds")
    assert nutrition.brand_slugs("drei worte hier") == []


def test_from_off_reads_portion_and_pack():
    p = {"code": "123", "product_name": "Riegel", "brands": "X", "countries_tags": ["en:austria"],
         "nutriments": {"energy-kcal_100g": 450}, "serving_quantity": "40",
         "serving_size": "1 Riegel (40 g)", "quantity": "4 x 40 g"}
    food = nutrition._from_off(p)
    assert food["serving_g"] == 40 and food["serving_label"] == "1 Riegel (40 g)"
    assert food["pack_g"] == 40   # nur die erste Grammangabe im Text – daher product_quantity bevorzugt
    assert food["dach"]
    assert nutrition._from_off({**p, "product_quantity": 160})["pack_g"] == 160
    assert nutrition._from_off({**p, "serving_size": "40 g"})["serving_label"] is None
    assert nutrition._from_off({**p, "nutriments": {}}) is None


def test_search_off_merges_ranks_and_survives_portion_errors(monkeypatch):
    def hit(code, name, brand, countries=("en:germany",)):
        return {"code": code, "product_name": name, "brands": brand, "countries_tags": list(countries),
                "nutriments": {"energy-kcal_100g": 250}}

    def fake_get(url, timeout=None):
        if "/api/v2/product/" in url:
            if "222" in url:
                raise nutrition.FoodError("HTTP 429")
            return {"product": {"serving_quantity": 215, "serving_size": "1 Burger (215 g)"}}
        if "brands_tags" in urllib.parse.unquote_plus(url):
            return {"hits": [hit("111", "Big Mac", "McDonald's"), hit("222", "Pommes", "McDonald's")]}
        return {"hits": [hit("333", "Shortbread", "Macdonalds", ("en:united-kingdom",)),
                         hit("111", "Big Mac", "McDonald's"), {"code": "444", "product_name": "Lippenstift"}]}

    nutrition._servings.clear()
    monkeypatch.setattr(nutrition, "_get", fake_get)
    out = nutrition.search_off("macdonalds")
    codes = [f["barcode"] for f in out]
    assert sorted(codes) == ["111", "222", "333"]   # doppelt und ohne kcal fallen weg
    assert codes[-1] == "333"                        # fremdes Land nach hinten
    big = next(f for f in out if f["barcode"] == "111")
    assert big["serving_g"] == 215 and big["serving_label"] == "1 Burger (215 g)"
    assert next(f for f in out if f["barcode"] == "222")["serving_g"] is None


def test_local_search_ignores_case_umlauts_and_prefers_used(profile_id):
    from app.routes import nutrition as routes
    a = nutrition.remember({"name": "Müsli Beere", "kcal": 380, "source": "eigen"})
    b = nutrition.remember({"name": "Müsli Schoko", "kcal": 420, "source": "eigen"})
    for _ in range(3):
        db.insert_row("food_log", {"profile_id": profile_id, "date": date.today().isoformat(),
                                   "meal": "snack", "food_id": b["id"], "name": b["name"], "grams": 60})
    found = routes._local("mueSLI", profile_id)
    ids = [f["id"] for f in found]
    assert a["id"] in ids and ids.index(b["id"]) < ids.index(a["id"])
    assert found[0]["uses"] == 3 and found[0]["last_grams"] == 60


# ------------------------------------------------ eigene Lebensmittel
def _own(name="Haferbrei", **kw):
    from app.routes import nutrition as routes
    body = routes.FoodIn(name=name, kcal=kw.pop("kcal", 100), **kw)
    return routes, routes.add_food(body)


def test_eigenes_lebensmittel_ohne_barcode_wird_nicht_doppelt_angelegt():
    name = f"Brei-{os.urandom(3).hex()}"
    routes, first = _own(name, kcal=100)
    _, again = _own(name.upper(), kcal=120)
    assert again["id"] == first["id"]
    assert again["kcal"] == 120


def test_eigenes_lebensmittel_bearbeiten_und_loeschen(profile_id):
    routes, food = _own(f"Brot-{os.urandom(3).hex()}", kcal=250)
    changed = routes.patch_food(food["id"], routes.FoodIn(name=food["name"], kcal=260, serving_g=40))
    assert changed["kcal"] == 260 and changed["serving_g"] == 40
    with db.connect() as c:
        c.execute("INSERT INTO food_log (profile_id, date, meal, food_id, name, grams, kcal)"
                  " VALUES (?,?,?,?,?,?,?)", (profile_id, "2026-09-01", "abend", food["id"], food["name"], 80, 208))
    routes.delete_food(food["id"])
    assert db.get_row("foods", food["id"]) is None
    row = db.one("SELECT * FROM food_log WHERE name=?", (food["name"],))
    assert row["food_id"] is None and row["kcal"] == 208   # der alte Eintrag bleibt


def test_fremde_produkte_sind_nicht_aenderbar():
    from fastapi import HTTPException
    from app.routes import nutrition as routes
    food = db.insert_row("foods", {"name": "OFF-Produkt", "kcal": 50, "source": "off",
                                   "barcode": str(10**12 + int.from_bytes(os.urandom(3), "big"))})
    with pytest.raises(HTTPException):
        routes.delete_food(food["id"])


def test_saved_meal_roundtrip_and_profile_isolation(profile_id):
    from fastapi import HTTPException
    from app.routes import nutrition as routes

    food = db.insert_row("foods", {"name": "Haferflocken", "kcal": 370, "protein": 13,
                                   "carbs": 59, "fat": 7, "source": "eigen"})
    saved = routes.save_meal(routes.SavedMealIn(
        profile_id=profile_id, name="Frühstück Standard",
        items=[routes.MealItem(food_id=food["id"], grams=100)]))
    assert saved["kcal"] == 370
    assert [m["name"] for m in routes.list_meals(profile_id)] == ["Frühstück Standard"]
    other = db.insert_row("profiles", {"name": "Anderes Profil"})["id"]
    assert routes.list_meals(other) == []
    with pytest.raises(HTTPException):
        routes.delete_meal(saved["id"], other)
    routes.delete_meal(saved["id"], profile_id)
    assert routes.list_meals(profile_id) == []


def test_day_reports_burned_kcal_from_cardio(profile_id):
    day = date.today().isoformat()
    db.insert_row("cardio", {"profile_id": profile_id, "date": day, "activity": "Laufen",
                             "minutes": 30, "kcal": 300})
    assert nutrition.day(profile_id, day)["burned_kcal"] == 300


# ------------------------------------------------- Kalorien- und Eiweissbedarf
from datetime import timedelta


def _profil(profile_id, **extra):
    db.update_row("profiles", profile_id, {
        "birth_year": date.today().year - 34, "sex": "männlich", "height_cm": 180,
        "goal": "fitness", "activity_level": "niedrig", "session_minutes": 60, **extra})
    db.set_bodyweight(profile_id, date.today().isoformat(), 80)
    return db.get_row("profiles", profile_id)


def _ziele(profile_id, **extra):
    calc, missing = nutrition.computed_goals(_profil(profile_id, **extra))
    assert missing == []
    return calc


def test_mehr_training_gibt_mehr_kalorien_auch_mit_gesetzter_alltagsaktivitaet(profile_id):
    # Fruehere Formel nahm bei gesetztem Level immer denselben Faktor, egal wie oft trainiert wird.
    wenig = _ziele(profile_id, train_weekdays="0")
    viel = _ziele(profile_id, train_weekdays="0,1,2,3,4,5")
    assert viel["tdee"] > wenig["tdee"] + 200
    assert viel["breakdown"]["sessions"] == 6 and wenig["breakdown"]["sessions"] == 1


def test_laengere_einheiten_und_hoehere_alltagsaktivitaet_zaehlen(profile_id):
    basis = _ziele(profile_id, train_weekdays="0,2,4")["tdee"]
    assert _ziele(profile_id, train_weekdays="0,2,4", session_minutes=90)["tdee"] > basis
    assert _ziele(profile_id, train_weekdays="0,2,4", activity_level="hoch")["tdee"] > basis


def test_rechenweg_ist_nachvollziehbar(profile_id):
    c = _ziele(profile_id, train_weekdays="0,2,4,5")
    b = c["breakdown"]
    assert b["formula"] == "Mifflin-St Jeor" and b["body_fat"] is None
    assert b["bmr"] == 1760                                  # 10*80 + 6.25*180 - 5*34 + 5
    assert b["daily_kcal"] == round(1760 * 1.3)
    assert c["tdee"] == b["daily_kcal"] + b["training_kcal"] or abs(c["tdee"] - b["daily_kcal"] - b["training_kcal"]) <= 1


def test_abnehmen_defizit_liegt_im_esn_bereich_und_nie_unter_dem_grundumsatz(profile_id):
    c = _ziele(profile_id, goal="abnehmen", train_weekdays="0,2,4")
    assert -500 <= c["breakdown"]["adjustment"] <= -300
    assert c["kcal"] >= c["breakdown"]["bmr"]
    # Sehr leichte Person mit kleinem Verbrauch: das Defizit darf nicht unter den Grundumsatz druecken
    db.set_bodyweight(profile_id, date.today().isoformat(), 45)
    klein, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert klein["kcal"] >= klein["breakdown"]["bmr"]


def test_aufbau_ueberschuss_liegt_im_esn_bereich(profile_id):
    c = _ziele(profile_id, goal="aufbau", train_weekdays="0,2,4")
    assert 200 <= c["breakdown"]["adjustment"] <= 500
    assert _ziele(profile_id, goal="fitness")["breakdown"]["adjustment"] == 0


def test_katch_mcardle_nur_mit_aktuellem_gemessenem_koerperfett(profile_id):
    from app import body
    _profil(profile_id)
    body.save(profile_id, {"date": date.today().isoformat(), "body_fat_pct": 15.0})
    c, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert c["breakdown"]["formula"] == "Katch-McArdle"
    assert c["breakdown"]["bmr"] == round(370 + 21.6 * 80 * 0.85)
    # zu alt -> wieder Mifflin
    with db.connect() as conn:
        conn.execute("UPDATE body_measurements SET date=? WHERE profile_id=?",
                     ((date.today() - timedelta(days=200)).isoformat(), profile_id))
    c, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert c["breakdown"]["formula"] == "Mifflin-St Jeor"


def test_geschaetztes_koerperfett_aus_der_navy_formel_wird_nicht_verwendet(profile_id):
    from app import body
    _profil(profile_id)
    body.save(profile_id, {"date": date.today().isoformat(), "waist_cm": 85, "neck_cm": 38})
    row = db.one("SELECT body_fat_pct, body_fat_estimated FROM body_measurements WHERE profile_id=?", (profile_id,))
    assert row["body_fat_estimated"] == 1 and row["body_fat_pct"] is not None
    c, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert c["breakdown"]["formula"] == "Mifflin-St Jeor"


def test_unplausibles_koerperfett_weicht_auf_mifflin_aus(profile_id):
    from app import body
    _profil(profile_id)
    body.save(profile_id, {"date": date.today().isoformat(), "body_fat_pct": 60.0})   # Katch ~ -37 %
    c, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert c["breakdown"]["formula"] == "Mifflin-St Jeor" and c["breakdown"]["body_fat"] is None


def test_makros_ergeben_die_kalorien_und_haben_untergrenzen(profile_id):
    for goal in ("abnehmen", "aufbau", "kraft", "fitness"):
        c = _ziele(profile_id, goal=goal, train_weekdays="0,2,4")
        assert c["fat"] >= 64                                  # 0,8 g/kg bei 80 kg
        assert c["protein"] * 4 <= c["kcal"] * 0.35 + 4
        assert abs(c["protein"] * 4 + c["carbs"] * 4 + c["fat"] * 9 - c["kcal"]) <= 12
    assert _ziele(profile_id, goal="abnehmen")["protein"] > _ziele(profile_id, goal="fitness")["protein"]


def test_eiweiss_mit_koerperfett_richtet_sich_nach_der_fettfreien_masse(profile_id):
    from app import body
    ohne = _ziele(profile_id, goal="aufbau")["protein"]
    assert ohne == round(80 * 1.9)
    body.save(profile_id, {"date": date.today().isoformat(), "body_fat_pct": 30.0})
    c, _ = nutrition.computed_goals(db.get_row("profiles", profile_id))
    assert c["protein"] == round(80 * 0.7 * 2.2)               # 56 kg fettfreie Masse, nicht 80 kg


def test_eiweiss_ist_auf_35_prozent_der_kalorien_begrenzt(profile_id):
    # Beim Abnehmen (2,2 g/kg = 176 g) wuerde ein kleines Kalorienziel zu viel Eiweiss verlangen
    c = _ziele(profile_id, goal="abnehmen")
    assert c["protein"] == round(c["kcal"] * 0.35 / 4) < round(80 * 2.2)
