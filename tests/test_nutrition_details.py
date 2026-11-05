"""Naehrwerte im Detail: Ballaststoffe, Zucker, gesaettigte Fette und Salz – je Eintrag eingefroren,
summiert je Tag und Mahlzeit, und ehrlich "unbekannt" (NULL), wo ein Lebensmittel sie nicht kennt."""
from app import db, food_catalog, nutrition, recipes
from app.routes import nutrition as routes

APPLE = "F110100"


def _food(code):
    return db.one("SELECT * FROM foods WHERE bls_code=?", (code,))


def _log(profile_id, food_id, grams, meal="fruehstueck", day="2026-10-16"):
    return routes.add_log(routes.LogIn(profile_id=profile_id, food_id=food_id, grams=grams, meal=meal,
                                       date=day))["entry"]


def test_bls_kennt_gesaettigte_fette_und_salz():
    butter = db.one("SELECT * FROM foods WHERE source='bls' AND name LIKE 'Butter%' AND sat_fat > 40")
    assert butter and butter["salt"] is not None
    assert _food(APPLE)["fiber"] > 2


def test_portion_detail_unbekannt_bleibt_none():
    p = nutrition.portion({"kcal": 100, "protein": 10, "fiber": 4, "salt": 1.234}, 50)
    assert p["kcal"] == 50 and p["fiber"] == 2 and p["salt"] == 0.62
    assert p["sugar"] is None and p["sat_fat"] is None and p["carbs"] == 0


def test_tag_und_mahlzeit_summen(profile_id):
    apple = _food(APPLE)
    _log(profile_id, apple["id"], 200)
    routes.add_log(routes.LogIn(profile_id=profile_id, kcal=300, protein=10, meal="mittag", date="2026-10-16"))
    d = nutrition.day(profile_id, "2026-10-16")
    assert d["totals"]["fiber"] == round(apple["fiber"] * 2, 1)
    assert d["totals"]["missing"]["fiber"] == 1          # der Schnelleintrag kennt keine Ballaststoffe
    assert d["meals"]["fruehstueck"]["totals"]["sugar"] == round(apple["sugar"] * 2, 1)
    assert d["meals"]["mittag"]["totals"]["kcal"] == 300 and d["meals"]["mittag"]["totals"]["missing"]["salt"] == 1


def test_alte_eintraege_werden_nachgetragen(profile_id):
    apple = _food(APPLE)
    with db.connect() as c:
        c.execute("INSERT INTO food_log (profile_id, date, meal, food_id, name, grams, kcal) VALUES"
                  " (?, '2026-01-01', 'snack', ?, 'Apfel', 150, 87)", (profile_id, apple["id"]))
        c.execute("DELETE FROM settings WHERE key=?", (food_catalog.LOG_FLAG,))
    db.init()
    row = db.one("SELECT * FROM food_log WHERE profile_id=? AND date='2026-01-01'", (profile_id,))
    assert row["fiber"] == round(apple["fiber"] * 1.5, 1) and row["sugar"] is not None


def test_open_food_facts_detailwerte():
    f = nutrition._from_off({"code": "9001234567890", "product_name": "Müsli", "nutriments": {
        "energy-kcal_100g": 380, "sugars_100g": 12.5, "fiber_100g": 8, "saturated-fat_100g": 1.2,
        "salt_100g": 0.05}})
    assert (f["sugar"], f["fiber"], f["sat_fat"], f["salt"]) == (12.5, 8, 1.2, 0.05)


def test_bekanntes_produkt_bekommt_fehlende_werte():
    code = "9009876543210"
    nutrition.remember({"barcode": code, "name": "Riegel", "kcal": 400, "source": "off"})
    food = nutrition.remember({"barcode": code, "name": "Riegel", "kcal": 400, "salt": 0.3, "source": "off"})
    assert food["salt"] == 0.3 and food["kcal"] == 400


def test_richtwerte():
    r = nutrition.reference(2000)
    assert r["fiber"] == {"value": 30, "kind": "min"} and r["salt"]["kind"] == "max"
    assert r["sugar"]["value"] == 50 and r["sat_fat"]["value"] == 22
    assert "sugar" not in nutrition.reference(None)


def test_rezept_mit_unbekanntem_detailwert():
    own = nutrition.remember({"name": f"Sauce-{id(object())}", "kcal": 100, "fiber": 1, "source": "eigen"})
    out = recipes.compute([{"food_id": _food(APPLE)["id"], "grams": 100}, {"food_id": own["id"], "grams": 100}], 1)
    assert out["salt"] is None and out["fiber"] == round((_food(APPLE)["fiber"] + 1) / 2, 1)
