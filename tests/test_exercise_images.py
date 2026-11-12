"""Bewegungs-Bilder der Uebungen (app/exercise_images.py): jede Zuordnung zeigt auf zwei echte Dateien."""
from app import db, exercise_images


def test_jede_zuordnung_hat_beide_bilder():
    fehlend = [n for n, slug in exercise_images.SLUGS.items()
               if not all((exercise_images.IMG_DIR / f"{slug}-{i}.webp").is_file() for i in (0, 1))]
    assert fehlend == []


def test_zugeordnete_uebungen_existieren_im_katalog():
    # Ein Tippfehler im Namen wuerde die Zuordnung still ins Leere laufen lassen
    namen = {r["name"] for r in db.rows("SELECT name FROM exercises")}
    assert set(exercise_images.SLUGS) <= namen


def test_images_for_start_und_ende_oder_leer():
    assert exercise_images.images_for("Bankdrücken") == [
        "/img/ex/barbell_bench_press_-_medium_grip-0.webp", "/img/ex/barbell_bench_press_-_medium_grip-1.webp"]
    assert exercise_images.images_for("Pendulum Squat") == []        # keine passende Vorlage
    assert exercise_images.images_for("Eigene Übung") == []
