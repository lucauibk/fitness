"""Intervallfasten: Dauer und Ziel, Mitternacht, Zeitumstellung, Nachsenden (client_id),
ein laufendes Fasten je Profil, Profil-Trennung, Serie, Migration, Coach ohne Fastendaten."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import coach, db, fasting
from app.routes import nutrition as routes


def _on(profile_id, kind="16:8", hours=None):
    db.update_row("profiles", profile_id, {"fasting_kind": kind, "fasting_hours": hours})


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _ago(**kw):
    return _iso(datetime.now(timezone.utc) - timedelta(**kw))


# client_id ist global eindeutig und die Test-DB wird geteilt: je Profil eine eigene
def _start(profile_id, cid="a", started_at=None):
    return routes.start_fast(routes.FastStart(
        profile_id=profile_id, client_id=f"{cid}-{profile_id}", started_at=started_at))


def _stop(profile_id, cid="a", ended_at=None):
    return routes.stop_fast(routes.FastStop(
        profile_id=profile_id, client_id=f"{cid}-{profile_id}", ended_at=ended_at))


def _raw(profile_id, start, end, target=16, cid=None):
    return db.insert_row("fasts", {"profile_id": profile_id, "started_at": start, "ended_at": end,
                                   "target_hours": target, "kind": "16:8", "client_id": cid})


# ---------------------------------------------------------------- Dauer und Ziel
def test_start_stop_dauer_und_ziel_erreicht(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=17))
    out = _stop(profile_id)
    assert out["fast"]["ended_at"]
    entry = fasting.history(profile_id)[0]
    assert entry["hours"] == pytest.approx(17, abs=0.05)
    assert entry["target_hours"] == 16 and entry["hit"] is True


def test_ziel_nicht_erreicht(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=10))
    _stop(profile_id)
    assert fasting.history(profile_id)[0]["hit"] is False


def test_eigene_stundenzahl_ist_das_ziel(profile_id):
    _on(profile_id, "custom", 23)
    f = _start(profile_id)["fast"]
    assert f["target_hours"] == 23 and f["kind"] == "custom"


def test_ziel_wird_beim_start_eingefroren(profile_id):
    _on(profile_id, "16:8")
    _start(profile_id, started_at=_ago(hours=1))
    _on(profile_id, "18:6")
    assert fasting.running(profile_id)["target_hours"] == 16


# --------------------------------------------------- Mitternacht und Zeitumstellung
def test_fasten_ueber_mitternacht_zaehlt_fuer_den_endtag(profile_id):
    # 20:00 → 12:30 Wien (MEZ, Winter) = 19:00 → 11:30 UTC
    _raw(profile_id, "2026-01-14T19:00:00+00:00", "2026-01-15T11:30:00+00:00")
    e = fasting.history(profile_id)[0]
    assert e["day"] == "2026-01-15" and e["hours"] == 16.5


def test_ende_kurz_nach_lokaler_mitternacht_zaehlt_fuer_den_neuen_tag(profile_id):
    # 23:30 UTC am 14.01. = 00:30 Wien am 15.01.
    _raw(profile_id, "2026-01-14T07:00:00+00:00", "2026-01-14T23:30:00+00:00")
    assert fasting.history(profile_id)[0]["day"] == "2026-01-15"


def test_dauer_ueber_zeitumstellung_stimmt(profile_id):
    # Nacht zum 29.03.2026 (MEZ→MESZ): 20:00 Wien bis 12:00 Wien sind nur 15 h
    _raw(profile_id, "2026-03-28T19:00:00+00:00", "2026-03-29T10:00:00+00:00")
    e = fasting.history(profile_id)[0]
    assert e["hours"] == 15 and e["hit"] is False and e["day"] == "2026-03-29"
    # Nacht zum 25.10.2026 (MESZ→MEZ): dieselbe Wanduhr-Spanne sind 17 h
    _raw(profile_id, "2026-10-24T18:00:00+00:00", "2026-10-25T11:00:00+00:00")
    assert fasting.history(profile_id)[0]["hours"] == 17


# --------------------------------------------------------- Nachsenden und Konflikte
def test_start_doppelt_nachgesendet_eine_zeile(profile_id):
    _on(profile_id)
    first = _start(profile_id, "dup")
    second = _start(profile_id, "dup")
    assert first["created"] is True and second["created"] is False
    assert db.one("SELECT COUNT(*) AS n FROM fasts WHERE profile_id=?", (profile_id,))["n"] == 1


def test_stop_doppelt_nachgesendet_aendert_nichts(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=17))
    first = _stop(profile_id)["fast"]
    later = _stop(profile_id, ended_at=_iso(datetime.now(timezone.utc)))
    assert later["created"] is False and later["fast"]["ended_at"] == first["ended_at"]


def test_zweiter_start_bei_laufendem_fasten_409(profile_id):
    _on(profile_id)
    _start(profile_id, "eins")
    with pytest.raises(HTTPException) as e:
        _start(profile_id, "zwei")
    assert e.value.status_code == 409


def test_unique_index_haelt_auch_direkt_in_der_db(profile_id):
    _on(profile_id)
    _start(profile_id, "eins")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_row("fasts", {"profile_id": profile_id, "started_at": _ago(hours=1),
                                "target_hours": 16, "kind": "16:8", "client_id": f"x-{profile_id}"})


def test_start_ohne_eingeschaltetes_fasten_400(profile_id):
    with pytest.raises(HTTPException) as e:
        _start(profile_id)
    assert e.value.status_code == 400


@pytest.mark.parametrize("when", [
    lambda: _iso(datetime.now(timezone.utc) + timedelta(hours=2)),   # Zukunft
    lambda: _ago(hours=80),                                          # zu alt
    lambda: "gestern",                                               # Unsinn
])
def test_start_mit_unplausibler_zeit_400(profile_id, when):
    _on(profile_id)
    with pytest.raises(HTTPException) as e:
        _start(profile_id, started_at=when())
    assert e.value.status_code == 400


def test_stop_vor_dem_start_400_und_ohne_start_404(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=5))
    with pytest.raises(HTTPException) as e:
        _stop(profile_id, ended_at=_ago(hours=6))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _stop(profile_id, "gibt-es-nicht")
    assert e.value.status_code == 404


# ---------------------------------------------------------------- Profil-Trennung
def test_fremdes_fasten_ist_gesperrt(profile_id):
    _on(profile_id)
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?, ?)",
                          (f"Anderer-{profile_id}", db.now())).lastrowid
    _on(other)
    mine = _start(profile_id, "mein")["fast"]
    with pytest.raises(HTTPException) as e:
        routes.patch_fast(mine["id"], routes.FastPatch(started_at=_ago(hours=2)), profile_id=other)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        routes.delete_fast(mine["id"], profile_id=other)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:       # fremde client_id
        routes.stop_fast(routes.FastStop(profile_id=other, client_id=mine["client_id"]))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:       # fremde client_id beim Nachsenden
        routes.start_fast(routes.FastStart(profile_id=other, client_id=mine["client_id"]))
    assert e.value.status_code == 403
    assert fasting.running(other) is None


def test_korrigieren_und_loeschen(profile_id):
    _on(profile_id)
    f = _start(profile_id, started_at=_ago(hours=3))["fast"]
    out = routes.patch_fast(f["id"], routes.FastPatch(started_at=_ago(hours=18)), profile_id=profile_id)
    assert fasting.hours_between(out["fast"]["started_at"], _iso(datetime.now(timezone.utc))) == pytest.approx(18, abs=0.05)
    routes.delete_fast(f["id"], profile_id=profile_id)
    assert fasting.running(profile_id) is None


# ------------------------------------------------------------------------- Serie
def _day(d, hit=True):
    return {"day": d.isoformat(), "hit": hit}


def test_serie_zaehlt_aufeinanderfolgende_tage():
    today = date(2026, 5, 10)
    e = [_day(today - timedelta(days=i)) for i in range(3)]
    assert fasting.streak(e, today) == 3


def test_serie_bricht_bei_luecke_und_bei_verfehltem_ziel():
    today = date(2026, 5, 10)
    assert fasting.streak([_day(today), _day(today - timedelta(days=2))], today) == 1
    assert fasting.streak([_day(today, hit=False), _day(today - timedelta(days=1))], today) == 1


def test_serie_laufender_tag_ohne_treffer_bricht_nicht():
    today = date(2026, 5, 10)
    e = [_day(today - timedelta(days=1)), _day(today - timedelta(days=2))]
    assert fasting.streak(e, today) == 2


def test_zwei_fasten_am_selben_tag_zaehlen_einmal():
    today = date(2026, 5, 10)
    assert fasting.streak([_day(today), _day(today), _day(today - timedelta(days=1))], today) == 2


# ----------------------------------------------------------- Uebersicht und Profil
def test_get_ohne_fasten_liefert_kind_none(profile_id):
    out = routes.get_fasting(profile_id=profile_id)
    assert out["kind"] is None and out["running"] is None and out["history"] == []


def test_hinweis_wenn_im_fasten_trainiert_wurde(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=6))
    assert routes.get_fasting(profile_id=profile_id)["hint"] is None
    ex = db.one("SELECT id FROM exercises ORDER BY id LIMIT 1")
    db.save_workout({"profile_id": profile_id, "started_at": _ago(hours=1), "finished_at": _ago(minutes=10),
                     "sets": [{"exercise_id": ex["id"], "set_no": 1, "reps": 8, "weight": 50}]})
    assert "Eiweiß" in routes.get_fasting(profile_id=profile_id)["hint"]


def test_profil_eigene_fastenart_braucht_stunden(profile_id):
    from app import main
    with pytest.raises(HTTPException) as e:
        main.api_update_profile(profile_id, main.ProfileIn(fasting_kind="custom"))
    assert e.value.status_code == 400
    out = main.api_update_profile(profile_id, main.ProfileIn(fasting_kind="custom", fasting_hours=20))
    assert out["fasting_kind"] == "custom" and out["fasting_hours"] == 20
    off = main.api_update_profile(profile_id, main.ProfileIn(fasting_kind=None))
    assert off["fasting_kind"] is None


def test_profil_speichern_ohne_fastenfelder_aendert_sie_nicht(profile_id):
    from app import main
    _on(profile_id, "18:6")
    main.api_update_profile(profile_id, main.ProfileIn(name=f"Neu-{profile_id}"))
    assert db.get_row("profiles", profile_id)["fasting_kind"] == "18:6"


# ----------------------------------------------------------- Migration und Coach
def test_migration_ergaenzt_spalten_in_alter_db(tmp_path, monkeypatch):
    old = tmp_path / "alt.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,"
              " color TEXT, created_at TEXT)")
    c.execute("INSERT INTO profiles (name, created_at) VALUES ('Alt', 'x')")
    c.commit()
    c.close()
    monkeypatch.setattr(db, "DB_PATH", old)
    db.init()
    cols = {r["name"] for r in db.rows("PRAGMA table_info(profiles)")}
    assert {"fasting_kind", "fasting_hours", "fasting_flags", "eating_window_pref"} <= cols
    assert db.one("SELECT name, fasting_kind FROM profiles")["name"] == "Alt"
    assert db.one("SELECT COUNT(*) AS n FROM fasts")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM fasting_advice")["n"] == 0


def test_coach_kontext_enthaelt_kein_fasten(profile_id):
    _on(profile_id)
    _start(profile_id, started_at=_ago(hours=5))
    ctx = str(coach.context(profile_id)).lower()
    assert "fast" not in ctx
