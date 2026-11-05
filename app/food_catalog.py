"""Lebensmittel-Grundstock und Suche wie in grossen Kalorien-Apps.

Open Food Facts kennt fast nur verpackte Produkte mit Barcode – "Apfel", "Haferflocken" oder
"Haehnchenbrust" fehlen oder kommen in zig uneinheitlichen Varianten. Den Grundstock liefert
deshalb der Bundeslebensmittelschluessel (BLS 4.0, ~7.100 Lebensmittel, amtliche Werte),
eingelesen aus app/data/bls_foods.csv (erzeugt von scripts/bls_convert.py).

Quelle: Max Rubner-Institut (2025): Bundeslebensmittelschluessel (BLS), Version 4.0 –
Deutsche Naehrstoffdatenbank. Lizenz CC BY 4.0. DOI 10.25826/Data20251217-134202-0

Die App wird in Oesterreich benutzt: Die Suche kennt die oesterreichischen Begriffe (Topfen,
Paradeiser, Erdaepfel, Semmel, ...) und findet damit auch die deutschen BLS-Namen – und umgekehrt."""
import csv
from functools import lru_cache
from pathlib import Path

CSV = Path(__file__).resolve().parent / "data" / "bls_foods.csv"
FLAG = "bls_4_0_v2"   # v2: gesaettigte Fettsaeuren und Salz
LOG_FLAG = "food_log_details_v1"
DETAILS = ("fiber", "sugar", "sat_fat", "salt")
ATTRIBUTION = ("Nährwerte: Bundeslebensmittelschlüssel (BLS) 4.0, Max Rubner-Institut, CC BY 4.0 · "
               "Produkte: Open Food Facts, ODbL")

# Oesterreichisch -> deutsch (BLS-Bezeichnung). Wortanfaenge in Vergleichsform (siehe norm()).
# Beide Richtungen werden gesucht: "Topfen" findet "Quark", "Quark" findet Topfen-Produkte.
SYNONYMS_AT_DE = {
    "topfen": "quark", "paradeiser": "tomate", "paradeis": "tomate", "erdapfel": "kartoffel",
    "semmel": "weizenbrotchen", "weckerl": "weizenbrotchen", "kipferl": "hornchen", "faschiertes": "hackfleisch",
    "faschierte": "hackfleisch", "marille": "aprikose", "karfiol": "blumenkohl",
    "fisolen": "grune bohnen", "fisole": "grune bohne", "kohlsprossen": "rosenkohl",
    "ribisel": "johannisbeere", "zwetschke": "pflaume", "kren": "meerrettich",
    "melanzani": "aubergine", "schlagobers": "schlagsahne", "kaffeeobers": "kaffeesahne",
    "obers": "sahne", "sauerrahm": "saure sahne", "eierschwammerl": "pfifferling",
    "schwammerl": "pilz", "hauptelsalat": "kopfsalat", "vogerlsalat": "feldsalat",
    "kukuruz": "mais", "rote ruben": "rote bete", "rote rube": "rote bete", "karotte": "mohre",
    "weichsel": "sauerkirsche", "powidl": "pflaumenmus", "germ": "hefe", "brosel": "paniermehl",
    "hendl": "hahnchen", "truthahn": "pute", "beiried": "roastbeef", "lungenbraten": "filet",
    "selchfleisch": "kasseler", "geselchtes": "kasseler", "leberkase": "fleischkase",
    "extrawurst": "fleischwurst", "krapfen": "berliner", "palatschinke": "pfannkuchen",
    "knodel": "kloss", "nockerl": "spatzle", "gugelhupf": "napfkuchen", "pfefferoni": "peperoni",
    "zuckerl": "bonbon", "rindsuppe": "rinderbruhe", "fleischlaberl": "frikadelle",
    "golatsche": "plunder", "schwarzbeere": "heidelbeere", "jogurt": "joghurt",
    "fruhstucksspeck": "bauchspeck", "kochsalat": "romanasalat", "polenta": "maisgriess",
    "staubzucker": "puderzucker", "kristallzucker": "zucker", "topfengolatsche": "quarkplunder",
    "kaiserschmarrn": "kaiserschmarren", "hendlbrust": "hahnchen brustfilet",
    "huttenkase": "korniger frischkase",
}
# Alltagswoerter -> BLS-Bezeichnung (beide deutsch): so heisst es im Laden, nicht im BLS.
EVERYDAY = {
    "nudeln": "teigwaren", "pasta": "teigwaren", "spaghetti": "teigwaren", "penne": "teigwaren",
    "hahnchenbrust": "hahnchen brustfilet", "huhnerbrust": "hahnchen brustfilet",
    "putenbrust": "pute brust", "cottage cheese": "korniger frischkase", "magerquark": "speisequark magerstufe",
    "haferflocken": "hafer flocken", "brotchen": "weizenbrotchen",
    "schinken gekocht": "kochschinken", "gekochter schinken": "kochschinken",
    "beinschinken": "kochschinken", "schinkenspeck": "rohschinken",
}
# Nur als ganzes Wort – sonst wuerde aus "Eis" ein "Huehnereis".
WHOLE_WORDS = {"ei": "huhnerei", "eier": "huhnerei", "eiklar": "huhnerei eiklar",
               "quark": "speisequark", "eis": "speiseeis", "milch": "vollmilch",
               # in Oesterreich ist "Schinken" ohne Zusatz der gekochte (Beinschinken)
               "schinken": "kochschinken"}


