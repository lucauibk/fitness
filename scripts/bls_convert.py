"""Bundeslebensmittelschluessel (BLS) 4.0 -> app/data/bls_foods.csv

Einmalig auf dem Mac ausfuehren, wenn eine neue BLS-Version erscheint:

    python3 scripts/bls_convert.py ~/Downloads/BLS_4_0_2025_DE/BLS_4_0_Daten_2025_DE.xlsx

Die Excel-Datei (blsdb.de/download, ~20 MB, 138 Naehrstoffe) wird nicht ins Repo gelegt;
uebernommen werden nur die Werte, die die App zeigt. Gelesen wird mit der Standardbibliothek
(eine xlsx ist ein ZIP mit XML), damit weder Mac noch Pi openpyxl brauchen.

Lizenz der Daten: CC BY 4.0. Quelle: Max Rubner-Institut (2025): Bundeslebensmittelschluessel
(BLS), Version 4.0 – Deutsche Naehrstoffdatenbank. DOI 10.25826/Data20251217-134202-0"""
import csv
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "bls_foods.csv"
# Spaltenkopf beginnt mit dem Komponenten-Code des BLS
COLUMNS = {"code": "BLS Code", "name": "Lebensmittelbezeichnung", "kcal": "ENERCC ",
           "protein": "PROT625 ", "carbs": "CHO ", "fat": "FAT ", "fiber": "FIBT ", "sugar": "SUGAR ", "sat_fat": "FASAT ", "salt": "NACL "}


def _rows(path):
    z = zipfile.ZipFile(path)
    shared = [("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
              for si in ET.parse(z.open("xl/sharedStrings.xml")).getroot().findall("m:si", NS)]
    for _, el in ET.iterparse(z.open("xl/worksheets/sheet1.xml"), events=("end",)):
        if not el.tag.endswith("}row"):
            continue
        row = {}
        for c in el.findall("m:c", NS):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            v = c.find("m:v", NS)
            if v is None:
                continue
            row[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
        el.clear()
        yield row


def _num(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def convert(path):
    rows = _rows(path)
    head = next(rows)
    cols = {}
    for key, prefix in COLUMNS.items():
        hit = [c for c, v in head.items() if v and (v == prefix if key in ("code", "name") else v.startswith(prefix))
               and "Datenherkunft" not in v and "Referenz" not in v]
        if len(hit) != 1:
            raise SystemExit(f"Spalte fuer {key} nicht eindeutig gefunden: {hit}")
        cols[key] = hit[0]
    out = []
    for r in rows:
        name = (r.get(cols["name"]) or "").strip()
        kcal = _num(r.get(cols["kcal"]))
        if not name or kcal is None:
            continue
        out.append({"code": r[cols["code"]].strip(), "name": name, "kcal": kcal,
                    **{k: _num(r.get(cols[k])) for k in ("protein", "carbs", "fat", "fiber", "sugar", "sat_fat", "salt")}})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(out)
    return len(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(f"{convert(sys.argv[1])} Lebensmittel -> {OUT}")
