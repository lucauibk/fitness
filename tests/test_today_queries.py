""""Heute" (/api/today) rechnet den Tagesplan genau einmal und laedt den aktiven Split nur so oft
wie noetig: einmal im Wochen-Check (der den Plan aendern darf) und einmal danach fuer alles Weitere."""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import daily_view, db, readiness, stats
from app.main import app

client = TestClient(app)


def _plan_mit_einheiten(pid, anzahl=12):
    assert client.post("/api/planner/create", params={"profile": pid}, json={"days": 4}).status_code == 200
    split = db.active_split(pid)
    start = stats.today() - timedelta(days=2 * anzahl)
    for i in range(anzahl):
        day = split["days"][i % len(split["days"])]
        at = f"{(start + timedelta(days=2 * i)).isoformat()}T10:00:00+00:00"
        db.save_workout({"profile_id": pid, "split_day_id": day["id"], "title": day["label"],
                         "started_at": at, "finished_at": at,
                         "sets": [{"exercise_id": e["exercise_id"], "set_no": n + 1, "reps": 8,
                                   "weight": 40 + i, "rpe": 8}
                                  for e in day["exercises"] for n in range(3)]})


def test_tagesplan_einmal_und_split_hoechstens_zweimal(profile_id, monkeypatch):
    _plan_mit_einheiten(profile_id)
    client.get("/api/today", params={"profile": profile_id})       # Wochen-Check/Block einmal anlegen lassen

    aufrufe = {"day_plan": 0, "active_split": 0}
    echt_plan, echt_split = stats.day_plan, db.active_split

    def day_plan(*a, **kw):
        aufrufe["day_plan"] += 1
        return echt_plan(*a, **kw)

    def active_split(*a, **kw):
        aufrufe["active_split"] += 1
        return echt_split(*a, **kw)

    monkeypatch.setattr(stats, "day_plan", day_plan)
    monkeypatch.setattr(db, "active_split", active_split)
    r = client.get("/api/today", params={"profile": profile_id})
    assert r.status_code == 200
    assert aufrufe["day_plan"] == 1          # frueher 2: today_view rechnete, build verwarf und rechnete neu
    assert aufrufe["active_split"] <= 2      # frueher 5


def test_heute_zeigt_denselben_tag_wie_der_angepasste_tagesplan(profile_id):
    """Inhaltlich unveraendert: "day" ist der fuer den Check-in angepasste Plan des faelligen Tags."""
    _plan_mit_einheiten(profile_id)
    data = client.get("/api/today", params={"profile": profile_id}).json()
    split = db.active_split(profile_id)
    day, _, _ = stats.schedule_day(profile_id, split, stats.today())
    checkin = readiness.get(profile_id, stats.today().isoformat())
    erwartet = daily_view.adjusted_day(profile_id, day, checkin)
    assert data["day"]["id"] == day["id"]
    assert data["day"]["exercises"] == erwartet["exercises"]
    assert data["day"]["block"] == erwartet["block"]
    assert data["days"] == [{"id": d["id"], "label": d["label"], "weekdays": d.get("weekdays") or []}
                            for d in split["days"]]


def test_heute_ohne_plan(profile_id):
    data = client.get("/api/today", params={"profile": profile_id}).json()
    assert data["split"] is None and data["day"] is None
