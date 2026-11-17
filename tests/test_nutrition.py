import os
import urllib.parse
from datetime import date, timedelta

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
    if not db.one("SELECT 1 FROM bodyweight WHERE profile_id=?", (profile_id,)):
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


# ------------------------------------- Trainingstag-Ziele, Tages- und Wochenfeedback
def _logge(profile_id, tag, kcal, protein, meal="mittag"):
    db.insert_row("food_log", {"profile_id": profile_id, "date": tag.isoformat(), "meal": meal,
                               "food_id": None, "grams": 0, "name": "Test", "kcal": kcal,
                               "protein": protein, "carbs": 0, "fat": 0})


def _wochentag_von(tag):
    return str(tag.weekday())


def test_trainingstag_hat_mehr_kalorien_als_ruhetag_und_das_wochenmittel_bleibt(profile_id):
    c = _ziele(profile_id, train_weekdays="0,2,4", goal="aufbau")
    train, rest = c["by_day"]["training"], c["by_day"]["rest"]
    assert train["kcal"] > rest["kcal"] and train["carbs"] > rest["carbs"]
    assert train["protein"] == rest["protein"]
    mittel = (3 * train["kcal"] + 4 * rest["kcal"]) / 7
    assert abs(mittel - c["kcal"]) <= 20


def test_goals_nimmt_nach_wochentag_das_trainings_oder_ruhetagsziel(profile_id):
    profil = _profil(profile_id, train_weekdays="0,2,4")          # Mo, Mi, Fr
    montag, dienstag = "2026-11-02", "2026-11-03"
    t, r = nutrition.goals(profil, montag), nutrition.goals(profil, dienstag)
    assert t["day_type"] == "training" and r["day_type"] == "rest"
    assert t["goals"]["kcal"] > r["goals"]["kcal"]
    assert nutrition.goals(profil)["day_type"] is None             # ohne Tag: Wochenmittel


def test_ohne_trainingsplan_gilt_das_wochenmittel_statt_eines_geschaetzten_ruhetags(profile_id):
    profil = _profil(profile_id, train_weekdays=None)
    g = nutrition.goals(profil, "2026-11-03")
    assert g["day_type"] is None and g["goals"]["kcal"] == g["computed"]["kcal"]


def test_gemachte_einheit_macht_den_tag_zum_trainingstag_auch_ohne_plan(profile_id):
    profil = _profil(profile_id, train_weekdays="0")                # nur Montag geplant
    db.insert_row("workouts", {"profile_id": profile_id, "started_at": "2026-11-03T16:00:00+00:00",
                               "finished_at": "2026-11-03T17:00:00+00:00"})    # Dienstag
    assert nutrition.goals(profil, "2026-11-03")["day_type"] == "training"


def test_einheit_kurz_nach_mitternacht_zaehlt_zum_lokalen_tag(profile_id):
    profil = _profil(profile_id, train_weekdays="0")
    # 23:30 UTC am Dienstag = 00:30 MEZ am Mittwoch
    db.insert_row("workouts", {"profile_id": profile_id, "started_at": "2026-11-03T23:30:00+00:00",
                               "finished_at": "2026-11-04T00:30:00+00:00"})
    assert nutrition.goals(profil, "2026-11-04")["day_type"] == "training"
    assert nutrition.goals(profil, "2026-11-03")["day_type"] == "rest"


def test_eigene_ziele_gelten_an_jedem_tag_gleich(profile_id):
    profil = _profil(profile_id, train_weekdays="0,2,4", nutrition_auto=0, kcal_goal=2500,
                     protein_goal=150, carbs_goal=250, fat_goal=70)
    g = nutrition.goals(profil, "2026-11-02")
    assert g["day_type"] is None and g["goals"]["kcal"] == 2500


def test_tagesfeedback_eiweiss_kalorien_und_trainingstag():
    info = {"goals": {"kcal": 2500, "protein": 150}, "day_type": "training"}
    ok = nutrition.day_feedback(info, {"kcal": 1500, "protein": 60}, 0)
    texte = [f["text"] for f in ok]
    assert texte == ["Noch 1000 kcal frei.", "Noch 90 g Eiweiß – am besten nach dem Training."]
    ruhe = nutrition.day_feedback({**info, "day_type": "rest"}, {"kcal": 1500, "protein": 60}, 0)
    assert ruhe[1]["text"] == "Noch 90 g Eiweiß."
    ueber = nutrition.day_feedback({"goals": {"kcal": 2000, "protein": 100}, "day_type": "rest"},
                                   {"kcal": 2600, "protein": 120}, 0)
    assert ueber[0]["tone"] == "warn" and "600 kcal über dem Ziel" in ueber[0]["text"]
    assert ueber[1]["text"] == "Eiweißziel erreicht."
    assert nutrition.day_feedback(info, {"kcal": 2480, "protein": 150}, 0)[0]["tone"] == "good"
    # verbrannte Kalorien aus Cardio erhoehen den Rahmen wie im Ring
    assert "Noch 300 kcal frei." in [f["text"] for f in nutrition.day_feedback(
        {"goals": {"kcal": 2000, "protein": 0}, "day_type": None}, {"kcal": 2000, "protein": 0}, 300)]


