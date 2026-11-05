"""Natuerliche Portionen fuer allgemeine Lebensmittel: Stueck, Scheibe, EL – Markenprodukte
behalten ihre eigene Portion aus Open Food Facts."""
import pytest

from app import db, nutrition, portions


def _bls(code):
    return db.one("SELECT * FROM foods WHERE bls_code=?", (code,))


@pytest.mark.parametrize("name, label, grams", [
    ("Apfel roh", "Stück (mittel)", 150), ("Banane roh", "Stück (mittel)", 120),
    ("Weizenbrötchen", "Stück", 60), ("Vollkornbrot", "Scheibe", 50),
    ("Hühnerei roh", "Stück (M)", 58), ("Olivenöl", "TL", 4), ("Butter gesalzen", "TL", 5),
    ("Joghurt mild, mind. 3,5 % Fett", "Becher (150 g)", 150),
    ("Bier hell", "Krügerl (0,5 l)", 500),
])
def test_regeln(name, label, grams):
    first = portions.rule_units(name)[0]
    assert (first["label"], first["g"]) == (label, grams)


@pytest.mark.parametrize("name", ["Apfelsaft", "Apfelstrudel (Mürbeteig)", "Bananenchips", "Eisbergsalat roh"])
def test_keine_falschen_stuecke(name):
    assert not any(u["label"].startswith("Stück (mittel)") for u in portions.rule_units(name))


def test_bls_lebensmittel_haben_einheiten():
    apple = _bls("F110100")
    assert portions.units(apple)[0] == {"label": "Stück (mittel)", "g": 150.0}


def test_markenprodukt_behaelt_seine_portion():
    food = {"name": "Apfel Chips", "source": "off", "serving_g": 25, "serving_label": "1 Tüte", "pack_g": 100}
    assert portions.units(food) == [{"label": "1 Tüte", "g": 25.0}, {"label": "Packung", "g": 100.0}]


def test_tagebuch_eintraege_bringen_einheiten_mit(profile_id):
    apple = _bls("F110100")
    with db.connect() as c:
        c.execute("INSERT INTO food_log (profile_id, date, meal, food_id, name, grams, kcal)"
                  " VALUES (?,?,?,?,?,?,?)", (profile_id, "2026-10-15", "snack", apple["id"], apple["name"], 300, 174))
    e = nutrition.day(profile_id, "2026-10-15")["meals"]["snack"]["entries"][0]
    assert e["units"][0]["label"] == "Stück (mittel)"