@lru_cache(maxsize=1)
def _pairs():
    """[(oesterreichisch/alltag, BLS-deutsch)] in Vergleichsform, laengste zuerst – sonst wuerde
    "obers" in "schlagobers" vor "schlagobers" selbst ersetzt."""
    from .nutrition import norm
    pairs = [(norm(a), norm(d)) for a, d in (*SYNONYMS_AT_DE.items(), *EVERYDAY.items())]
    return tuple(sorted({(a, d) for a, d in pairs if a and d and a != d}, key=lambda p: -len(p[0])))


def variants(query):
    """Die Suchanfrage plus ihre Uebersetzungen. Jede Variante entsteht aus der Originalanfrage
    (nicht aus einer schon uebersetzten), sonst wuerde z. B. "schinken gekocht" zweimal ersetzt.
    Ersetzt wird auch in Zusammensetzungen: "erdapfelsalat" wird zu "kartoffelsalat"."""
    from .nutrition import norm
    q = norm(query)
    whole = lambda text: " ".join(WHOLE_WORDS.get(w, w) for w in text.split())   # noqa: E731
    to_de, to_at = q, q
    for at, de in _pairs():
        if at in to_de:
            to_de = to_de.replace(at, de)
        if len(de) >= 4 and de in to_at:
            to_at = to_at.replace(de, at)
    return {q, whole(q), to_de, whole(to_de), to_at}


def canonical(query):
    """Die Anfrage mit Ganzwort-Zuordnungen (Schinken -> Kochschinken), sonst None.
    Wer nur "Schinken" tippt, meint den ueblichen – nicht "Gefuellter Schinken"."""
    from .nutrition import norm
    q = norm(query)
    c = " ".join(WHOLE_WORDS.get(w, w) for w in q.split())
    return c if c != q else None


def search_text(food):
    """Vorberechnete Vergleichsform fuer die Suche (Name + Marke)."""
    from .nutrition import norm
    return norm(f"{food.get('name') or ''} {food.get('brand') or ''}")


def matches(hay, query, vs=None):
    """Alle Suchwoerter kommen vor – in der Anfrage selbst oder in einer ihrer Uebersetzungen.
    Auch zusammengeschrieben: "haferflocken" findet "Hafer Flocken". vs: vorberechnete variants()."""
    joined = hay.replace(" ", "")
    return any(v and all(w in hay or w in joined for w in v.split()) for v in (vs or variants(query)))


def _bls_rows():
    with CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            num = {k: (float(r[k]) if r[k] not in ("", None) else None)
                   for k in ("kcal", "protein", "carbs", "fat", *DETAILS)}
            yield {"bls_code": r["code"], "name": r["name"], **num}


def apply(c):
    """BLS einlesen – einmal je Version. Vorhandene Zeilen (gleicher BLS-Code) werden
    aktualisiert, damit Tagebuch-Eintraege ihre food_id behalten."""
    if c.execute("SELECT 1 FROM settings WHERE key=?", (FLAG,)).fetchone():
        return
    rows = list(_bls_rows())
    c.executemany(
        "INSERT INTO foods (bls_code, name, kcal, protein, carbs, fat, fiber, sugar, sat_fat, salt,"
        " source, search, created_at) VALUES (:bls_code, :name, :kcal, :protein, :carbs, :fat, :fiber,"
        " :sugar, :sat_fat, :salt, 'bls', :search, datetime('now'))"
        " ON CONFLICT(bls_code) DO UPDATE SET name=excluded.name, kcal=excluded.kcal,"
        " protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat, fiber=excluded.fiber,"
        " sugar=excluded.sugar, sat_fat=excluded.sat_fat, salt=excluded.salt, search=excluded.search",
        [{**r, "search": search_text(r)} for r in rows])
    # Aeltere Lebensmittel (Open Food Facts, eigene) bekommen ihre Suchform nachgetragen
    for fid, name, brand in c.execute("SELECT id, name, brand FROM foods WHERE search IS NULL").fetchall():
        c.execute("UPDATE foods SET search=? WHERE id=?", (search_text({"name": name, "brand": brand}), fid))
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, datetime('now'))", (FLAG,))


def backfill_log(c):
    """Einmalig: Tagebuch-Eintraege von vor den Detail-Naehrwerten bekommen Ballaststoffe, Zucker,
    gesaettigte Fette und Salz aus ihrem Lebensmittel nachgetragen (nur wo noch nichts steht und das
    Lebensmittel den Wert kennt). Schnelleintraege ohne Lebensmittel bleiben unbekannt (NULL)."""
    if c.execute("SELECT 1 FROM settings WHERE key=?", (LOG_FLAG,)).fetchone():
        return
    for k in DETAILS:
        digits = 2 if k == "salt" else 1     # wie nutrition.portion()
        c.execute(f"UPDATE food_log SET {k} = ROUND((SELECT f.{k} FROM foods f WHERE f.id = food_log.food_id)"
                  f" * grams / 100, {digits}) WHERE {k} IS NULL AND food_id IS NOT NULL AND grams > 0")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, datetime('now'))", (LOG_FLAG,))