def test_tagesfeedback_ohne_eintraege_oder_ziel_ist_leer():
    assert nutrition.day_feedback({"goals": {"kcal": 2000}, "day_type": None}, {"kcal": 0, "protein": 0}) == []
    assert nutrition.day_feedback({"goals": {"kcal": None}, "day_type": None}, {"kcal": 500, "protein": 20}) == []


def test_wochenbilanz_zaehlt_nur_abgeschlossene_tage_mit_eintraegen(profile_id, monkeypatch):
    from datetime import timedelta
    heute = date(2026, 11, 5)
    monkeypatch.setattr(nutrition, "local_today", lambda: heute)
    profil = _profil(profile_id, train_weekdays=None, goal="fitness", nutrition_auto=0, kcal_goal=2000,
                     protein_goal=100, carbs_goal=200, fat_goal=60)
    for vor, kcal, prot in ((1, 2000, 100), (2, 1900, 95), (3, 2600, 60), (5, 2050, 110)):
        _logge(profile_id, heute - timedelta(days=vor), kcal, prot)
    _logge(profile_id, heute, 300, 20)                      # heute: noch offen, zaehlt nicht
    w = nutrition.week(profil, heute.isoformat())
    assert w["counted"] == 4 and len(w["days"]) == 7
    assert w["avg_kcal"] == round((2000 + 1900 + 2600 + 2050) / 4)
    assert w["kcal_ok"] == 3 and w["protein_ok"] == 3       # 2600 kcal und 60 g Eiweiß fallen raus
    assert [d["open"] for d in w["days"]][-1] is True
    assert w["days"][-1]["logged"] is True and w["days"][2]["logged"] is False   # leer ist nicht 0 kcal
    assert "Im Schnitt" in w["text"][0] and "3 von 4" in w["text"][-1]


def test_wochenbilanz_ohne_genug_tage_sagt_das_ehrlich(profile_id, monkeypatch):
    from datetime import timedelta
    heute = date(2026, 11, 5)
    monkeypatch.setattr(nutrition, "local_today", lambda: heute)
    profil = _profil(profile_id, nutrition_auto=0, kcal_goal=2000, protein_goal=100)
    _logge(profile_id, heute - timedelta(days=1), 1800, 90)
    w = nutrition.week(profil, heute.isoformat())
    assert w["avg_kcal"] is None and "fehlen noch Einträge" in w["text"][0]


def test_wochenbilanz_bewertet_deutlich_zu_wenig_vorsichtig(profile_id, monkeypatch):
    from datetime import timedelta
    heute = date(2026, 11, 5)
    monkeypatch.setattr(nutrition, "local_today", lambda: heute)
    profil = _profil(profile_id, nutrition_auto=0, kcal_goal=2500, protein_goal=100)
    for vor in (1, 2, 3):
        _logge(profile_id, heute - timedelta(days=vor), 1500, 80)
    assert any("deutlich unter dem Ziel" in t and "fehlen Einträge" in t
               for t in nutrition.week(profil, heute.isoformat())["text"])


def test_routen_liefern_feedback_und_woche(profile_id):
    from app.routes import nutrition as routes
    _profil(profile_id, train_weekdays="0,2,4")
    _logge(profile_id, date.today(), 800, 30)
    tag = routes.get_day(profile_id, date.today().isoformat())
    assert tag["feedback"] and tag["goals"]["day_type"] in ("training", "rest")
    woche = routes.get_week(profile_id, date.today().isoformat())
    assert woche["to"] == date.today().isoformat() and len(woche["days"]) == 7


# ------------------------------------------------- Untergrenze, Tempo, Vorschau
def _klein(profile_id, sex, **extra):
    """Kleine, leichte Person mit niedrigem Verbrauch: Grundumsatz weit unter den Untergrenzen."""
    extra.setdefault("goal", "abnehmen")
    p = _profil(profile_id, sex=sex, height_cm=150, birth_year=date.today().year - 60, **extra)
    db.set_bodyweight(profile_id, date.today().isoformat(), 45)
    return p


