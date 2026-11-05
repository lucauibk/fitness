"""Coach-Chat und der Claude-Aufruf: das Abo statt eines API-Schluessels, lesbare Fehler,
Verlauf in der DB und hoechstens eine Antwort zur Zeit."""
import json
import os
import subprocess
import threading

import pytest
from fastapi import HTTPException

from app import claude_cli, coach_chat, daily_view, db
from app.routes import coach as routes


# ------------------------------------------------------------ claude_cli
class _Done:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def test_cli_nimmt_das_abo_nicht_den_api_schluessel(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "abo")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return _Done(json.dumps({"is_error": False, "result": "hallo"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert claude_cli.ask("x", "sys", "m") == "hallo"
    assert "ANTHROPIC_API_KEY" not in seen and seen["CLAUDE_CODE_OAUTH_TOKEN"] == "abo"


def test_cli_fehler_nennt_die_ursache(monkeypatch):
    out = json.dumps({"type": "result", "is_error": True, "result": "Credit balance is too low",
                      "usage": {"input_tokens": 0}})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _Done(out, returncode=1))
    with pytest.raises(RuntimeError, match="Credit balance is too low"):
        claude_cli.ask("x", "sys", "m")


def _fake_cli(tmp_path, lines, exit_code=0):
    """Kleines Skript, das wie `claude -p --output-format stream-json` Zeilen ausgibt."""
    script = tmp_path / "claude"
    body = "\n".join(json.dumps(line) for line in lines)
    script.write_text(f"#!/bin/sh\ncat >/dev/null\ncat <<'EOF'\n{body}\nEOF\nexit {exit_code}\n")
    script.chmod(0o755)
    return str(script)


def _delta(text):
    return {"type": "stream_event", "event": {"type": "content_block_delta",
                                              "delta": {"type": "text_delta", "text": text}}}


def test_stream_liefert_stuecke_und_ende(tmp_path, monkeypatch):
    exe = _fake_cli(tmp_path, [{"type": "system"}, _delta("Hal"), _delta("lo"),
                               {"type": "result", "is_error": False, "result": "Hallo"}])
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: exe)
    assert list(claude_cli.stream("x", "sys", "m")) == [("delta", "Hal"), ("delta", "lo"), ("done", "Hallo")]


def test_stream_fehler_aus_dem_ergebnis(tmp_path, monkeypatch):
    exe = _fake_cli(tmp_path, [{"type": "result", "is_error": True, "result": "usage limit reached"}], 1)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: exe)
    with pytest.raises(RuntimeError, match="usage limit"):
        list(claude_cli.stream("x", "sys", "m"))


def test_stream_timeout(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: str(script))
    with pytest.raises(RuntimeError, match="zu lange"):
        list(claude_cli.stream("x", "sys", "m", timeout=0.5))


# ------------------------------------------------------------ Chat
def _fake_stream(chunks=("Mach ", "weiter."), error=None):
    def fake(text, system, model, timeout):
        fake.prompt = text
        for c in chunks:
            yield "delta", c
        if error:
            raise RuntimeError(error)
        yield "done", "".join(chunks)
    return fake


def test_antwort_wird_mit_frage_gespeichert(profile_id, monkeypatch):
    fake = _fake_stream()
    monkeypatch.setattr(claude_cli, "stream", fake)
    events = list(coach_chat.answer(profile_id, "Wie lief es?"))
    assert [k for k, _ in events] == ["delta", "delta", "done"]
    assert events[-1][1]["text"] == "Mach weiter."
    assert [(m["role"], m["text"]) for m in coach_chat.messages(profile_id)] == \
        [("user", "Wie lief es?"), ("coach", "Mach weiter.")]
    assert "<frage>\nWie lief es?\n</frage>" in fake.prompt
    # Die neue Frage steht nur in <frage>, nicht doppelt im Verlauf
    assert fake.prompt.count("Wie lief es?") == 1


