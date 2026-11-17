"""Jeder Import zwischen den Frontend-Modulen (static/js) muss einen vorhandenen Export treffen.
Ein einziger fehlender Name legt sonst die ganze App lahm (der Browser lädt dann kein Modul), und es
gibt keine Frontend-Tests, die das bemerken würden."""
import re
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / "static" / "js"
EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)")
EXPORT_LIST = re.compile(r"export\s*\{([^}]*)\}")
IMPORT = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w-]+\.js)"')


def _exports(source):
    names = set(EXPORT.findall(source))
    for group in EXPORT_LIST.findall(source):
        names |= {x.strip().split(" as ")[-1] for x in group.split(",") if x.strip()}
    return names


def test_alle_importe_haben_einen_export():
    exports = {f.name: _exports(f.read_text()) for f in JS.glob("*.js")}
    missing = []
    for f in JS.glob("*.js"):
        for names, module in IMPORT.findall(f.read_text()):
            for name in (x.strip().split(" as ")[0] for x in names.split(",") if x.strip()):
                if name not in exports.get(module, set()):
                    missing.append(f"{f.name}: {name} aus {module}")
    assert not missing, missing
