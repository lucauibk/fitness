"""Gemerkte Satzpause je Uebung (static/js/pause.js): ±15 s in der Pausenleiste gilt
kuenftig fuer diese Uebung, der Plan bleibt unveraendert. Laeuft unter Node; ohne Node
wird der Test uebersprungen."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

RUN = """
import * as p from "./pause.js";
const [kind, ...args] = JSON.parse(process.argv[2]);
console.log(JSON.stringify(p[kind](...args) ?? null));
"""

BENCH = {"exercise_id": 7, "kind": "compound", "rest": 150}
CURL = {"exercise_id": 9, "kind": "isolation", "rest": None}
SQUAT = {"exercise_id": 3, "kind": "compound"}


@pytest.fixture
def call(tmp_path):
    shutil.copy(ROOT / "static" / "js" / "pause.js", tmp_path / "pause.js")
    (tmp_path / "run.mjs").write_text(RUN)

    def run(fn, *args):
        res = subprocess.run([NODE, str(tmp_path / "run.mjs"), json.dumps([fn, *args])],
                             capture_output=True, text=True, cwd=tmp_path, timeout=30)
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)
    return run


pytestmark = pytest.mark.skipif(not NODE, reason="Node nicht installiert")


def test_ohne_gemerkte_pause_gilt_plan_sonst_standard(call):
    assert call("restFor", BENCH, {}) == 150          # Planwert
    assert call("restFor", CURL, None) == 75          # Isolation ohne Planwert
    assert call("restFor", SQUAT, {}) == 120          # Grunduebung ohne Planwert


def test_plus_minus_wird_je_uebung_gemerkt(call):
    out = call("rememberRest", {}, BENCH, 15)
    assert out == {"saved": {"7": 165}, "seconds": 165}
    # Gilt fuer die naechste Pause dieser Uebung, andere Uebungen bleiben beim Plan
    assert call("restFor", BENCH, out["saved"]) == 165
    assert call("restFor", CURL, out["saved"]) == 75
    assert call("restNote", {**BENCH, "name": "Bankdrücken"}, 165) == "Pause bei Bankdrücken ab jetzt 2:45."
    # Zweimal −15 s: 135 s
    two = call("rememberRest", call("rememberRest", out["saved"], BENCH, -15)["saved"], BENCH, -15)
    assert two == {"saved": {"7": 135}, "seconds": 135}


def test_zurueck_auf_planwert_loescht_eintrag(call):
    saved = {"7": 165, "9": 90}
    out = call("rememberRest", saved, BENCH, -15)
    assert out == {"saved": {"9": 90}, "seconds": 150}   # andere Uebung bleibt gemerkt
    assert call("restNote", {**BENCH, "name": "Bankdrücken"}, 150) == \
        "Pause bei Bankdrücken wieder wie im Plan (2:30)."


def test_grenzen_wie_im_plan(call):
    assert call("rememberRest", {"9": 30}, CURL, -15)["seconds"] == 30
    assert call("rememberRest", {"9": 300}, CURL, 15)["seconds"] == 300
    # Ein kaputter Wert im Geraetespeicher wird begrenzt statt uebernommen
    assert call("restFor", CURL, {"9": 9999}) == 300


def test_ohne_uebung_wird_nichts_gemerkt(call):
    assert call("rememberRest", {"7": 165}, None, 15) == {"saved": {"7": 165}, "seconds": None}
    assert call("rememberRest", {}, {"kind": "compound"}, 15) == {"saved": {}, "seconds": None}
    assert call("restNote", None, 90) == ""