def test_untergrenze_frau_1200_im_defizit(profile_id):
    calc, _ = nutrition.computed_goals(_klein(profile_id, "weiblich", nutrition_pace="sanft"))
    b = calc["breakdown"]
    assert b["bmr"] < 1000 and b["floor"] == 1200 and b["floor_applied"] is True
    assert calc["kcal"] == 1200
    assert calc["by_day"]["rest"]["kcal"] >= 1200 and calc["by_day"]["training"]["kcal"] >= 1200


def test_untergrenze_mann_1500_im_defizit(profile_id):
    calc, _ = nutrition.computed_goals(_klein(profile_id, "männlich"))
    assert calc["breakdown"]["floor"] == 1500 and calc["kcal"] == 1500
    assert min(d["kcal"] for d in calc["by_day"].values()) >= 1500


def test_untergrenze_gilt_nur_im_defizit(profile_id):
    # Erhaltung bei kleinem Verbrauch wird nicht auf 1200 hochgezogen (das waere ein Ueberschuss ueber dem Bedarf)
    calc, _ = nutrition.computed_goals(_klein(profile_id, "weiblich", goal="fitness"))
    b = calc["breakdown"]
    assert b["floor_applied"] is False and b["floor"] < 1200 and calc["kcal"] == round(calc["tdee"] / 10) * 10


def test_rechenweg_geht_mit_untergrenze_auf(profile_id):
    c, _ = nutrition.computed_goals(_klein(profile_id, "weiblich"))
    b = c["breakdown"]
    # Ohne Untergrenze laege das Ziel bei tdee + adjustment; mit ihr genau bei b["floor"]
    assert round((c["tdee"] + b["adjustment"]) / 10) * 10 < b["floor"] == c["kcal"]


def test_normale_person_bleibt_ueber_der_untergrenze(profile_id):
    c = _ziele(profile_id, goal="abnehmen", train_weekdays="0,2,4")
    assert c["breakdown"]["floor_applied"] is False and c["kcal"] > 1500


@pytest.mark.parametrize("goal", ["abnehmen", "aufbau"])
def test_tempo_ordnet_die_ziele(profile_id, goal):
    kcal = {p: _ziele(profile_id, goal=goal, nutrition_pace=p, train_weekdays="0,2,4")["kcal"]
            for p in nutrition.PACES}
    if goal == "abnehmen":
        assert kcal["sanft"] > kcal["standard"] > kcal["zuegig"]
    else:
        assert kcal["sanft"] < kcal["standard"] < kcal["zuegig"]
    # Ohne Auswahl gilt "standard" wie bisher
    assert _ziele(profile_id, goal=goal, nutrition_pace=None, train_weekdays="0,2,4")["kcal"] == kcal["standard"]


def test_tempo_aendert_kraft_und_fitness_nicht(profile_id):
    for goal in ("kraft", "fitness"):
        base = _ziele(profile_id, goal=goal)["kcal"]
        assert _ziele(profile_id, goal=goal, nutrition_pace="zuegig")["kcal"] == base


def test_eiweiss_pro_kg_im_rechenweg(profile_id):
    c = _ziele(profile_id, goal="aufbau")
    b = c["breakdown"]
    assert b["protein_per_kg"] == nutrition.PROTEIN_PER_KG["aufbau"] and b["protein_basis_kg"] == 80
    assert c["protein"] == round(80 * b["protein_per_kg"]) and b["protein_capped"] is False
    # Mit gemessenem Koerperfett: fettfreie Masse als Bezug und hoeherer Faktor
    from app import body
    body.save(profile_id, {"date": date.today().isoformat(), "body_fat_pct": 25.0})
    b = nutrition.computed_goals(db.get_row("profiles", profile_id))[0]["breakdown"]
    assert b["protein_basis_kg"] == 60 and b["protein_per_kg"] == nutrition.PROTEIN_PER_KG_LEAN["aufbau"]


def test_vorschau_speichert_nichts_und_zeigt_alle_tempi(profile_id):
    _profil(profile_id, goal="fitness", activity_level="niedrig")
    before = db.get_row("profiles", profile_id)
    out = nutrition.preview(before, goal="abnehmen", activity_level="hoch")
    assert out["missing"] == [] and set(out["paces"]) == set(nutrition.PACES)
    assert out["paces"]["standard"]["kcal"] == out["computed"]["kcal"]
    assert out["paces"]["sanft"]["weekly_kg"] < out["paces"]["zuegig"]["weekly_kg"]
    assert out["computed"]["breakdown"]["pal"] == nutrition.ACTIVITY["hoch"]
    assert db.get_row("profiles", profile_id) == before          # nichts geschrieben
    assert nutrition.preview(before, goal="fitness")["paces"] == {}


