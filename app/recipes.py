"""Rezepte: Zutaten mit Mengen und Portionen -> ein eigenes Lebensmittel (source='rezept').

Gespeichert wie jedes Lebensmittel (Werte je 100 g, Portion = Gesamtgewicht / Portionen), damit
Suche, Favoriten, "zuletzt verwendet" und das Tagebuch ohne Sonderfall funktionieren. Die
Zutatenliste steht als JSON in foods.recipe und wird beim Speichern neu durchgerechnet.
Gesamtgewicht: Summe der Zutaten, ausser es ist das Gewicht nach dem Kochen angegeben – Nudeln
nehmen Wasser auf, Sossen kochen ein; nur so stimmt "100 g vom fertigen Gericht"."""
import json

from . import db
from .food_catalog import search_text
from .nutrition import DETAILS, NUTRIENTS


class RecipeError(ValueError):
    pass


def compute(items, portions, cooked_g=None):
    """Werte je 100 g und Portionsgewicht aus [{food_id, grams}]."""
    if not items:
        raise RecipeError("Ein Rezept braucht mindestens eine Zutat.")
    totals = {k: 0.0 for k in NUTRIENTS}
    unknown = set()     # Detail-Wert fehlt bei einer Zutat: fuers Rezept unbekannt statt zu niedrig
    raw_g, lines = 0.0, []
    foods = db.rows_by_ids("foods", [it["food_id"] for it in items])
    for it in items:
        food = foods.get(it["food_id"])
        if not food:
            raise RecipeError("Eine Zutat gibt es nicht (mehr).")
        g = float(it["grams"])
        raw_g += g
        for k in NUTRIENTS:
            if food.get(k) is None and k in DETAILS:
                unknown.add(k)
            totals[k] += (food.get(k) or 0) * g / 100
        lines.append({"food_id": food["id"], "name": food["name"], "grams": g,
                      "kcal": round((food.get("kcal") or 0) * g / 100)})
    weight = float(cooked_g) if cooked_g else raw_g
    if weight <= 0:
        raise RecipeError("Das Gesamtgewicht muss größer als 0 sein.")
    per100 = {k: None if k in unknown else round(v * 100 / weight, 2 if k == "salt" else 1)
              for k, v in totals.items()}
    if per100["kcal"] > 950:
        raise RecipeError("Mehr als 950 kcal je 100 g – stimmt das Gewicht nach dem Kochen?")
    return {**per100, "serving_g": round(weight / portions, 1), "serving_label": "Portion",
            "recipe": json.dumps({"portions": portions, "cooked_g": cooked_g, "items": lines,
                                  "total_kcal": round(totals["kcal"])}, ensure_ascii=False)}


def save(name, items, portions, cooked_g=None, recipe_id=None):
    name = (name or "").strip()
    if not name:
        raise RecipeError("Bitte einen Namen eingeben.")
    if recipe_id and any(it["food_id"] == recipe_id for it in items):
        raise RecipeError("Ein Rezept kann sich nicht selbst als Zutat enthalten.")
    data = {"name": name, **compute(items, portions, cooked_g), "source": "rezept"}
    data["search"] = search_text(data)
    if recipe_id:
        return out(db.update_row("foods", recipe_id, data))
    return out(db.insert_row("foods", {**data, "created_at": db.now()}))


def out(food):
    from .portions import units
    food = dict(food)
    food["recipe"] = json.loads(food["recipe"]) if food.get("recipe") else None
    food["units"] = units(food)
    return food
