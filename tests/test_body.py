from app.body import navy_body_fat


def test_navy_body_fat_male_plausible_range():
    v = navy_body_fat("männlich", 180, waist_cm=85, neck_cm=38)
    assert v is not None and 5 < v < 25


def test_navy_body_fat_female_needs_hip():
    assert navy_body_fat("weiblich", 165, waist_cm=75, neck_cm=32, hip_cm=None) is None
    v = navy_body_fat("weiblich", 165, waist_cm=75, neck_cm=32, hip_cm=95)
    assert v is not None and 5 < v < 45


def test_navy_body_fat_impossible_measurements_return_none():
    # Taille kleiner als Hals ergibt bei Maennern eine negative/undefinierte Formel.
    assert navy_body_fat("männlich", 180, waist_cm=30, neck_cm=38) is None


def test_navy_body_fat_unknown_sex_returns_none():
    assert navy_body_fat("divers", 180, waist_cm=85, neck_cm=38) is None


# ------------------------------------------------------------------ Trend
from datetime import date, timedelta

from app import body

HEUTE = date(2026, 11, 2)


def _serie(werte, ende=HEUTE, abstand=7):
    """Werte in Reihenfolge, der letzte liegt am Datum `ende`."""
    n = len(werte)
    return [{"date": (ende - timedelta(days=abstand * (n - 1 - i))).isoformat(), "value": v}
            for i, v in enumerate(werte)]


def test_trend_abnehmen_passt_bei_gleicher_muskelmasse():
    series = {"weight_kg": _serie([86.0, 85.6, 85.3, 85.0, 84.7]),
              "muscle_mass_kg": _serie([38.0, 38.2, 38.1], abstand=14)}
    v = body.trend(series, "abnehmen", HEUTE)["verdict"]
    assert v["status"] == "passt"
    assert v["rate_kg_week"] < 0
    assert "gleicher Muskelmasse" in v["text"] and "Abnehmen" in v["text"]


def test_trend_einzelner_ausreisser_kippt_das_urteil_nicht():
    # Eine Waage-Ausreisser-Messung (+1,5 kg Wasser) darf den Trend nicht umdrehen
    series = {"weight_kg": _serie([86.0, 85.6, 85.3, 85.0, 86.5])}
    assert body.trend(series, "abnehmen", HEUTE)["verdict"]["status"] != "gegenlaeufig"


def test_trend_zu_wenig_messungen_kein_urteil():
    assert body.trend({"weight_kg": _serie([85.0, 84.0])}, "abnehmen", HEUTE)["verdict"]["status"] == "zu_wenig"
    # drei Messungen, aber nur an drei aufeinanderfolgenden Tagen (Spanne < 14 Tage)
    kurz = _serie([85.0, 84.8, 84.6], abstand=1)
    assert body.trend({"weight_kg": kurz}, "abnehmen", HEUTE)["verdict"]["status"] == "zu_wenig"


def test_trend_veraltete_messung_kein_urteil():
    alt = _serie([86.0, 85.5, 85.0, 84.5], ende=HEUTE - timedelta(days=40))
    assert body.trend({"weight_kg": alt}, "abnehmen", HEUTE)["verdict"]["status"] == "veraltet"


def test_trend_aufbau_und_gewichtsverlust_ist_gegenlaeufig():
    series = {"weight_kg": _serie([86.0, 85.5, 85.0, 84.5, 84.0])}
    assert body.trend(series, "aufbau", HEUTE)["verdict"]["status"] == "gegenlaeufig"


def test_trend_abnehmen_und_zunahme_ist_gegenlaeufig_zu_schnell_ist_neutral():
    hoch = {"weight_kg": _serie([84.0, 84.5, 85.0, 85.5, 86.0])}
    assert body.trend(hoch, "abnehmen", HEUTE)["verdict"]["status"] == "gegenlaeufig"
    schnell = {"weight_kg": _serie([90.0, 88.0, 86.0, 84.0, 82.0])}
    v = body.trend(schnell, "abnehmen", HEUTE)["verdict"]
    assert v["status"] == "neutral" and "Muskelmasse" in v["text"]


def test_trend_halten_innerhalb_des_bandes_passt():
    series = {"weight_kg": _serie([85.0, 85.1, 84.9, 85.0, 85.1])}
    assert body.trend(series, "fitness", HEUTE)["verdict"]["status"] == "passt"


def test_trend_unbekanntes_oder_fehlendes_ziel_wird_wie_halten_behandelt():
    series = {"weight_kg": _serie([85.0, 85.1, 84.9, 85.0, 85.1])}
    assert body.trend(series, None, HEUTE)["verdict"]["goal"] == "Halten"
    assert body.trend(series, "unsinn", HEUTE)["verdict"]["goal"] == "Halten"


def test_trend_sinkende_muskelmasse_wird_vorsichtig_erwaehnt():
    series = {"weight_kg": _serie([86.0, 85.6, 85.3, 85.0, 84.7]),
              "muscle_mass_kg": _serie([39.5, 38.8, 38.0], abstand=14)}
    text = body.trend(series, "abnehmen", HEUTE)["verdict"]["text"]
    assert "Muskelmasse sinkt" in text and "BIA" in text


