#!/usr/bin/env python3
"""Davkovy prevod PDF faktur do Peppol BIS Billing 3.0.

Priklad:
    python3 cli.py                        # vstup/ -> vystup/
    python3 cli.py --vstup ~/faktury/2026 --vlakna 8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from faktury import cesty
from faktury.config import nacitaj
from faktury.pipeline import spracuj_vsetky, zapis, zapis_suhrn
from faktury.validate import xsd_dostupne

KOREN = cesty.data()
ZNACKY = {"ok": "OK   ", "s upozornenim": "UPOZ ", "neplatne": "NEPL ", "chyba": "CHYBA"}


def argumenty() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prevod PDF faktur do Peppol BIS Billing 3.0 (UBL XML).")
    p.add_argument("--vstup", type=Path, default=KOREN / "vstup", help="Priecinok s PDF faktumi.")
    p.add_argument("--vystup", type=Path, default=KOREN / "vystup", help="Priecinok pre XML.")
    p.add_argument("--chyby", type=Path, default=KOREN / "chyby", help="Priecinok pre protokoly o chybach.")
    p.add_argument("--config", type=Path, default=KOREN / "config.toml", help="Konfiguracny subor.")
    p.add_argument("--vlakna", type=int, default=4, help="Kolko faktur spracovat naraz.")
    p.add_argument("--striktne", action="store_true", help="Nezapisovat XML, ktore neprejde validaciou.")
    p.add_argument("--bez-rekurzie", action="store_true", help="Nehladat PDF v podpriecinkoch.")
    return p.parse_args()


def main() -> int:
    args = argumenty()
    cfg = nacitaj(args.config)
    striktne = args.striktne or cfg.nastavenia.striktna_validacia

    if not args.vstup.is_dir():
        print(f"Vstupny priecinok {args.vstup} neexistuje.", file=sys.stderr)
        return 2

    vzor = "*.pdf" if args.bez_rekurzie else "**/*.pdf"
    subory = sorted(p for p in args.vstup.glob(vzor) if p.is_file())
    if not subory:
        print(f"V priecinku {args.vstup} nie su ziadne PDF subory.")
        return 0

    if not xsd_dostupne():
        print("Poznamka: UBL schemy nie su stiahnute, bezi len kontrola obchodnych pravidiel.")
        print("          Stiahnes ich prikazom: python3 stiahni_schemy.py\n")

    print(f"Spracuvam {len(subory)} PDF suborov.\n")

    vysledky = spracuj_vsetky(subory, cfg, args.vlakna)

    for vysledok in vysledky:
        zapis(vysledok, args.vystup, args.chyby, striktne)
        znacka = ZNACKY.get(vysledok.stav, "?????")
        popis = vysledok.cislo_faktury or "bez cisla"
        print(f"{znacka} {vysledok.subor.name}  ->  {popis}")
        for chyba in vysledok.chyby:
            print(f"        chyba: {chyba}")
        for upozornenie in vysledok.upozornenia:
            print(f"        pozn.: {upozornenie.splitlines()[0]}")

    suhrn = args.chyby / "suhrn.json"
    zapis_suhrn(vysledky, suhrn)

    pocty = {stav: sum(1 for v in vysledky if v.stav == stav) for stav in ZNACKY}
    print(
        f"\nHotovo. OK {pocty['ok']}, s upozornenim {pocty['s upozornenim']}, "
        f"neplatnych {pocty['neplatne']}, chybnych {pocty['chyba']}."
    )
    print(f"XML v {args.vystup}, protokoly v {args.chyby}, suhrn v {suhrn}.")

    return 1 if pocty["chyba"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
