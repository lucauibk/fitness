"""Open-Food-Facts-Produkte von vor den Detail-Naehrwerten: Ballaststoffe, Zucker, gesaettigte Fette
und Salz einmal nachladen und in die schon eingetragenen Mahlzeiten uebernehmen.

Auf dem Pi:  .venv/bin/python scripts/off_details.py

Fragt je Produkt mit Barcode, dem noch alle vier Werte fehlen, Open Food Facts ab (nur der Barcode
wird uebertragen) und ergaenzt nur fehlende Werte – nichts Vorhandenes wird ueberschrieben."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, food_catalog, nutrition  # noqa: E402


def main():
    db.init()
    todo = db.rows("SELECT * FROM foods WHERE source='off' AND barcode IS NOT NULL"
                   " AND fiber IS NULL AND sugar IS NULL AND sat_fat IS NULL AND salt IS NULL")
    filled = 0
    for food in todo:
        try:
            found = nutrition.product_off(food["barcode"])
        except nutrition.FoodError as e:
            print(f"  {food['name']}: {e}")
            continue
        if found:
            before = {k: food[k] for k in nutrition.DETAILS}
            after = nutrition.remember(found)      # ergaenzt nur fehlende Detail-Werte
            if any(after[k] is not None and before[k] is None for k in nutrition.DETAILS):
                filled += 1
        time.sleep(3)       # Open Food Facts drosselt schon bei 2 Anfragen pro Sekunde
    with db.connect() as c:
        c.execute("DELETE FROM settings WHERE key=?", (food_catalog.LOG_FLAG,))
        food_catalog.backfill_log(c)
    print(f"{filled} von {len(todo)} Produkten ergänzt, Tagebuch nachgetragen.")


if __name__ == "__main__":
    main()