def test_zweite_frage_sieht_den_verlauf(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "stream", _fake_stream())
    list(coach_chat.answer(profile_id, "Erste Frage"))
    fake = _fake_stream()
    monkeypatch.setattr(claude_cli, "stream", fake)
    list(coach_chat.answer(profile_id, "Zweite"))
    verlauf = fake.prompt.split("<verlauf>")[1].split("</verlauf>")[0]
    assert "Erste Frage" in verlauf and "Mach weiter." in verlauf


def test_fehler_wird_lesbar_gespeichert(profile_id, monkeypatch):
    monkeypatch.setattr(claude_cli, "stream", _fake_stream(chunks=(), error="usage limit reached"))
    kind, msg = list(coach_chat.answer(profile_id, "Hallo?"))[-1]
    assert kind == "error" and msg["status"] == "error" and "Nutzungslimit" in msg["text"]
    # Fehler landen nicht als Text auf "Heute"
    assert "Nutzungslimit" not in coach_chat.summary(profile_id)["impulse"]["text"]


def test_kontext_ohne_plan_mit_ernaehrung_und_koerper(profile_id):
    ctx = coach_chat.context(profile_id)
    assert ctx["plan"] is None
    assert {"ziele", "heute", "schnitt_vortage"} <= set(ctx["ernaehrung"])
    assert "gewicht_kg" in ctx["koerper"]


def test_nur_eine_antwort_zur_zeit(profile_id, monkeypatch):
    gate = threading.Event()

    def slow(text, system, model, timeout):
        yield "delta", "a"
        gate.wait(5)
        yield "done", "a"

    monkeypatch.setattr(claude_cli, "stream", slow)
    first = coach_chat.answer(profile_id, "eins")
    assert next(first) == ("delta", "a")
    with pytest.raises(HTTPException) as e:
        routes.post_chat(routes.ChatIn(text="zwei"), profile_id)
    assert e.value.status_code == 429
    gate.set()
    list(first)
    monkeypatch.setattr(claude_cli, "stream", _fake_stream())
    assert routes.post_chat(routes.ChatIn(text="drei"), profile_id).media_type == "text/event-stream"


def test_zu_lange_nachricht():
    with pytest.raises(ValueError):
        routes.ChatIn(text="x" * (coach_chat.MAX_QUESTION + 1))


def test_ungelesen_und_heute(profile_id):
    coach_chat.add_message(profile_id, "coach", "Guten Morgen", source="daily")
    last = coach_chat.add_message(profile_id, "coach", "Noch was")
    assert coach_chat.unread(profile_id) == 2
    routes.post_seen(routes.SeenIn(id=last["id"]), profile_id)
    assert coach_chat.unread(profile_id) == 0
    assert daily_view.build(profile_id)["coach"]["unread"] == 0


def test_heute_begruesst_dann_tipp_dann_impuls(profile_id):
    assert coach_chat.impulse(profile_id)["kind"] == "hallo"
    coach_chat.add_message(profile_id, "user", "Hi")
    tip = coach_chat.impulse(profile_id)
    assert tip["kind"] == "tipp" and tip["text"] in coach_chat.FALLBACK_TIPS
    coach_chat.set_impulse(profile_id, "Heute UK: Hip Thrust 40 kg x 10 ist fällig.")
    assert daily_view.build(profile_id)["coach"]["impulse"] == \
        {"text": "Heute UK: Hip Thrust 40 kg x 10 ist fällig.", "kind": "impuls"}
    coach_chat.set_impulse(profile_id, "Neu am selben Tag")      # zweiter Lauf ersetzt
    assert coach_chat.impulse(profile_id)["text"] == "Neu am selben Tag"


def test_impuls_von_gestern_gilt_nicht(profile_id):
    from datetime import timedelta
    from app.stats import today
    coach_chat.add_message(profile_id, "user", "Hi")
    coach_chat.set_impulse(profile_id, "Gestern: UK", day=today() - timedelta(days=1))
    assert coach_chat.impulse(profile_id)["kind"] == "tipp"


