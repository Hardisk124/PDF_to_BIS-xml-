#!/usr/bin/env python3
"""Stiahne UBL 2.1 XSD schemy od OASIS a schemy POHODA od Stormware do priecinka schemas/.

Bez nich bezi len kontrola obchodnych pravidiel. So schemami sa navyse overi,
ze XML zodpoveda strukture UBL aj POHODA.
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
# Schema faktury POHODA a vsetko, co importuje.
ZDROJ_POHODA = "https://www.stormware.cz/schema/version_2/"
SCHEMY_POHODA = ("invoice.xsd", "type.xsd", "documentresponse.xsd", "print.xsd", "filter.xsd")


def stiahni_pohodu() -> int:
    ciel = CIEL / "pohoda"
    ciel.mkdir(parents=True, exist_ok=True)
    for nazov in SCHEMY_POHODA:
        print(f"Stahujem {ZDROJ_POHODA}{nazov} ...")
        try:
            with urllib.request.urlopen(ZDROJ_POHODA + nazov, timeout=60) as odpoved:
                (ciel / nazov).write_bytes(odpoved.read())
        except Exception as chyba:
            print(f"Stiahnutie zlyhalo: {chyba}", file=sys.stderr)
            return 1
    print(f"Hotovo, {len(SCHEMY_POHODA)} schem POHODA v {ciel}.")
    return 0


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
    return stiahni_pohodu()


if __name__ == "__main__":
    raise SystemExit(main())
