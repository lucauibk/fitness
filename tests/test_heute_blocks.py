"""Heute: eine Zahl fuers Tagesbudget, Fasten-Kurzstand, Coach-Ungelesen als eigene Abfrage."""
from datetime import datetime, timedelta, timezone

from app import coach_chat, daily_view, db, fasting, nutrition
from app.routes import coach as coach_routes


def test_budget_ist_ziel_plus_verbrannt():
    assert nutrition.budget({"kcal": 2000}, 300) == 2300
    assert nutrition.budget({"kcal": 2000}, None) == 2000


def test_budget_ohne_ziel_ist_null():
    assert nutrition.budget({}, 300) == 0
    assert nutrition.budget(None, 0) == 0


def test_fasten_kurzstand_nur_wenn_aktiv(profile_id):
    profile = db.get_row("profiles", profile_id)
    assert fasting.brief(profile_id, profile) is None
    db.update_row("profiles", profile_id, {"fasting_kind": "16:8"})
    brief = fasting.brief(profile_id, db.get_row("profiles", profile_id))
    assert brief["kind"] == "16:8" and brief["running"] is None and brief["last_ended_at"] is None


def test_fasten_kurzstand_zeigt_laufendes(profile_id):
    start = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    with db.connect() as c:
        c.execute("INSERT INTO fasts (profile_id, started_at, target_hours, kind, client_id) VALUES (?,?,?,?,?)",
                  (profile_id, start, 16, "16:8", f"brief-{profile_id}"))
    brief = fasting.brief(profile_id, db.get_row("profiles", profile_id))
    assert brief["running"]["started_at"] == start


def test_heute_liefert_budget_und_fasten(profile_id):
    view = daily_view.build(profile_id)
    assert "budget_kcal" in view["nutrition"]
    assert view["fasting"] is None


def test_coach_ungelesen_eigene_abfrage(profile_id):
    assert coach_routes.get_unread(profile_id) == {"unread": coach_chat.unread(profile_id)}
