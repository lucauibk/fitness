"""Favoriten (je Profil, weiter oben in der Suche) und Schnelleintraege (nur Kalorien)."""
import os

import pytest
from fastapi import HTTPException

from app import db, nutrition
from app.routes import nutrition as routes


def _other_profile():
    with db.connect() as c:
        return c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                         (f"P-{os.urandom(3).hex()}", db.now())).lastrowid


def _apple():
    return db.one("SELECT * FROM foods WHERE bls_code='F110100'")


def test_favorit_je_profil_und_oben_in_der_suche(profile_id):
    plain = routes._local("Apfel", profile_id)
    target = next(f for f in plain if f["name"] != plain[0]["name"])
    routes.add_favorite(target["id"], profile_id)
    favs = routes.favorites(profile_id)
    assert [f["id"] for f in favs] == [target["id"]] and favs[0]["favorite"] and favs[0]["units"] is not None
    ranked = routes._local("Apfel", profile_id)
    assert ranked.index(next(f for f in ranked if f["id"] == target["id"])) < plain.index(target)
    assert routes.favorites(_other_profile()) == []
    routes.remove_favorite(target["id"], profile_id)
    assert routes.favorites(profile_id) == []


def test_schnelleintrag(profile_id):
    out = routes.add_log(routes.LogIn(profile_id=profile_id, kcal=850.4, carbs=90, meal="abend",
                                      name="  Pizza  ", date="2026-10-15"))
    e = out["entry"]
    assert (e["food_id"], e["grams"], e["kcal"], e["name"]) == (None, 0, 850, "Pizza")
    assert nutrition.day(profile_id, "2026-10-15")["totals"]["kcal"] == 850
    routes.patch_log(e["id"], routes.LogPatch(kcal=900, name=""), profile_id)
    e = db.get_row("food_log", e["id"])
    assert (e["kcal"], e["name"]) == (900, "Schnelleintrag")
    with pytest.raises(HTTPException):
        routes.patch_log(e["id"], routes.LogPatch(grams=100), profile_id)


def test_schnelleintrag_braucht_kalorien(profile_id):
    with pytest.raises(HTTPException):
        routes.add_log(routes.LogIn(profile_id=profile_id, meal="snack"))


def test_normaler_eintrag_bleibt_bei_gramm(profile_id):
    e = routes.add_log(routes.LogIn(profile_id=profile_id, food_id=_apple()["id"], grams=150, meal="snack"))["entry"]
    assert e["kcal"] == 87
    with pytest.raises(HTTPException):
        routes.patch_log(e["id"], routes.LogPatch(kcal=10), profile_id)
    with pytest.raises(HTTPException):
        routes.add_log(routes.LogIn(profile_id=profile_id, food_id=_apple()["id"], meal="snack"))