def test_fremde_nachrichten_bleiben_fremd(profile_id):
    coach_chat.add_message(profile_id, "user", "geheim")
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    assert routes.get_messages(other, before=None)["messages"] == []


# ------------------------------------------------------------ Uebung ersetzen
def _plan_with(profile_id, *names):
    with db.connect() as c:
        ids = []
        for n in names:
            ids.append(c.execute("INSERT INTO exercises (name, muscle_group, unit) VALUES (?,?,?)",
                                 (f"{n}-{os.urandom(3).hex()}", "beine", "kg")).lastrowid)
        split = c.execute("INSERT INTO splits (profile_id, name, active, created_at) VALUES (?,?,1,?)",
                          (profile_id, "T", db.now())).lastrowid
        day = c.execute("INSERT INTO split_days (split_id, position, label) VALUES (?,0,'UK')", (split,)).lastrowid
        item = c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position, generated)"
                         " VALUES (?,?,0,1)", (day, ids[0])).lastrowid
        c.execute("INSERT INTO split_exercises (split_day_id, exercise_id, position) VALUES (?,?,1)", (day, ids[1]))
    rows = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM exercises WHERE id IN (%s)" % ",".join(map(str, ids)))}
    return item, ids, [rows[i] for i in ids]


def _block(actions):
    return f"Nimm lieber **Beinpresse**.\n<aktionen>{json.dumps(actions, ensure_ascii=False)}</aktionen>"


