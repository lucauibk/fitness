"""Mahlzeit erkennen: Claude (simuliert) liefert Teile und Mengen, die Naehrwerte kommen aus der
eigenen Datenbank; ohne Treffer bleibt Claudes Schaetzung als Rueckfall."""
import base64
import json
import threading

import pytest
from fastapi import HTTPException

from app import claude_cli, food_ai
from app.routes import nutrition as routes

ANSWER = {"items": [
    {"name": "Semmel", "suche": "Weizenbrötchen", "gramm": 120, "menge": "2 Stück", "kcal": 330},
    {"name": "Zaubertrank", "suche": "Zaubertrank xyz", "gramm": 200, "kcal": 90, "protein": 1},
], "hinweis": "Butter geschätzt"}


@pytest.fixture
def fake_claude(monkeypatch):
    calls = {}

    def fake(text, system, model, timeout=120, images=None):
        calls.update(text=text, images=images)
        return "Hier:\n" + json.dumps(ANSWER, ensure_ascii=False)
    monkeypatch.setattr(claude_cli, "complete", fake)
    return calls


def test_text_wird_zerlegt_und_gefunden(profile_id, fake_claude):
    out = routes.recognize(routes.AiIn(text="2 Semmeln mit Butter"), profile_id)
    semmel, unknown = out["items"]
    assert semmel["candidates"][0]["name"] == "Weizenbrötchen" and semmel["grams"] == 120
    assert semmel["kcal_found"] == 336 and semmel["candidates"][0]["units"]
    assert unknown["candidates"] == [] and unknown["estimate"]["kcal"] == 90
    assert out["hinweis"] == "Butter geschätzt" and fake_claude["images"] is None


def test_foto_geht_als_bild_an_claude(profile_id, fake_claude):
    img = base64.b64encode(b"\xff\xd8" + b"0" * 2000).decode()
    routes.recognize(routes.AiIn(image=f"data:image/jpeg;base64,{img}"), profile_id)
    assert fake_claude["images"] == [("image/jpeg", img)]


@pytest.mark.parametrize("image", ["data:image/svg+xml;base64,PHN2Zz4=", "data:image/jpeg;base64,***",
                                   "data:image/jpeg;base64,AAAA"])
def test_ungueltige_bilder(profile_id, fake_claude, image):
    with pytest.raises(HTTPException) as e:
        routes.recognize(routes.AiIn(image=image), profile_id)
    assert e.value.status_code == 400


def test_leere_anfrage(profile_id):
    with pytest.raises(HTTPException):
        routes.recognize(routes.AiIn(text="  "), profile_id)


def test_kaputte_antwort_und_limit(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "complete", lambda *a, **k: "keine Ahnung")
    with pytest.raises(HTTPException) as e:
        routes.recognize(routes.AiIn(text="Pizza"), profile_id)
    assert e.value.status_code == 502

    def limit(*a, **k):
        raise RuntimeError("usage limit reached")
    monkeypatch.setattr(claude_cli, "complete", limit)
    with pytest.raises(HTTPException) as e:
        routes.recognize(routes.AiIn(text="Pizza"), profile_id)
    assert "Nutzungslimit" in e.value.detail


def test_nur_eine_erkennung_zur_zeit(profile_id, monkeypatch):
    gate, started = threading.Event(), threading.Event()

    def slow(*a, **k):
        started.set()
        gate.wait(5)
        return json.dumps({"items": []})
    monkeypatch.setattr(claude_cli, "complete", slow)
    t = threading.Thread(target=routes.recognize, args=(routes.AiIn(text="a"), profile_id))
    t.start()
    started.wait(5)
    with pytest.raises(HTTPException) as e:
        routes.recognize(routes.AiIn(text="b"), profile_id)
    assert e.value.status_code == 429
    gate.set()
    t.join()


def test_cli_schickt_bilder_als_stream_json(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    log = tmp_path / "args"
    script.write_text(f"#!/bin/sh\necho \"$@\" > {log}\ncat > {tmp_path}/stdin\n"
                      "echo '{\"type\": \"result\", \"is_error\": false, \"result\": \"ok\"}'\n")
    script.chmod(0o755)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: str(script))
    assert claude_cli.complete("Was ist das?", "sys", "m", images=[("image/jpeg", "QUJD")]) == "ok"
    assert "--input-format stream-json" in log.read_text()
    msg = json.loads((tmp_path / "stdin").read_text())
    assert msg["message"]["content"][0]["source"]["data"] == "QUJD"
