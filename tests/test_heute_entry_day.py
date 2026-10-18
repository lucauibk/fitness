"""„Heute“ (static/js/heute.js): Check-in, Wasser und Mobility bekommen das Datum des Tages, an dem
sie eingetragen werden. Offline liefert der Service Worker /api/today aus dem Cache – mit dem Datum
des Tages, an dem zuletzt geladen wurde. Dieses Datum darf nicht mitgeschickt werden, sonst
ueberschreibt der neue Check-in den von gestern. Laeuft util.js unter Node; ohne Node wird
der Laufzeit-Teil uebersprungen."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

RUN = """
globalThis.window = { dispatchEvent() {}, addEventListener() {} };
globalThis.document = { addEventListener() {} };
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o?.detail; } };
const { entryDay } = await import("./util.js");
const now = new Date(2026, 9, 18, 7, 30);
console.log(JSON.stringify({
  fresh: entryDay("2026-10-18", now),
  cached: entryDay("2026-10-15", now),
  missing: entryDay(undefined, now),
  ahead: entryDay("2026-10-19", now),
}));
"""


@pytest.mark.skipif(not NODE, reason="Node nicht installiert")
def test_entry_day_nimmt_nie_einen_vergangenen_tag(tmp_path):
    shutil.copy(ROOT / "static" / "js" / "util.js", tmp_path / "util.js")
    (tmp_path / "run.mjs").write_text(RUN)
    res = subprocess.run([NODE, str(tmp_path / "run.mjs")], capture_output=True, text=True,
                         cwd=tmp_path, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out == {"fresh": "2026-10-18", "cached": "2026-10-18", "missing": "2026-10-18",
                   "ahead": "2026-10-19"}   # Server-Zeitzone schon weiter: dessen Tag bleibt


def test_heute_schickt_nicht_das_datum_aus_dem_cache():
    src = (ROOT / "static" / "js" / "heute.js").read_text()
    assert not re.search(r"date:\s*data\??\.date", src), "Eintrag nimmt das Datum der /today-Antwort ungeprüft"
    assert src.count("date: entryDay(data?.date)") == 3     # Check-in, Mobility, Wasser
