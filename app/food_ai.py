"""Mahlzeit per Text oder Foto erkennen – ueber `claude -p` mit dem Claude-Abo (kein API-Guthaben).

Claude zerlegt die Mahlzeit in einzelne Lebensmittel mit geschaetzter Menge und je einem
Suchbegriff. Die Naehrwerte kommen danach aus der eigenen Datenbank (BLS, Produkte) ueber dieselbe
Suche wie beim Tippen – Claudes eigene kcal-Schaetzung dient nur als Rueckfall, wenn nichts passt.
Eingetragen wird erst, wenn die Person die Liste in der App bestaetigt."""
import json
import logging
import os
import re
import threading

from . import claude_cli, nutrition

log = logging.getLogger(__name__)
MODEL = os.environ.get("FOOD_AI_MODEL", "claude-sonnet-5-5")
TIMEOUT = 120
MAX_ITEMS = 12
CANDIDATES = 4
_busy = threading.Lock()

SYSTEM = """Du bist ein Ernährungs-Assistent in einer Kalorien-App in Österreich. Die Person \
beschreibt oder fotografiert, was sie gegessen hat. Zerlege das in einzelne Lebensmittel und \
schätze die Mengen realistisch (österreichische Portionen: 1 Semmel ≈ 60 g, 1 Scheibe Brot ≈ 50 g, \
1 TL Butter ≈ 5 g, 1 Scheibe Schinken ≈ 15 g, 1 Ei ≈ 58 g, 1 Teller Nudeln gekocht ≈ 250 g).

Antworte NUR mit JSON in diesem Format, ohne Text davor oder danach:
{"items": [{"name": "Semmel", "suche": "Weizenbrötchen", "gramm": 60, "menge": "1 Stück", \
"kcal": 168, "protein": 6, "carbs": 32, "fat": 1}], "hinweis": null}

- "name": wie die Person es nennen würde (österreichisch ist ok)
- "suche": ein einfacher deutscher Suchbegriff für eine Nährwert-Datenbank (Bundeslebensmittel-\
schlüssel), z. B. "Weizenbrötchen", "Butter", "Schinken gekocht", "Hähnchen Brustfilet", \
"Teigwaren gekocht", "Speisequark Magerstufe". Bei erkennbarer Marke: "Marke Produkt".
- "gramm": geschätzte essbare Menge in Gramm (Getränke in ml)
- "menge": die Menge in Worten
- kcal/protein/carbs/fat: deine Schätzung für genau diese Menge (nur Rückfall)
- "hinweis": ein kurzer Satz, wenn etwas unsicher ist (z. B. "Soße geschätzt"), sonst null
Zusammengesetzte Gerichte (Pizza, Schnitzel mit Pommes) zerlegst du in sinnvolle Teile, \
außer es gibt sie als Ganzes in der Datenbank (z. B. "Kaiserschmarren", "Wiener Schnitzel"). \
Ist auf dem Foto kein Essen, gib {"items": [], "hinweis": "Kein Essen erkannt."} zurück."""


class Busy(Exception):
    pass


def _json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("Keine verwertbare Antwort.")
    data = json.loads(m.group(0))
    if not isinstance(data.get("items"), list):
        raise ValueError("Keine verwertbare Antwort.")
    return data


def _num(v, lo=0, hi=5000):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, 1) if lo <= v <= hi else None


def _candidates(item, profile_id, search):
    """Treffer aus der eigenen Datenbank – erst mit Claudes Suchbegriff, dann mit dem Namen."""
    seen, out = set(), []
    for q in (item.get("suche"), item.get("name")):
        if not q or len(str(q).strip()) < 2:
            continue
        for f in search(str(q).strip()[:80], profile_id):
            if f["id"] not in seen:
                seen.add(f["id"])
                out.append({k: f.get(k) for k in ("id", "name", "brand", "kcal", "protein", "carbs", "fat",
                                                   "source", "units")})
        if len(out) >= CANDIDATES:
            break
    return out[:CANDIDATES]


def recognize(profile_id, text, image, search):
    """-> {"items": [...], "hinweis": str|None}. image: (media_type, base64) oder None.
    search: Suchfunktion wie routes.nutrition._local (q, profile_id) -> Lebensmittel."""
    if not _busy.acquire(blocking=False):
        raise Busy()
    try:
        prompt = (text or "").strip() or "Was ist auf dem Foto? Liste die Lebensmittel."
        raw = claude_cli.complete(prompt, SYSTEM, MODEL, TIMEOUT, images=[image] if image else None)
    finally:
        _busy.release()
    data = _json(raw)
    items = []
    for it in data["items"][:MAX_ITEMS]:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        grams = _num(it.get("gramm"), 1, 3000) or 100
        estimate = {k: _num(it.get(k), 0, 5000) for k in ("kcal", "protein", "carbs", "fat")}
        cands = _candidates(it, profile_id, search)
        items.append({"name": str(it["name"])[:80], "menge": str(it.get("menge") or "")[:40],
                      "grams": grams, "estimate": estimate, "candidates": cands,
                      "kcal_found": round(nutrition.portion(cands[0], grams)["kcal"]) if cands else None})
    hint = data.get("hinweis")
    return {"items": items, "hinweis": str(hint)[:200] if hint else None}
