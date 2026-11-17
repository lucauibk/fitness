"""Fasten-Berater: Regeln (rein), Start-Sperre, Profilfelder, Migration, KI-Endpunkt (per monkeypatch)."""
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import claude_cli, db, fasting, fasting_advice as fa, main
from app.routes import nutrition as routes

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _profile(**kw):
    base = {"birth_year": 1990, "height_cm": 175, "goal": "fitness", "experience": "einsteiger"}
    base.update(kw)
    return base


def _hist(hits, total=7, target=14):
    return [{"hit": i < hits, "target_hours": target} for i in range(total)]


def rec(profile=None, weight=70, history=(), workouts=(), checkins=None):
    return fa.recommend(profile or _profile(), weight, list(history), list(workouts), NOW, checkins)


# ------------------------------------------------------------------ Sperren
@pytest.mark.parametrize("profile, weight, code", [
    (_profile(birth_year=NOW.year - 17), 70, "age"),
    (_profile(), 54.0, "bmi"),                       # 175 cm, 54 kg -> BMI 17,6
    (_profile(fasting_flags="pregnant"), 70, "pregnant"),
    (_profile(fasting_flags="eating_disorder"), 70, "eating_disorder"),
    (_profile(fasting_flags="diabetes_meds"), 70, "diabetes_meds"),
    (_profile(fasting_flags=["diabetes_meds"]), 70, "diabetes_meds"),   # profile_out liefert Listen
])
def test_sperre(profile, weight, code):
    r = rec(profile, weight)
    assert r["allowed"] is False and r["kind"] is None
    assert code in [b["code"] for b in r["blocked"]] and all(b["text"] for b in r["blocked"])


def test_bmi_grenze_18_5_ist_erlaubt():
    # 175 cm: BMI 18,5 bei 56,7 kg (gerundet 18,5)
    assert rec(_profile(), 56.7)["allowed"] is True
    assert rec(_profile(), 56.0)["allowed"] is False


def test_alter_18_ist_erlaubt():
    assert rec(_profile(birth_year=NOW.year - 18))["allowed"] is True


def test_mehrere_sperren_gleichzeitig():
    r = rec(_profile(birth_year=NOW.year - 16, fasting_flags="pregnant,eating_disorder"), 50)
    assert {b["code"] for b in r["blocked"]} == {"age", "bmi", "pregnant", "eating_disorder"}


def test_fehlendes_geburtsjahr_nur_einstieg_mit_warnung():
    r = rec(_profile(birth_year=None), history=_hist(7), )
    assert r["allowed"] is True and r["kind"] == "14:10" and r["missing"] == ["birth_year"]
    assert any("Geburtsjahr" in w for w in r["warnings"])


def test_fehlendes_gewicht_oder_groesse_nur_einstieg():
    r = rec(_profile(), weight=None, history=_hist(7))
    assert r["kind"] == "14:10" and r["missing"] == ["weight_kg"]
    assert rec(_profile(height_cm=None), 70, _hist(7))["missing"] == ["height_cm"]


# ------------------------------------------------- Einstieg, Aufstieg, Deckel
def test_einstieg_ohne_verlauf():
    r = rec()
    assert (r["allowed"], r["kind"], r["blocked"], r["missing"]) == (True, "14:10", [], [])
    assert r["reasons"] and isinstance(r["warnings"], list)


@pytest.mark.parametrize("hits, kind", [(4, "14:10"), (5, "16:8"), (7, "16:8")])
def test_aufstieg_bei_5_von_7(hits, kind):
    assert rec(history=_hist(hits))["kind"] == kind


def test_aufstieg_braucht_mindestens_5_fasten():
    assert rec(history=_hist(4, total=4))["kind"] == "14:10"


def test_nur_die_letzten_7_zaehlen():
    older = _hist(0, total=7)             # neueste zuerst: 7 verfehlt vorn, Treffer danach zaehlen nicht
    assert rec(history=older + _hist(7))["kind"] == "14:10"


@pytest.mark.parametrize("profile, weight, kind", [
    (_profile(experience="fortgeschritten", goal="abnehmen"), 70, "18:6"),
    (_profile(experience="erfahren", goal="abnehmen"), 70, "18:6"),
    (_profile(experience="einsteiger", goal="abnehmen"), 70, "16:8"),
    (_profile(experience="fortgeschritten", goal="fitness"), 70, "16:8"),
    (_profile(experience="fortgeschritten", goal="abnehmen"), 60, "16:8"),   # BMI 19,6 < 20
])
def test_18_6_nur_bei_erfahrung_bmi_und_fettabbau(profile, weight, kind):
    assert rec(profile, weight, _hist(6, target=16))["kind"] == kind


