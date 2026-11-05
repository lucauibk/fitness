"""Rezepte: Werte je 100 g aus den Zutaten, Portion = Gesamtgewicht / Portionen, optional das
Gewicht nach dem Kochen. Danach verhaelt sich ein Rezept wie jedes Lebensmittel."""
import pytest
from fastapi import HTTPException

from app import db, portions
from app.routes import nutrition as routes


def _food(code):
    return db.one("SELECT * FROM foods WHERE bls_code=?", (code,))["id"]


APPLE, BANANA = "F110100", "F503100"


def _body(**kw):
    base = {"name": "Obstsalat", "portions": 2,
            "items": [{"food_id": _food(APPLE), "grams": 300}, {"food_id": _food(BANANA), "grams": 100}]}
    return routes.RecipeIn(**{**base, **kw})


def test_werte_und_portion():
    r = routes.add_recipe(_body())
    # 300 g Apfel (58 kcal/100 g) + 100 g Banane (79) = 253 kcal auf 400 g
    assert r["kcal"] == round(253 * 100 / 400, 1) and r["serving_g"] == 200
    assert r["recipe"]["total_kcal"] == 253 and len(r["recipe"]["items"]) == 2
    assert portions.units(r)[0] == {"label": "Portion", "g": 200.0}


def test_gewicht_nach_dem_kochen():
    r = routes.add_recipe(_body(cooked_g=320))
    assert r["kcal"] == round(253 * 100 / 320, 1) and r["serving_g"] == 160


def test_rezept_ist_suchbar_und_eintragbar(profile_id):
    r = routes.add_recipe(_body(name="Tinas Marillenknödel-Test"))
    assert routes._local("Marillenknödel-Test", profile_id)[0]["id"] == r["id"]
    e = routes.add_log(routes.LogIn(profile_id=profile_id, food_id=r["id"], grams=200, meal="snack"))["entry"]
    assert abs(e["kcal"] - 253 / 2) < 0.5      # je 100 g auf 0,1 gerundet


def test_bearbeiten_und_schutz():
    r = routes.add_recipe(_body())
    r2 = routes.update_recipe(r["id"], _body(portions=4))
    assert r2["id"] == r["id"] and r2["serving_g"] == 100
    with pytest.raises(HTTPException):        # sich selbst als Zutat
        routes.update_recipe(r["id"], _body(items=[{"food_id": r["id"], "grams": 100}]))
    with pytest.raises(HTTPException):        # BLS-Lebensmittel sind kein Rezept
        routes.get_recipe(_food(APPLE))
    with pytest.raises(HTTPException):        # unmoegliche Werte
        routes.add_recipe(_body(cooked_g=1))
    routes.delete_food(r["id"])
    assert db.get_row("foods", r["id"]) is None