def test_vorschlag_wird_geprueft(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    text, actions = coach_chat.split_actions(_block([
        {"typ": "ersetzen", "plan_id": item, "neu": names[2].upper()},      # ok, Gross/Klein egal
        {"typ": "ersetzen", "plan_id": item, "neu": names[1]},              # schon am Tag
        {"typ": "ersetzen", "plan_id": item, "neu": "Gibt es nicht"},
        {"typ": "ersetzen", "plan_id": 999999, "neu": names[2]},            # fremde Planzeile
        {"typ": "loeschen", "plan_id": item},
    ]), profile_id)
    assert text == "Nimm lieber **Beinpresse**."
    assert [(a["from"], a["to"], a["state"]) for a in actions] == [(names[0], names[2], "open")]


def test_kaputter_block_ergibt_nur_text(profile_id):
    assert coach_chat.split_actions("Hallo <aktionen>[kaputt", profile_id) == ("Hallo", [])


def test_ersetzen_und_rueckgaengig(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse", "Hack")
    _, actions = coach_chat.split_actions(_block([
        {"typ": "ersetzen", "plan_id": item, "neu": names[2]},
        {"typ": "ersetzen", "plan_id": item, "neu": names[3]}]), profile_id)
    msg = coach_chat.add_message(profile_id, "coach", "x", actions=actions)
    out = routes.post_action(msg["id"], 0, "apply", profile_id)["message"]
    assert [a["state"] for a in out["actions"]] == ["applied", "skipped"]
    row = db.get_row("split_exercises", item)
    assert row["exercise_id"] == ids[2] and row["generated"] is None
    with pytest.raises(HTTPException) as e:
        routes.post_action(msg["id"], 1, "apply", profile_id)
    assert e.value.status_code == 409
    out = routes.post_action(msg["id"], 0, "undo", profile_id)["message"]
    assert [a["state"] for a in out["actions"]] == ["undone", "open"]
    assert db.get_row("split_exercises", item)["exercise_id"] == ids[0]
    # Verlauf zeigt dem Coach, was aus seinen Vorschlaegen wurde
    assert "rückgängig" in coach_chat._history(profile_id)[-1]["vorschlaege"][0]


def test_ersetzen_nach_planaenderung_abgelehnt(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse", "Hack")
    _, actions = coach_chat.split_actions(_block([{"typ": "ersetzen", "plan_id": item, "neu": names[2]}]), profile_id)
    msg = coach_chat.add_message(profile_id, "coach", "x", actions=actions)
    db.update_row("split_exercises", item, {"exercise_id": ids[3]})
    with pytest.raises(HTTPException) as e:
        routes.post_action(msg["id"], 0, "apply", profile_id)
    assert e.value.status_code == 409 and db.get_row("split_exercises", item)["exercise_id"] == ids[3]


def test_fremdes_profil_darf_nicht_ersetzen(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    _, actions = coach_chat.split_actions(_block([{"typ": "ersetzen", "plan_id": item, "neu": names[2]}]), profile_id)
    msg = coach_chat.add_message(profile_id, "coach", "x", actions=actions)
    with db.connect() as c:
        other = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                          (f"Andere-{os.urandom(4).hex()}", db.now())).lastrowid
    with pytest.raises(HTTPException):
        routes.post_action(msg["id"], 0, "apply", other)
    assert db.get_row("split_exercises", item)["exercise_id"] == ids[0]


def test_antwort_speichert_vorschlaege(profile_id, monkeypatch):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    raw = _block([{"typ": "ersetzen", "plan_id": item, "neu": names[2]}])
    monkeypatch.setattr(claude_cli, "stream", _fake_stream(chunks=(raw,)))
    kind, msg = list(coach_chat.answer(profile_id, "Kniebeuge tut im Knie weh, Alternative?"))[-1]
    assert kind == "done" and "<aktionen>" not in msg["text"] and msg["actions"][0]["to"] == names[2]


def test_kontext_hat_plan_ids_und_katalog(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker")
    ctx = coach_chat.context(profile_id)
    assert ctx["plan"]["tage"][0]["uebungen"][0]["plan_id"] == item
    assert names[0] in ctx["uebungskatalog"]["beine"]
    assert "cardio" in ctx


def test_fehler_zaehlen_nicht_als_ungelesen(profile_id):
    coach_chat.add_message(profile_id, "coach", "kaputt", status="error")
    assert coach_chat.unread(profile_id) == 0


def test_nach_dem_training_urteil_statt_impuls(profile_id, monkeypatch):
    from app import workout_review
    coach_chat.set_impulse(profile_id, "Geh heute bis RPE 8")
    monkeypatch.setattr(workout_review, "get", lambda wid: {"status": "done", "body": {"kurzurteil": "Starke Einheit."}})
    assert coach_chat.impulse(profile_id, {"id": 1}) == {"text": "Starke Einheit.", "kind": "erledigt"}
    monkeypatch.setattr(workout_review, "get", lambda wid: None)
    assert coach_chat.impulse(profile_id, {"id": 1})["text"] == coach_chat.AFTER_WORKOUT
    assert coach_chat.impulse(profile_id)["text"] == "Geh heute bis RPE 8"


def test_motivation_wird_gespeichert_und_begrenzt(profile_id):
    from app import main
    out = main.api_update_profile(profile_id, main.ProfileIn(motivation="Ziel-Outfit"))
    assert out["motivation"] == "Ziel-Outfit"
    with pytest.raises(ValueError):
        main.ProfileIn(motivation="x" * 301)


# ------------------------------------------------------------ Coach in der laufenden Einheit
def _session(ids, done=1):
    """Entwurf wie aus der App: erste Uebung mit einem erledigten Satz."""
    return {"title": "UK", "started_at": "2026-10-16T10:00:00Z", "block": {"week": 2, "weeks": 6, "rir": 2},
            "current": 0, "notes": None,
            "exercises": [{"exercise_id": i, "reps_min": 8, "reps_max": 12,
                           "sets": [{"reps": 10, "weight": 60, "rpe": 9, "done": n < done, "warmup": False}
                                    for n in range(3)]} for i in ids]}


def test_session_kontext_mit_namen_aus_der_db(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker")
    ctx = coach_chat.session_context(profile_id, _session(ids[:2]))
    first = ctx["uebungen"][0]
    assert first["nr"] == 0 and first["name"] == names[0] and first["ziel_wdh"] == "8–12"
    assert first["saetze"][0] == {"wdh": 10, "kg": 60, "rpe": 9, "erledigt": True}
    assert first["schritt_kg"] > 0 and ctx["aktuell"] == 0 and ctx["block"]["ziel_rir"] == 2


def test_session_vorschlaege_werden_geprueft(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    session = _session(ids[:2])
    _, actions = coach_chat.split_actions(_block([
        {"typ": "heute_tauschen", "nr": 0, "neu": names[2]},            # ok
        {"typ": "heute_tauschen", "nr": 0, "neu": names[1]},            # schon in der Einheit
        {"typ": "heute_tauschen", "nr": 7, "neu": names[2]},            # gibt es nicht
        {"typ": "saetze", "nr": 0, "gewicht": 57.5, "wdh": None, "saetze": None},   # ok
        {"typ": "saetze", "nr": 0, "gewicht": 9999},                    # Bereich
        {"typ": "saetze", "nr": 0, "saetze": 0},                        # weniger als erledigt
        {"typ": "saetze", "nr": 1},                                     # nichts zu aendern
    ]), profile_id, session)
    assert [(a["type"], a.get("to"), a.get("weight")) for a in actions] == \
        [("session_swap", names[2], None), ("session_sets", None, 57.5)]
    assert actions[0]["unit"] == "kg" and actions[0]["from_id"] == ids[0]
    # Ohne laufende Einheit gibt es keine Knoepfe fuer "heute"
    assert coach_chat.split_actions(_block([{"typ": "heute_tauschen", "nr": 0, "neu": names[2]}]), profile_id)[1] == []


def test_session_vorschlag_aendert_den_plan_nicht(profile_id):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    _, actions = coach_chat.split_actions(_block([
        {"typ": "heute_tauschen", "nr": 0, "neu": names[2]},
        {"typ": "saetze", "nr": 0, "gewicht": 55}]), profile_id, _session(ids[:2]))
    msg = coach_chat.add_message(profile_id, "coach", "x", "session", actions=actions)
    out = routes.post_action(msg["id"], 0, "apply", profile_id)["message"]
    assert [a["state"] for a in out["actions"]] == ["applied", "skipped"]
    assert db.get_row("split_exercises", item)["exercise_id"] == ids[0]
    out = routes.post_action(msg["id"], 0, "undo", profile_id)["message"]
    assert [a["state"] for a in out["actions"]] == ["undone", "open"]
    hist = coach_chat._history(profile_id)[-1]
    assert hist["art"] == "training" and hist["vorschlaege"][0].startswith("heute ")


def test_session_frage_kurz_und_als_training_gespeichert(profile_id, monkeypatch):
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker")
    seen = {}

    def fake(text, system, model, timeout):
        seen["system"], seen["prompt"] = system, text
        yield "done", "Geh auf 57,5 kg."
    monkeypatch.setattr(claude_cli, "stream", fake)
    kind, msg = list(coach_chat.answer(profile_id, "Zu schwer?", _session(ids[:2])))[-1]
    assert kind == "done" and msg["source"] == "session"
    assert "GERADE LÄUFT EINE EINHEIT" in seen["system"] and "laufende_einheit" in seen["prompt"]
    assert {m["source"] for m in coach_chat.messages(profile_id)} == {"session"}


def test_session_zu_gross():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        routes.ChatIn(text="x", session={"exercises": [{"exercise_id": 1}] * 31})


def test_session_vorschlag_bricht_den_kontext_nicht(profile_id):
    """Gespeicherte "nur heute"-Vorschlaege haben keine plan_id – Mustererkennung und Kontext
    muessen sie ueberspringen (sonst scheitert jede weitere Frage)."""
    item, ids, names = _plan_with(profile_id, "Kniebeuge", "Beinstrecker", "Beinpresse")
    _, actions = coach_chat.split_actions(_block([{"typ": "heute_tauschen", "nr": 0, "neu": names[2]}]),
                                          profile_id, _session(ids[:2]))
    coach_chat.add_message(profile_id, "coach", "x", "session", actions=actions)
    assert coach_chat.context(profile_id)["plan"]