def test_vorschau_ohne_daten_nennt_fehlendes(profile_id):
    out = nutrition.preview(db.get_row("profiles", profile_id), goal="abnehmen")
    assert out["computed"] is None and out["paces"] == {} and "Körpergewicht" in out["missing"]


def test_vorschau_route_und_tempo_im_profil(profile_id):
    from app import main
    from app.routes import nutrition as routes
    _profil(profile_id)
    out = routes.preview_goals(profile_id=profile_id, goal="abnehmen", activity_level="mittel", pace="zuegig")
    assert out["computed"]["breakdown"]["pace"] == "zuegig" and out["water_ml"] > 0
    saved = main.api_update_profile(profile_id, main.ProfileIn(nutrition_pace="sanft"))
    assert saved["nutrition_pace"] == "sanft"
    assert main.api_update_profile(profile_id, main.ProfileIn(nutrition_pace=None))["nutrition_pace"] is None
    with pytest.raises(Exception):
        main.ProfileIn(nutrition_pace="turbo")


def test_vorschau_http_nimmt_nur_bekannte_werte(profile_id):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _profil(profile_id)
    ok = client.get(f"/api/nutrition/goals/preview?profile={profile_id}&goal=abnehmen&activity_level=mittel&pace=sanft")
    assert ok.status_code == 200 and ok.json()["computed"]["breakdown"]["pace"] == "sanft"
    for query in ("pace=turbo", "goal=fliegen", "activity_level=extrem"):
        bad = client.get(f"/api/nutrition/goals/preview?profile={profile_id}&{query}")
        assert bad.status_code in (400, 422), query
    # Die Vorschau schreibt nichts ins Profil
    assert db.get_row("profiles", profile_id)["nutrition_pace"] is None


# ------------------------------------------------- Nachsteuerung am Gewichtsverlauf
def _gewichte(profile_id, kgs, ende=None, abstand=14):
    """Gewichte rueckwaerts ab `ende` (Standard heute) im Abstand von `abstand` Tagen, aelteste zuerst."""
    ende = ende or date.today()
    for i, kg in enumerate(reversed(kgs)):
        db.set_bodyweight(profile_id, (ende - timedelta(days=abstand * i)).isoformat(), kg)


def _aufbau(profile_id, **extra):
    return _ziele(profile_id, goal="aufbau", **extra)


def test_trend_sinkendes_gewicht_beim_aufbau_hebt_das_ziel(profile_id):
    ohne = _aufbau(profile_id)
    _gewichte(profile_id, [85.0, 84.3, 83.6, 83.0])        # ca. -0,35 kg/Woche, Ziel Aufbau
    mit = _aufbau(profile_id)
    b = mit["breakdown"]
    assert b["trend_adjust"] > 0 and b["trend_rate"] < 0
    assert mit["kcal"] > ohne["kcal"]
    assert b["adjustment"] - b["trend_adjust"] > 0          # der Zuschlag fuers Ziel bleibt, die Korrektur kommt dazu


def test_trend_im_zielband_aendert_nichts(profile_id):
    _gewichte(profile_id, [80.0, 80.4, 80.8, 81.2])        # ca. +0,2 kg/Woche = 0,25 %/Woche
    assert _aufbau(profile_id)["breakdown"]["trend_adjust"] == 0


def test_trend_braucht_genug_und_frische_messungen(profile_id):
    _gewichte(profile_id, [85.0, 83.0])                    # nur zwei Punkte
    assert _aufbau(profile_id)["breakdown"]["trend_adjust"] == 0
    _gewichte(profile_id, [85.0, 84.3, 83.6, 83.0], ende=date.today() - timedelta(days=90))
    assert _aufbau(profile_id)["breakdown"]["trend_adjust"] == 0   # zu alt


def test_trend_korrektur_ist_gedeckelt_und_gerundet(profile_id):
    _gewichte(profile_id, [90.0, 88.0, 86.0, 84.0])        # -1 kg/Woche beim Aufbau
    t = _aufbau(profile_id)["breakdown"]["trend_adjust"]
    assert t == nutrition.TREND_MAX_KCAL and t % 10 == 0


def test_trend_abnehmen_ohne_fortschritt_vergroessert_das_defizit(profile_id):
    ohne = _ziele(profile_id, goal="abnehmen")["breakdown"]["adjustment"]
    _gewichte(profile_id, [90.0, 90.1, 89.9, 90.0])        # stagniert
    b = _ziele(profile_id, goal="abnehmen")["breakdown"]
    assert b["trend_adjust"] < 0 and b["adjustment"] < ohne


def test_trend_ohne_gewichtsverlauf_ist_null(profile_id):
    assert _aufbau(profile_id)["breakdown"]["trend_adjust"] == 0
