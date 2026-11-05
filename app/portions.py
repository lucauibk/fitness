"""Natuerliche Portionen: "1 Stueck Apfel", "2 Scheiben Brot", "1 EL Oel" statt Gramm.

Der BLS liefert nur Werte je 100 g. Die Portionsgroessen hier sind uebliche Richtwerte
(Haushaltsmasse und mittlere Stueckgewichte, essbarer Anteil), wie sie Kalorien-Apps und
Ernaehrungstabellen verwenden. Sie gelten fuer allgemeine Lebensmittel (BLS) und eigene ohne
Portion; Markenprodukte bringen ihre Portion aus Open Food Facts mit.

Regeln: (Muster auf den Namen in Vergleichsform, [(Einheit, Gramm), ...]). Die erste passende
Regel gewinnt, also stehen spezielle Muster vor allgemeinen. Fluessigkeiten in ml = g."""
import re

from .nutrition import norm

RULES = [
    # Obst
    (r"^apfel( |$)(?!.*(saft|mus|kuchen|strudel|essig|wein|chips))", [("Stück (mittel)", 150), ("Stück (klein)", 110), ("Stück (groß)", 200)]),
    (r"^banane( |$)(?!.*(saft|chips))", [("Stück (mittel)", 120), ("Stück (groß)", 150)]),
    (r"^birne( |$)", [("Stück (mittel)", 160)]),
    (r"^(orange|apfelsine)( |$)", [("Stück (mittel)", 180)]),
    (r"^mandarine( |$)", [("Stück", 70)]),
    (r"^kiwi( |$)", [("Stück", 75)]),
    (r"^pfirsich( |$)|^nektarine( |$)", [("Stück", 130)]),
    (r"^aprikose( |$)", [("Stück", 40)]),
    (r"^pflaume( |$)", [("Stück", 35)]),
    (r"^erdbeere( |$)", [("Handvoll", 80), ("Schale (250 g)", 250)]),
    (r"^(heidelbeere|himbeere|brombeere|johannisbeere)( |$)", [("Handvoll", 60), ("Schale (125 g)", 125)]),
    (r"^weintraube( |$)|^traube( |$)", [("Handvoll", 80)]),
    (r"^avocado( |$)", [("Stück (ohne Kern)", 140), ("halbe", 70)]),
    (r"^mango( |$)", [("Stück (ohne Kern)", 300)]),
    (r"^ananas( |$)", [("Scheibe", 80)]),
    (r"^(wassermelone|honigmelone|melone)( |$)", [("Spalte", 200)]),
    # Gemuese
    (r"^tomate (roh|frisch)|^tomate$", [("Stück (mittel)", 100), ("Cocktailtomate", 15)]),
    (r"^gurke( |$)|^salatgurke", [("Stück", 400), ("Scheibe", 5)]),
    (r"^paprika( |$)(?!.*pulver)", [("Stück", 160)]),
    (r"^karotte|^mohre( |$)", [("Stück", 80)]),
    (r"^zwiebel( |$)", [("Stück (mittel)", 80)]),
    (r"^knoblauch( |$)", [("Zehe", 4)]),
    (r"^kartoffel (geschalt|ungeschalt|gekocht|roh)", [("Stück (mittel)", 90), ("Stück (groß)", 150)]),
    (r"^zucchini( |$)", [("Stück", 250)]),
    (r"^brokkoli( |$)|^blumenkohl( |$)", [("Portion", 200), ("Röschen", 15)]),
    (r"^(kopfsalat|eisbergsalat|feldsalat|rucola|romanasalat)", [("Portion", 50)]),
    (r"^champignon", [("Stück", 20), ("Portion", 150)]),
    (r"^mais( |$)(?!.*(mehl|griess|ol))", [("EL", 15), ("Dose (abgetropft)", 140)]),
    # Brot und Getreide
    (r"^(weizenbrotchen|brotchen|roggenbrotchen|vollkornbrotchen|laugenbrezel|brezel)", [("Stück", 60)]),
    (r"^(croissant|hornchen)", [("Stück", 60)]),
    (r"brot( |$)(?!.*(chen|aufstrich|krume|teig))", [("Scheibe", 50), ("Scheibe (dünn)", 30)]),
    (r"^toastbrot|^weizentoast|^toast( |$)", [("Scheibe", 25)]),
    (r"^knackebrot", [("Scheibe", 10)]),
    (r"^hafer flocken|flocken|^musli|^muesli|^granola", [("EL", 10), ("Portion", 50)]),
    (r"^(reis|teigwaren|nudeln|couscous|bulgur|quinoa|hirse).*roh", [("Portion (roh)", 70), ("EL (roh)", 15)]),
    (r"^(reis|teigwaren|nudeln|couscous|bulgur|quinoa|hirse).*(gekocht|gegart)", [("Portion", 200), ("EL", 25)]),
    (r"mehl( |$)", [("EL", 10), ("Tasse", 120)]),
    # Eier, Milchprodukte
    (r"^huhnerei (roh|gekocht|hart|weich|frisch)|^huhnerei$", [("Stück (M)", 58), ("Stück (L)", 68), ("Stück (S)", 48)]),
    (r"^huhnerei eiklar", [("Stück", 33)]),
    (r"^huhnerei eigelb", [("Stück", 18)]),
    (r"^(vollmilch|milch|h vollmilch|buttermilch)( |$)", [("Glas (200 ml)", 200), ("Tasse (150 ml)", 150), ("Schuss", 30)]),
    (r"^(joghurt|skyr)( |$)", [("Becher (150 g)", 150), ("Becher (500 g)", 500), ("EL", 20)]),
    (r"^speisequark|^quark( |$)|^korniger frischkase", [("Becher (250 g)", 250), ("EL", 30), ("Portion", 125)]),
    (r"^(schlagsahne|sahne|saure sahne|schmand|creme fraiche)", [("EL", 15), ("Becher (200 g)", 200)]),
    (r"^butter( |$)|^butterschmalz|^margarine", [("TL", 5), ("EL", 12), ("Portion (Brot)", 10)]),
    (r"^mozzarella", [("Kugel (125 g)", 125), ("Scheibe", 20)]),
    (r"kase( |$)|^(gouda|emmentaler|edamer|tilsiter|bergkase|parmesan)", [("Scheibe", 20), ("EL gerieben", 7)]),
    # Fleisch, Wurst, Fisch
    (r"^hahnchen brustfilet|^pute brust|^putenbrust", [("Stück (Filet)", 150), ("Portion", 125)]),
    (r"schnitzel", [("Stück", 150)]),
    (r"^(schinken|kochschinken|fleischwurst|salami|mortadella|lyoner|leberwurst|bierschinken)", [("Scheibe", 15)]),
    (r"^(wiener|frankfurter|bockwurst|wurstchen)", [("Stück", 50)]),
    (r"^(bratwurst|weisswurst|krakauer)", [("Stück", 100)]),
    (r"hackfleisch.*roh", [("Portion", 125), ("Packung", 500)]),
    (r"^(lachs|thunfisch|forelle|kabeljau|seelachs|zander)", [("Filet", 125), ("Dose (abgetropft)", 140)]),
    # Fette, Aufstriche, Suessungen
    (r"ol( |$)(?!.*(sardine|fisch|kern))|^olivenol|^rapsol|^sonnenblumenol", [("TL", 4), ("EL", 10)]),
    (r"^(honig|marmelade|konfiture|nutella|nuss nougat|erdnussbutter|erdnusscreme)", [("TL", 7), ("EL", 20), ("Portion (Brot)", 15)]),
    (r"^zucker( |$)|^puderzucker|^rohrzucker", [("TL", 4), ("EL", 12), ("Stück Würfel", 3)]),
    (r"^(mandel|walnuss|haselnuss|cashew|erdnuss|pistazie|nusse|nussmischung)(?!.*(milch|mus|butter|creme))", [("Handvoll", 30), ("EL gehackt", 8)]),
    (r"^(ketchup|tomatenketchup|senf|mayonnaise|sojasosse|sojasauce)", [("TL", 5), ("EL", 15)]),
    # Getraenke
    (r"saft( |$)|^limonade|^cola|^eistee|^nektar", [("Glas (250 ml)", 250), ("Flasche (500 ml)", 500)]),
    (r"^(bier|pils|weissbier|radler)", [("Krügerl (0,5 l)", 500), ("Seidl (0,3 l)", 300)]),
    (r"^(wein|rotwein|weisswein|sekt)", [("Achtel (125 ml)", 125), ("Viertel (250 ml)", 250)]),
    (r"^(kaffee|espresso|cappuccino|latte macchiato)", [("Tasse", 150), ("Espresso", 30)]),
    # Suesses
    (r"schokolade( |$)", [("Riegel/Rippe", 5), ("Tafel (100 g)", 100)]),
    (r"^(speiseeis|eiscreme)", [("Kugel", 50)]),
    (r"kuchen|torte|strudel", [("Stück", 120)]),
    (r"^(keks|butterkeks|kekse)", [("Stück", 8)]),
    # Gerichte allgemein
    (r"suppe|eintopf", [("Teller", 300), ("Schale", 250)]),
    (r"knodel|kloss", [("Stück", 80)]),
    (r"kaiserschmarren", [("Portion", 250)]),
    (r"pfannkuchen|palatschinke", [("Stück", 80), ("Portion", 240)]),
]
_COMPILED = [(re.compile(p), units) for p, units in RULES]


def rule_units(name):
    n = norm(name)
    for pattern, units in _COMPILED:
        if pattern.search(n):
            return [{"label": label, "g": g} for label, g in units]
    return []


def units(food):
    """Moegliche Einheiten fuer die Mengen-Eingabe: [{label, g}], die wichtigste zuerst.
    Markenprodukte: ihre Portion und Packung (Open Food Facts). Allgemeine und eigene
    Lebensmittel ohne Portion: die Regeln oben."""
    out = []
    if food.get("serving_g"):
        out.append({"label": food.get("serving_label") or "Portion", "g": food["serving_g"]})
    if food.get("pack_g") and food["pack_g"] <= 1000:
        out.append({"label": "Packung", "g": food["pack_g"]})
    if food.get("source") in ("bls", "eigen", "rezept", None) and not food.get("serving_g"):
        out += rule_units(food.get("name"))
    seen, unique = set(), []
    for u in out:
        if u["g"] and u["label"] not in seen:
            seen.add(u["label"])
            unique.append({"label": u["label"], "g": round(float(u["g"]), 1)})
    return unique


def with_units(foods):
    for f in foods:
        f["units"] = units(f)
    return foods