def test_trend_kennzahlen_delta_seit_letzter_und_fenster():
    series = {"weight_kg": _serie([90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0], abstand=14),
              "waist_cm": _serie([90.0, 89.0])}
    m = body.trend(series, "abnehmen", HEUTE)["metrics"]
    assert m["weight_kg"]["since_last"] == {"delta": -1.0, "days": 14}
    assert m["weight_kg"]["w4"] < 0 and m["weight_kg"]["w12"] < m["weight_kg"]["w4"]
    assert m["waist_cm"]["w12"] is None            # Verlauf zu kurz
    assert m["weight_kg"]["noisy"] is False


def test_trend_bia_werte_sind_als_unsicher_markiert_und_leer_ist_leer():
    m = body.trend({"body_fat_pct": _serie([20.0, 19.5, 19.8])}, "abnehmen", HEUTE)
    assert m["metrics"]["body_fat_pct"]["noisy"] is True
    assert m["verdict"] is None                     # ohne Gewicht kein Urteil
    assert body.trend({}, "aufbau", HEUTE) == {"metrics": {}, "verdict": None}


def test_trend_mehrere_messungen_pro_tag_werden_gemittelt():
    s = [{"date": "2026-11-01", "value": 85.0}, {"date": "2026-11-01", "value": 86.0}]
    assert body.trend({"weight_kg": s}, "fitness", HEUTE)["metrics"]["weight_kg"]["smoothed"] == 85.5


def test_measurements_route_liefert_trend_mit_profilziel(profile_id):
    from app import db
    from app.routes import body as routes
    db.update_row("profiles", profile_id, {"goal": "aufbau"})
    for i, kg in enumerate([80.0, 80.4, 80.8, 81.2]):
        body.save(profile_id, {"date": (HEUTE - timedelta(days=7 * (3 - i))).isoformat(), "weight_kg": kg})
    out = routes.list_measurements(profile_id)
    assert out["trend"]["metrics"]["weight_kg"]["since_last"]["delta"] == 0.4
    assert out["trend"]["verdict"]["goal"] == "Aufbau"


# ------------------------------------------------------------ KI-Tipps
import json
import subprocess

import pytest
from fastapi import HTTPException

from app import body_advice, claude_cli


def _messungen(profile_id):
    from app import db
    db.update_row("profiles", profile_id, {"goal": "abnehmen", "sex": "männlich", "height_cm": 180})
    for i, kg in enumerate([86.0, 85.6, 85.3, 85.0, 84.7]):
        body.save(profile_id, {"date": (date.today() - timedelta(days=7 * (4 - i))).isoformat(),
                               "weight_kg": kg})


def test_advice_ohne_claude_code_ist_ein_klarer_fehler(profile_id, monkeypatch):
    from app.routes import body as routes
    monkeypatch.setattr(claude_cli, "available", lambda: False)
    with pytest.raises(HTTPException) as e:
        routes.advice(profile_id)
    assert e.value.status_code == 400


def test_advice_ohne_messung_wird_abgelehnt(profile_id, monkeypatch):
    from app.routes import body as routes
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: pytest.fail("ohne Daten darf nichts verschickt werden"))
    with pytest.raises(HTTPException) as e:
        routes.advice(profile_id)
    assert e.value.status_code == 400


def test_advice_schickt_nur_zahlen_und_eckdaten_und_begrenzt_die_antwort(profile_id, monkeypatch):
    from app import db
    from app.routes import body as routes
    _messungen(profile_id)
    db.update_row("profiles", profile_id, {"name": "Geheimer Name"})
    seen = {}

    def fake(text, system, model, timeout=300):
        seen.update(text=text, system=system)
        return json.dumps({"gut": ["a", "b", "c", "d", " "], "schlecht": [], "verbessern": ["x" * 500, 7]})
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "ask", fake)
    res = routes.advice(profile_id)
    assert "Geheimer Name" not in seen["text"]
    assert "abnehmen" in seen["text"] and "Gewicht" in seen["text"]
    assert "Keine Tipps zu Training, Ernährung" in seen["system"]       # nur Körpermessung
    assert res["gut"] == ["a", "b", "c"]                                # höchstens 3, Leeres raus
    assert res["schlecht"] == []
    assert len(res["verbessern"]) == 1 and len(res["verbessern"][0]) == body_advice.MAX_CHARS


def test_tipps_werden_am_satzende_gekuerzt_nicht_mittendrin():
    lang = "Das ist ein ganzer Satz mit etwas Länge. " * 12
    out = body_advice.parse(json.dumps({"gut": [lang], "schlecht": [], "verbessern": []}))["gut"][0]
    assert len(out) <= body_advice.MAX_CHARS and out.endswith(".")


@pytest.mark.parametrize("fehler", [RuntimeError("claude Exit 1"), OSError("nicht gefunden"),
                                    subprocess.TimeoutExpired("claude", 90), "kein json", "{}"])
def test_advice_fehler_werden_zu_422(profile_id, monkeypatch, fehler):
    from app.routes import body as routes
    _messungen(profile_id)

    def fake(*a, **k):
        if isinstance(fehler, Exception):
            raise fehler
        return fehler
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "ask", fake)
    with pytest.raises(HTTPException) as e:
        routes.advice(profile_id)
    assert e.value.status_code == 422


def test_measurements_route_meldet_ob_tipps_moeglich_sind(profile_id, monkeypatch):
    from app.routes import body as routes
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    assert routes.list_measurements(profile_id)["tips_available"] is True
    monkeypatch.setattr(claude_cli, "available", lambda: False)
    assert routes.list_measurements(profile_id)["tips_available"] is False