def test_18_6_braucht_erfolge_bei_mindestens_16_stunden():
    p = _profile(experience="fortgeschritten", goal="abnehmen")
    assert rec(p, 70, _hist(6, target=14))["kind"] == "16:8"


def test_18_6_hat_ehrlichen_evidenzhinweis():
    p = _profile(experience="fortgeschritten", goal="abnehmen")
    assert any("Studienlage dünn" in r for r in rec(p, 70, _hist(6, target=16))["reasons"])


def test_20_4_und_omad_werden_nie_empfohlen():
    p = _profile(experience="erfahren", goal="abnehmen")
    for kind in ("20:4", "OMAD"):
        assert rec({**p, "fasting_kind": kind}, 70, _hist(7, target=20))["kind"] == "18:6"


@pytest.mark.parametrize("experience", ["einsteiger", "fortgeschritten", "erfahren"])
@pytest.mark.parametrize("hits", [0, 5, 7])
def test_muskelaufbau_deckel_16_8(experience, hits):
    r = rec(_profile(goal="aufbau", experience=experience), 70, _hist(hits, target=16))
    assert fasting.KINDS[r["kind"]] <= 16


def test_gewaehlte_fastenart_ueber_empfehlung_warnt_aber_sperrt_nicht():
    r = rec(_profile(fasting_kind="OMAD", goal="aufbau"))
    assert r["allowed"] is True and r["kind"] == "14:10"
    assert any("liegt über der Empfehlung" in w for w in r["warnings"])
    assert any("Studienlage dünn" in w for w in r["warnings"])


def test_essensfenster_wunsch_und_trainingszeit_im_hinweis():
    morgens = [{"started_at": (NOW - timedelta(days=i)).replace(hour=6).isoformat()} for i in range(1, 5)]
    r = rec(_profile(eating_window_pref="abends"), workouts=morgens)
    text = " ".join(r["reasons"])
    assert "10–20 Uhr" in text and "meist morgens" in text


def test_trainingshinweis_ohne_genug_einheiten_ist_allgemein():
    assert "Essensfenster oder direkt davor" in " ".join(rec()["reasons"])


def test_schlechter_schlaf_und_stress_warnen():
    days = [{"date": (NOW - timedelta(days=i)).date().isoformat(), "sleep": 2, "stress": 4} for i in range(4)]
    w = rec(checkins=days)["warnings"]
    assert any("Schlaf" in x for x in w) and any("Stress" in x for x in w)
    gut = [{"date": d["date"], "sleep": 4, "stress": 2} for d in days]
    assert rec(checkins=gut)["warnings"] == []
    assert rec(checkins=days[:2])["warnings"] == []          # zu wenige Check-ins


def test_deterministisch_und_ohne_seiteneffekt():
    args = dict(profile=_profile(goal="abnehmen", fasting_kind="16:8"), history=_hist(5))
    before = json.dumps(args, sort_keys=True)
    assert rec(**args) == rec(**args)
    assert json.dumps(args, sort_keys=True) == before


# -------------------------------------------------------------------- Tipps
def test_phase_tips_ohne_ketose_versprechen_mit_eiweiss():
    for kind in [*fasting.KINDS, "custom", "quatsch"]:
        t = fa.phase_tips(kind)
        text = " ".join(t["tipps"]).lower()
        assert "1,2–1,6 g" in text and "ketose" not in text and "fettverbrennung" not in text
        assert (t["evidenz"] is not None) == (kind in fasting.ADVANCED)


def test_protokolle_haben_alle_fastenarten():
    p = {x["kind"]: x for x in fa.protocols()}
    assert set(p) == set(fasting.KINDS) >= {"12:12", "20:4", "OMAD"}
    assert fasting.KINDS["OMAD"] == 23 and p["OMAD"]["advanced"] and not p["16:8"]["advanced"]


def test_tips_endpunkt():
    out = routes.fasting_tips("16:8")
    assert out["kind"] == "16:8" and len(out["tipps"]) >= 5 and out["evidenz"] is None
    assert routes.fasting_tips("OMAD")["evidenz"]
    assert routes.fasting_tips(None)["tipps"]


# --------------------------------------------------------------- Start-Sperre
def _on(pid, kind="16:8", **extra):
    db.update_row("profiles", pid, {"fasting_kind": kind, **extra})


def _start(pid, cid="s"):
    return routes.start_fast(routes.FastStart(profile_id=pid, client_id=f"{cid}-{pid}"))


