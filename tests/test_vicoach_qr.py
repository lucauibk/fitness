"""Studio-QR (mescan/ViCoach): der QR-Code liefert eine measurementId, die als client_id
"vicoach:<id>" denselben Import nur einmal zulaesst. Der Link-Parser (static/js/vicoach.js)
laeuft unter Node; ohne Node wird dieser Teil uebersprungen."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.routes.body import MeasurementIn, add_measurement

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
MID = "6b02d23faaff127e75349901"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJfaWQiOiJ4In0.sig"


def _add(profile_id, client_id, source="vicoach", kg=80.0):
    return add_measurement(MeasurementIn(profile_id=profile_id, date="2026-11-22", source=source,
                                         client_id=client_id, weight_kg=kg))


def _mid():
    """Eigene measurementId je Test (die Test-Datenbank ist fuer alle Tests dieselbe)."""
    return os.urandom(12).hex()


def test_gleiche_studio_messung_nur_einmal(profile_id):
    mid = _mid()
    first = _add(profile_id, f"vicoach:{mid}")
    again = _add(profile_id, f"vicoach:{mid}", kg=81.0)
    assert first["created"] and not again["created"]
    assert again["measurement"]["id"] == first["measurement"]["id"]
    assert db.one("SELECT COUNT(*) AS n FROM body_measurements WHERE profile_id=?", (profile_id,))["n"] == 1


@pytest.mark.parametrize("cid,source", [
    ("vicoach:123", "vicoach"),                 # keine 24 Hex-Zeichen
    (f"vicoach:{MID.upper()}", "vicoach"),       # Frontend schreibt klein
    (f"vicoach:{MID}", "manuell"),               # Studio-Kennung nur mit Quelle ViCoach
])
def test_ungueltige_studio_kennung(profile_id, cid, source):
    with pytest.raises(HTTPException) as e:
        _add(profile_id, cid, source=source)
    assert e.value.status_code == 400


def test_studio_messung_eines_anderen_profils(profile_id):
    mid = _mid()
    _add(profile_id, f"vicoach:{mid}")
    other = db.insert_row("profiles", {"name": "Zweit", "created_at": db.now()})["id"]
    with pytest.raises(HTTPException) as e:
        _add(other, f"vicoach:{mid}")
    assert e.value.status_code == 409


CASES = {
    "ok": f"https://vicoach.io/assign-measurement/mescan?scopeToken={TOKEN}&measurementId={MID}",
    "upper": f"https://vicoach.io/assign-measurement/mescan?scopeToken={TOKEN}&measurementId={MID.upper()}",
    "space": f"  https://vicoach.io/assign-measurement/mescan?measurementId={MID}\n",
    "http": f"http://vicoach.io/assign-measurement/mescan?measurementId={MID}",
    "fremd": f"https://vicoach.io.evil.example/assign-measurement/mescan?measurementId={MID}",
    "pfad": f"https://vicoach.io/login?measurementId={MID}",
    "kurz": "https://vicoach.io/assign-measurement/mescan?measurementId=abc",
    "ean": "4006381333931",
    "leer": "",
}


@pytest.mark.skipif(not NODE, reason="Node nicht installiert")
def test_link_parser():
    script = f"""
const m = await import("./vicoach.js");
const cases = {json.dumps(CASES)};
const out = {{}};
for (const [k, v] of Object.entries(cases)) out[k] = m.parseVicoachLink(v)?.measurementId ?? null;
out.cid = m.vicoachClientId("{MID.upper()}");
console.log(JSON.stringify(out));
"""
    res = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=ROOT / "static" / "js",
                         capture_output=True, text=True, check=True)
    out = json.loads(res.stdout)
    assert out["ok"] == out["upper"] == out["space"] == MID
    assert all(out[k] is None for k in ("http", "fremd", "pfad", "kurz", "ean", "leer"))
    assert out["cid"] == f"vicoach:{MID}"
