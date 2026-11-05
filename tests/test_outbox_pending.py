"""Ausgang (static/js/outbox.js): wartende Eintraege gehoeren zu dem Profil, mit dem sie
angelegt wurden. Laeuft outbox.js unter Node mit einer kleinen util.js-Attrappe; ohne Node
wird der Test uebersprungen."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

UTIL_STUB = """
const store = {};
let profileId = null;
export const setProfile = (id) => { profileId = id ? Number(id) : null; };
export const getProfile = () => profileId;
export const prefs = {
  get(key, fallback = null) { return key in store ? JSON.parse(store[key]) : fallback; },
  set(key, value) { store[key] = JSON.stringify(value); return true; },
};
export const toast = () => {};
export async function api() { const e = new Error("offline"); e.offline = true; throw e; }
"""

RUN = """
globalThis.window = { dispatchEvent() {}, addEventListener() {} };
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o?.detail; } };
const util = await import("./util.js");
const outbox = await import("./outbox.js");
util.prefs.set("outbox", [
  { path: "food-log", body: { profile_id: 1, client_id: "a", preview: { kcal: 500 } } },
  { path: "food-log", body: { profile_id: 2, client_id: "b", preview: { kcal: 300 } } },
  { path: "workouts", body: { profile_id: 2, client_id: "c", sets: [] } },
]);
util.setProfile(1);
const one = { food: outbox.pending("food-log").map((e) => e.client_id),
              workouts: outbox.pendingWorkouts().map((e) => e.client_id) };
util.setProfile(2);
const two = { food: outbox.pending("food-log").map((e) => e.client_id),
              workouts: outbox.pendingWorkouts().map((e) => e.client_id) };
console.log(JSON.stringify({ one, two }));
"""


@pytest.mark.skipif(not NODE, reason="Node nicht installiert")
def test_pending_nur_fuers_aktive_profil(tmp_path):
    shutil.copy(ROOT / "static" / "js" / "outbox.js", tmp_path / "outbox.js")
    (tmp_path / "util.js").write_text(UTIL_STUB)
    (tmp_path / "run.mjs").write_text(RUN)
    res = subprocess.run([NODE, str(tmp_path / "run.mjs")], capture_output=True, text=True,
                         cwd=tmp_path, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["one"] == {"food": ["a"], "workouts": []}
    assert out["two"] == {"food": ["b"], "workouts": ["c"]}
