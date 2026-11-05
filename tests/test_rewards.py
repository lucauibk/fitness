"""Shopping-Topf: Geld gibt es pro Einheit mit echten Saetzen, Betragsaenderungen gelten
nicht rueckwirkend, und gekauft wird nur, was im Topf auch drin ist."""
import os

import pytest
from fastapi import HTTPException

from app import db, rewards
from app.routes import rewards as routes

JPEG = "data:image/jpeg;base64," + "A" * 40


def _exercise():
    with db.connect() as c:
        return c.execute("INSERT INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                         (f"Test-{os.urandom(4).hex()}", "beine", "kg")).lastrowid


def _workout(profile_id, sets=((10, 0),)):
    ex = _exercise()
    db.save_workout({"profile_id": profile_id, "sets": [
        {"exercise_id": ex, "set_no": i + 1, "reps": reps, "weight": 40.0, "warmup": warm}
        for i, (reps, warm) in enumerate(sets)]})


def _item(profile_id, name="Jeans", price=20.0, **extra):
    return routes.add_item(routes.ItemIn(name=name, price=price, **extra), profile_id)


def test_topf_ist_aus_bis_ein_betrag_steht(profile_id):
    _workout(profile_id)
    assert rewards.card(profile_id) is None
    assert rewards.status(profile_id)["enabled"] is False


def test_nur_einheiten_ab_dem_einschalten_zaehlen(profile_id):
    _workout(profile_id)                      # vor dem Einschalten
    rewards.set_rate(profile_id, 5)
    _workout(profile_id)
    _workout(profile_id)
    s = rewards.status(profile_id)
    assert s["balance"] == 10
    assert s["earned_today"] == 10


def test_leere_oder_nur_aufwaermsaetze_bringen_nichts(profile_id):
    rewards.set_rate(profile_id, 5)
    _workout(profile_id, sets=((0, 0),))
    _workout(profile_id, sets=((10, 1),))
    assert rewards.status(profile_id)["balance"] == 0


def test_betragsaenderung_gilt_nicht_rueckwirkend(profile_id):
    rewards.set_rate(profile_id, 5)
    _workout(profile_id)
    _workout(profile_id)
    rewards.set_rate(profile_id, 2)
    _workout(profile_id)
    assert rewards.status(profile_id)["balance"] == 12
    rewards.set_rate(profile_id, None)        # ausschalten behaelt das Verdiente
    assert rewards.status(profile_id)["balance"] == 12


def test_ziel_zeigt_fehlende_trainings(profile_id):
    rewards.set_rate(profile_id, 5)
    _workout(profile_id)
    s = _item(profile_id, price=22)
    goal = s["goal"]
    assert goal["missing"] == 17 and goal["workouts_left"] == 4 and goal["affordable"] is False


def test_kaufen_nur_mit_genug_im_topf_und_rueckgaengig(profile_id):
    rewards.set_rate(profile_id, 10)
    item_id = _item(profile_id, price=20)["goal"]["id"]
    _workout(profile_id)
    with pytest.raises(HTTPException) as e:
        routes.buy_item(item_id, profile_id)
    assert e.value.status_code == 400 and "10,00 €" in e.value.detail
    _workout(profile_id)
    s = routes.buy_item(item_id, profile_id)
    assert s["balance"] == 0 and s["goal"] is None and s["bought"][0]["id"] == item_id
    s = routes.unbuy_item(item_id, profile_id)
    assert s["balance"] == 20 and s["goal"]["affordable"] is True


def test_fremdes_profil_darf_nicht_kaufen(profile_id):
    item_id = _item(profile_id)["goal"]["id"]
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                          (f"Test-{os.urandom(4).hex()}", db.now())).lastrowid
    with pytest.raises(HTTPException) as e:
        routes.buy_item(item_id, other)
    assert e.value.status_code == 403


@pytest.mark.parametrize("link", ["javascript:alert(1)", "data:text/html,x", "www.zara.com"])
def test_nur_weblinks(profile_id, link):
    with pytest.raises(HTTPException):
        _item(profile_id, link=link)


def test_foto_nur_als_jpeg_data_url(profile_id):
    assert _item(profile_id, image=JPEG)["goal"]["image"] == JPEG
    with pytest.raises(HTTPException):
        _item(profile_id, image="data:image/svg+xml;base64,PHN2Zz4=")
    with pytest.raises(HTTPException):
        _item(profile_id, image="data:image/jpeg;base64,***")


def test_reihenfolge_bestimmt_das_ziel(profile_id):
    _item(profile_id, name="Jeans")
    s = _item(profile_id, name="Kleid")
    kleid = next(i for i in s["open"] if i["name"] == "Kleid")
    s = routes.update_item(kleid["id"], routes.ItemIn(position=0), profile_id)
    jeans = next(i for i in s["open"] if i["name"] == "Jeans")
    s = routes.update_item(jeans["id"], routes.ItemIn(position=1), profile_id)
    assert s["goal"]["name"] == "Kleid"
