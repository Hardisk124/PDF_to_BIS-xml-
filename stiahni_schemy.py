#!/usr/bin/env python3
"""Stiahne UBL 2.1 XSD schemy od OASIS a rozbali ich do priecinka schemas/.

Bez nich bezi len kontrola obchodnych pravidiel. So schemami sa navyse overi,
ze XML zodpoveda UBL strukture.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

KOREN = Path(__file__).resolve().parent
CIEL = KOREN / "schemas"
ZDROJ = "http://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip"
# Lokalna kopia archivu sa pouzije, ak existuje, aby sa nesahovalo opakovane.
LOKALNY = KOREN / "UBL-2.1.zip"


def main() -> int:
    if LOKALNY.exists():
        print(f"Pouzivam lokalny archiv {LOKALNY}")
        data = LOKALNY.read_bytes()
    else:
        print(f"Stahujem {ZDROJ} ...")
        try:
            with urllib.request.urlopen(ZDROJ, timeout=180) as odpoved:
                data = odpoved.read()
        except Exception as chyba:
            print(f"Stiahnutie zlyhalo: {chyba}", file=sys.stderr)
            print("Stiahni UBL-2.1.zip rucne a rozbal obsah xsd/ do priecinka schemas/.", file=sys.stderr)
            return 1

    print(f"Stiahnute {len(data) // 1024} kB, rozbalujem ...")
    pocet = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archiv:
        for zaznam in archiv.namelist():
            if not zaznam.endswith(".xsd"):
                continue
            # V archive su dve vetvy, xsd/ je normativna a xsdrt/ je runtime variant.
            if zaznam.startswith("xsd/"):
                relativna = zaznam[len("xsd/"):]
            elif "/xsd/" in zaznam:
                relativna = zaznam.split("/xsd/", 1)[1]
            else:
                continue
            cielovy = CIEL / relativna
            cielovy.parent.mkdir(parents=True, exist_ok=True)
            cielovy.write_bytes(archiv.read(zaznam))
            pocet += 1

    print(f"Hotovo, {pocet} schem v {CIEL}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