@pytest.mark.parametrize("fields", [
    {"fasting_flags": "pregnant"}, {"fasting_flags": "eating_disorder"}, {"fasting_flags": "diabetes_meds"},
    {"birth_year": datetime.now().year - 17},
])
def test_start_gesperrt_409(profile_id, fields):
    _on(profile_id, **fields)
    with pytest.raises(HTTPException) as e:
        _start(profile_id)
    assert e.value.status_code == 409 and "nicht empfohlen" in e.value.detail
    assert fasting.running(profile_id) is None


def test_start_gesperrt_bei_bmi_unter_18_5(profile_id):
    _on(profile_id, height_cm=180)
    db.set_bodyweight(profile_id, "2026-11-01", 55)
    with pytest.raises(HTTPException) as e:
        _start(profile_id)
    assert e.value.status_code == 409 and "BMI" in e.value.detail


def test_start_ohne_angaben_bleibt_moeglich(profile_id):
    _on(profile_id)
    assert _start(profile_id)["created"] is True


def test_nachsenden_eines_bestehenden_starts_wird_nicht_gesperrt(profile_id):
    _on(profile_id)
    _start(profile_id)
    _on(profile_id, fasting_flags="pregnant")
    assert _start(profile_id)["created"] is False        # gleicher Start aus dem Ausgang


# ------------------------------------------------------------- Profilfelder
def _patch(pid, **kw):
    return main.api_update_profile(pid, main.ProfileIn(**kw))


def test_profil_roundtrip_neue_felder(profile_id):
    out = _patch(profile_id, fasting_kind="OMAD", fasting_flags=["diabetes_meds", "pregnant", "pregnant"],
                 eating_window_pref="abends")
    assert out["fasting_flags"] == ["pregnant", "diabetes_meds"]            # dedupliziert, feste Reihenfolge
    assert out["fasting_kind"] == "OMAD" and out["eating_window_pref"] == "abends"
    assert db.get_row("profiles", profile_id)["fasting_flags"] == "pregnant,diabetes_meds"
    assert main.api_profile(profile_id)["fasting_flags"] == ["pregnant", "diabetes_meds"]
    out = _patch(profile_id, fasting_flags=[], eating_window_pref=None)
    assert out["fasting_flags"] == [] and out["eating_window_pref"] is None


def test_profil_neue_fastenarten_gueltig(profile_id):
    for kind in ("12:12", "20:4", "OMAD"):
        assert _patch(profile_id, fasting_kind=kind)["fasting_kind"] == kind


@pytest.mark.parametrize("kw", [
    {"fasting_flags": ["gesund"]}, {"fasting_flags": "pregnant"}, {"eating_window_pref": "mittags"},
    {"fasting_kind": "22:2"},
])
def test_profil_ungueltige_werte_werden_abgelehnt(kw):
    with pytest.raises(ValidationError):
        main.ProfileIn(**kw)


def test_migration_ergaenzt_neue_spalten_und_tabelle_in_alter_db(tmp_path, monkeypatch):
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
    assert {"fasting_flags", "eating_window_pref"} <= cols
    assert db.one("SELECT fasting_flags FROM profiles")["fasting_flags"] is None
    assert db.one("SELECT COUNT(*) AS n FROM fasting_advice")["n"] == 0
    db.init()                                             # zweiter Start: idempotent


# ------------------------------------------------------------------ KI-Pfad
GOOD = json.dumps({"erklaerung": "14:10 passt als Einstieg.", "tipps": ["Trink genug.", "Iss Eiweiß.",
                                                                          "Brich mit Gemüse.", "Viertens."]})


@pytest.fixture
def llm(monkeypatch):
    calls = []

    def ask(text, system, model, timeout=0):
        calls.append({"text": text, "system": system, "model": model})
        return GOOD

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "ask", ask)
    return calls


def test_advice_antwortform_und_tipps_begrenzt(profile_id, llm):
    _on(profile_id, goal="abnehmen")
    out = routes.fasting_advice_ai(profile_id)
    assert out == {"kind": "14:10", "erklaerung": "14:10 passt als Einstieg.",
                   "tipps": ["Trink genug.", "Iss Eiweiß.", "Brich mit Gemüse."], "cached": False}
    assert len(llm) == 1 and llm[0]["model"] == fa.MODEL


def test_advice_kind_kommt_aus_den_regeln_nicht_aus_der_ki(profile_id, monkeypatch, llm):
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: json.dumps(
        {"erklaerung": "Nimm OMAD.", "kind": "OMAD", "tipps": ["x"]}))
    assert routes.fasting_advice_ai(profile_id)["kind"] == "14:10"


