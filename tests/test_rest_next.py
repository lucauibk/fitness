"""Pausenleiste (static/js/naechster.js): zeigt, welcher Satz nach der Pause kommt.
Laeuft unter Node; ohne Node wird der Test uebersprungen."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def _s(done=False, reps=8, weight=60, warmup=False):
    return {"done": done, "reps": reps, "weight": weight, "warmup": warmup}


DRAFT = {"exercises": [
    {"name": "Bankdrücken", "unit": "kg",
     "sets": [_s(True, 10, 40, warmup=True), _s(True), _s(False, 8, 62.5), _s()]},
    {"name": "Klimmzüge", "unit": "koerper", "sets": [_s(reps=6, weight=0), _s(reps=6, weight=5)]},
    {"name": "Seitheben", "unit": "kg", "sets": [_s(reps=None, weight=8)]},
    {"name": "Plank", "unit": "kg", "sets": [_s(True)]},
]}

RUN = """
import { nextSet, nextLabel } from "./naechster.js";
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([draft, from]) => { const n = nextSet(draft, from); return n && { ...n, label: nextLabel(n) }; });
console.log(JSON.stringify(out));
"""


def _run(tmp_path, cases):
    shutil.copy(ROOT / "static" / "js" / "naechster.js", tmp_path / "naechster.js")
    (tmp_path / "run.mjs").write_text(RUN)
    res = subprocess.run([NODE, str(tmp_path / "run.mjs"), json.dumps(cases)], capture_output=True,
                         text=True, cwd=tmp_path, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _done(draft, ex_index, set_index):
    d = json.loads(json.dumps(draft))
    d["exercises"][ex_index]["sets"][set_index]["done"] = True
    return d


@pytest.mark.skipif(not NODE, reason="Node nicht installiert")
def test_naechster_satz_in_der_pause(tmp_path):
    # Zweite Klimmzug-Serie fertig: weiter mit Seitheben (Klimmzüge komplett)
    pulls_done = _done(_done(DRAFT, 1, 0), 1, 1)
    # Nur die Plank-Übung ist offen – von der letzten Übung aus geht es vorne weiter
    only_bench_done = {"exercises": [{**DRAFT["exercises"][0], "sets": [_s(True)]},
                                     {"name": "Plank", "unit": "kg", "sets": [_s(False, 1, None)]}]}
    all_done = {"exercises": [{"name": "X", "unit": "kg", "sets": [_s(True)]}]}
    same, bw, wrap_iso, wrap, none, empty, legacy, bw_extra = _run(tmp_path, [
        [DRAFT, 0], [DRAFT, 1], [pulls_done, 1], [DRAFT, 3], [all_done, 0], [{}, 0], [DRAFT, None],
        [_done(DRAFT, 1, 0), 1]])
    # Gleiche Übung, Aufwärmsatz zählt nicht mit: der dritte Eintrag ist Satz 2
    assert same["label"] == "Bankdrücken · Satz 2 · 8 × 62,5 kg"
    assert same["sameExercise"] is True and (same["exIndex"], same["setIndex"]) == (0, 2)
    # Körpergewicht ohne Zusatzlast: nur Wiederholungen; mit Zusatzlast "+5 kg"
    assert bw["label"] == "Klimmzüge · Satz 1 · 6 Whd."
    assert bw_extra["label"] == "Klimmzüge · Satz 2 · 6 × +5 kg"
    # Übung fertig → nächste offene Übung; ohne Wiederholungen nur das Gewicht
    assert wrap_iso["label"] == "Seitheben · Satz 1 · 8 kg" and wrap_iso["sameExercise"] is False
    # Von der letzten Übung (fertig) aus geht es vorne weiter – wie advanceFocus
    assert wrap["label"] == "Bankdrücken · Satz 2 · 8 × 62,5 kg"
    # Alles erledigt oder kein Entwurf: nichts anzeigen; alte Pause ohne "from" beginnt vorne
    assert none is None and empty is None
    assert legacy["exIndex"] == 0
    assert _run(tmp_path, [[only_bench_done, 0]])[0]["label"] == "Plank · Satz 1 · 1 Whd."
