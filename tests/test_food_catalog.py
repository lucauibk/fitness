"""Lebensmittel-Grundstock (BLS) und Suche: oesterreichische Begriffe, allgemeine Lebensmittel vor
Zubereitungen, ganze Woerter vor Zusammensetzungen – und schnell genug fuer jede Tasteneingabe."""
import time

import pytest

from app import db, food_catalog, nutrition
from app.routes import nutrition as routes


def _top(q, profile_id, n=1):
    return [f["name"] for f in routes._local(q, profile_id)[:n]]


def test_bls_ist_geladen_und_bleibt_bei_neustart_gleich():
    n = db.one("SELECT COUNT(*) AS n FROM foods WHERE source='bls'")["n"]
    assert n > 7000
    apple = db.one("SELECT id, kcal FROM foods WHERE bls_code='F110100'")
    assert apple["kcal"] == 58
    with db.connect() as c:        # neue Version: Flag weg -> erneut einlesen
        c.execute("DELETE FROM settings WHERE key=?", (food_catalog.FLAG,))
    db.init()
    assert db.one("SELECT COUNT(*) AS n FROM foods WHERE source='bls'")["n"] == n
    assert db.one("SELECT id FROM foods WHERE bls_code='F110100'")["id"] == apple["id"]


@pytest.mark.parametrize("query, expected", [
    ("Topfen", "Speisequark"), ("Erdäpfel", "Kartoffel"), ("Paradeiser", "Tomate roh"),
    ("Schlagobers", "Schlagsahne"), ("Faschiertes", "Hackfleisch"), ("Semmel", "Weizenbrötchen"),
    ("Kaiserschmarrn", "Kaiserschmarren"), ("Hühnerbrust", "Hähnchen Brustfilet, roh"),
    ("Nudeln", "Teigwaren"), ("Haferflocken", "Hafer Flocken"), ("Ei", "Hühnerei roh"),
])
def test_alltags_und_oesterreichische_begriffe(profile_id, query, expected):
    assert expected in _top(query, profile_id)[0]


@pytest.mark.parametrize("query, expected", [
    ("Apfel", "Apfel roh"), ("Banane", "Banane roh"), ("Joghurt", "Joghurt mild"),
])
def test_allgemein_vor_zubereitung(profile_id, query, expected):
    assert _top(query, profile_id)[0].startswith(expected)


def test_eis_bleibt_eis(profile_id):
    assert "Hühnerei" not in " ".join(_top("Eis", profile_id, 5))


def test_variants_in_beide_richtungen():
    assert "quark" in food_catalog.variants("Topfen")
    assert "topfen" in food_catalog.variants("Quark")
    assert "kartoffelsalat" in food_catalog.variants("Erdäpfelsalat")
    assert food_catalog.matches(food_catalog.search_text({"name": "Hafer Flocken"}), "haferflocken")


def test_produkte_aus_oesterreich_vorne():
    base = {"name": "Topfen Natur", "brand": "X", "dach": True}
    assert nutrition.score({**base, "at": True}, "topfen") > nutrition.score(base, "topfen")


def test_neue_lebensmittel_bekommen_suchform():
    food = nutrition.remember({"name": "Paradeiser-Sugo", "brand": "Spar", "kcal": 50, "source": "eigen"})
    assert food["search"] == "paradeiser sugo spar"


def test_suche_ist_schnell(profile_id):
    routes._local("Apfel", profile_id)     # Cache der Synonyme warm
    t = time.perf_counter()
    for q in ("Topfen", "Hühnerbrust", "Reis gekocht"):
        routes._local(q, profile_id)
    assert (time.perf_counter() - t) / 3 < 0.5


def test_schinken_meint_kochschinken(profile_id):
    assert "Kochschinken" in _top("Schinken", profile_id)[0]
    assert "Kochschinken" in _top("Schinken gekocht", profile_id)[0]
