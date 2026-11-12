"""Muskel-Grafik: jede Katalog-Uebung hat Hauptmuskeln, und alle Schluessel gibt es in der Grafik."""
import re
from pathlib import Path

from app import db, exercise_info

ROOT = Path(__file__).resolve().parent.parent


def test_jede_katalog_uebung_hat_hauptmuskeln(tmp_path, monkeypatch):
    # Frische Datenbank: andere Tests legen eigene Uebungen ohne Muskeln an
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "katalog.db")
    db.init()
    ohne = [r["name"] for r in db.rows(
        "SELECT name FROM exercises WHERE archived=0 AND COALESCE(muscles_primary,'')=''")]
    assert ohne == []


def test_alle_muskelschluessel_gibt_es_in_der_grafik():
    js = (ROOT / "static" / "js" / "muscles.js").read_text()
    # Schluessel der Formen: "name: [[" am Zeilenanfang im SHAPES-Block
    shapes = set(re.findall(r"^  (\w+): \[\[", js, re.M))
    benutzt = set()
    for primary, secondary in exercise_info.MUSKELN.values():
        benutzt |= set(primary.split(",")) | set(filter(None, secondary.split(",")))
    benutzt |= set(exercise_info.GROUP_MUSCLES.values())
    assert benutzt <= set(exercise_info.MUSCLES)
    assert benutzt <= shapes, benutzt - shapes