def test_advice_cache_treffer_ohne_zweiten_aufruf(profile_id, llm):
    first = routes.fasting_advice_ai(profile_id)
    second = routes.fasting_advice_ai(profile_id)
    assert len(llm) == 1 and second["cached"] is True and first["cached"] is False
    assert {k: v for k, v in second.items() if k != "cached"} == {k: v for k, v in first.items() if k != "cached"}


def test_advice_cache_laeuft_nach_24_stunden_ab(profile_id, llm):
    routes.fasting_advice_ai(profile_id)
    with db.connect() as c:
        c.execute("UPDATE fasting_advice SET created_at=? WHERE profile_id=?",
                  ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds"), profile_id))
    assert routes.fasting_advice_ai(profile_id)["cached"] is False and len(llm) == 2


def test_advice_neue_eingabe_neuer_aufruf(profile_id, llm):
    routes.fasting_advice_ai(profile_id)
    _on(profile_id, goal="aufbau")
    routes.fasting_advice_ai(profile_id)
    assert len(llm) == 2


def test_advice_fallback_tipps_wenn_ki_keine_liefert(profile_id, monkeypatch, llm):
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: json.dumps({"erklaerung": "Passt.", "tipps": []}))
    out = routes.fasting_advice_ai(profile_id)
    assert out["tipps"] == fa.phase_tips("14:10")["tipps"][:fa.MAX_TIPS] and out["erklaerung"] == "Passt."


def test_advice_ohne_claude_ist_400(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "available", lambda: False)
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: pytest.fail("kein Aufruf ohne claude"))
    with pytest.raises(HTTPException) as e:
        routes.fasting_advice_ai(profile_id)
    assert e.value.status_code == 400


@pytest.mark.parametrize("fehler", [RuntimeError("x"), subprocess.TimeoutExpired("claude", 1), OSError("x"),
                                    "kein json", json.dumps({"tipps": ["a"]})])
def test_advice_fehler_werden_zu_422_und_nicht_gecached(profile_id, monkeypatch, llm, fehler):
    def ask(*a, **k):
        if isinstance(fehler, BaseException):
            raise fehler
        return fehler

    monkeypatch.setattr(claude_cli, "ask", ask)
    with pytest.raises(HTTPException) as e:
        routes.fasting_advice_ai(profile_id)
    assert e.value.status_code == 422
    assert db.one("SELECT COUNT(*) AS n FROM fasting_advice WHERE profile_id=?", (profile_id,))["n"] == 0


def test_advice_busy_ist_429(profile_id, llm):
    assert fa._busy.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as e:
            routes.fasting_advice_ai(profile_id)
        assert e.value.status_code == 429 and not llm
    finally:
        fa._busy.release()
    routes.fasting_advice_ai(profile_id)                 # danach geht es wieder


def test_advice_gibt_sperre_auch_bei_fehler_frei(profile_id, monkeypatch, llm):
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(HTTPException):
        routes.fasting_advice_ai(profile_id)
    assert fa._busy.acquire(blocking=False)
    fa._busy.release()


def test_advice_bei_sperre_kein_aufruf(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: pytest.fail("bei Sperre darf nichts verschickt werden"))
    _on(profile_id, fasting_flags="eating_disorder")
    out = routes.fasting_advice_ai(profile_id)
    assert out["kind"] is None and out["tipps"] == [] and out["cached"] is False and out["erklaerung"]


def test_prompt_enthaelt_keinen_namen_und_nur_eckdaten(profile_id, llm):
    name = db.get_row("profiles", profile_id)["name"]
    _on(profile_id, goal="abnehmen", birth_year=1990, height_cm=175, limitations="Knie rechts")
    db.set_bodyweight(profile_id, "2026-11-01", 70)
    routes.fasting_advice_ai(profile_id)
    text = llm[0]["text"]
    assert name not in text and name not in llm[0]["system"]
    assert "Knie" not in text and "2026-11-01" not in text
    assert "Ziel: abnehmen" in text and "BMI-Klasse: Normalgewicht" in text and "Quote: 0 von 0" in text


def test_recommendation_endpunkt_ohne_ki(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: pytest.fail("Empfehlung läuft ohne KI"))
    out = routes.fasting_recommendation(profile_id)
    assert out["allowed"] is True and out["kind"] == "14:10"
    assert set(out) >= {"allowed", "kind", "reasons", "warnings", "blocked", "missing", "quota",
                        "current_kind", "protocols", "disclaimer"}
    assert out["quota"] == {"hits": 0, "total": 0} and "birth_year" in out["missing"]
