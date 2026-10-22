"""Test citania faktury podla pevneho vzoru nad skutocnymi PDF.

Beha nad suborami v priecinku vstup/. Ked tam ziadne nie su, test sa preskoci.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from faktury.config import Config, Nastavenia, Peppol
from faktury.pipeline import spracuj_subor
from faktury.sablona import precitaj
from faktury.ubl import cislo

CFG = Config(
    peppol=Peppol(moj_endpoint_scheme="0158", odberatel_endpoint_scheme="0158"),
    nastavenia=Nastavenia(),
)

# Co musi sediet na kazdej fakture tohto vzoru, nezavisle od konkretneho dokladu.
POVINNE_POLIA = [
    "cislo_faktury", "datum_vystavenia", "datum_splatnosti", "datum_dodania",
    "iban", "bic", "sposob_uhrady", "suma_bez_dph", "suma_dph", "suma_s_dph", "na_uhradu",
]
POVINNE_DODAVATEL = ["nazov", "ico", "dic", "ic_dph", "ulica", "mesto", "psc", "krajina"]
POVINNE_ODBERATEL = ["nazov", "ico", "ic_dph", "ulica", "mesto", "psc", "krajina"]

# Presne hodnoty odpisane z papierovych faktur. Kontrola vnutornej konzistencie
# by nezachytila, keby sa napriklad ulica a mesto prehodili, preto su tu aj tieto.
OCAKAVANE = {
    "fa0%.pdf": {
        "cislo_faktury": "V26/0049",
        "datum_vystavenia": "30.01.2026",
        "datum_splatnosti": "01.03.2026",
        "datum_dodania": "29.01.2026",
        "iban": "SK0575000000004005114339",
        "bic": "CEKOSKBX",
        "sposob_uhrady": "PP",
        "suma_bez_dph": "6724.80",
        "suma_dph": "0.00",
        "na_uhradu": "6724.80",
        "pocet_riadkov": 10,
        "dodavatel": ("GRIMMEL, s.r.o.", "36787965", "SK2022389633", "Obuvnícka 3", "Prievidza", "97101", "SK"),
        "odberatel": ("Dopcon s.r.o", "27966283", "CZ27966283", "Chebská 53", "Sokolov", "35601", "CZ"),
        "prvy_riadok": ("25.502", "t", "20.00", "510.04", "0"),
    },
    "fa23%.pdf": {
        "cislo_faktury": "V25/0770",
        "datum_vystavenia": "14.08.2025",
        "datum_splatnosti": "13.09.2025",
        "datum_dodania": "14.08.2025",
        "iban": "SK0575000000004005114339",
        "bic": "CEKOSKBX",
        "sposob_uhrady": "PP",
        "suma_bez_dph": "6344.64",
        "suma_dph": "1459.27",
        "na_uhradu": "7803.91",
        "pocet_riadkov": 11,
        "dodavatel": ("GRIMMEL, s.r.o.", "36787965", "SK2022389633", "Obuvnícka 3", "Prievidza", "97101", "SK"),
        "odberatel": ("ARIMEX Bratislava spol. s r.o.", "31325076", "SK2020335581",
                      "Vajnorská 100/A", "Bratislava", "83104", "SK"),
        "prvy_riadok": ("25.040", "t", "21.00", "525.84", "23"),
    },
}
POLIA_STRANY = ("nazov", "ico", "ic_dph", "ulica", "mesto", "psc", "krajina")
POLIA_RIADKU = ("mnozstvo", "jednotka", "jednotkova_cena", "suma_bez_dph", "sadzba_dph")


def skontroluj_golden(cesta: Path, fa) -> list[str]:
    """Porovna precitane hodnoty s tym, co je na papierovej fakture."""
    ocakavane = OCAKAVANE.get(cesta.name)
    if not ocakavane:
        return []

    chyby = []
    for pole in ("cislo_faktury", "datum_vystavenia", "datum_splatnosti", "datum_dodania",
                 "iban", "bic", "sposob_uhrady", "suma_bez_dph", "suma_dph", "na_uhradu"):
        skutocne = getattr(fa, pole)
        if skutocne != ocakavane[pole]:
            chyby.append(f"{pole}: precitane {skutocne!r}, ocakavane {ocakavane[pole]!r}")

    if len(fa.riadky) != ocakavane["pocet_riadkov"]:
        chyby.append(f"pocet riadkov {len(fa.riadky)}, ocakavany {ocakavane['pocet_riadkov']}")

    for kto, ocakavana_strana in (("dodavatel", ocakavane["dodavatel"]),
                                  ("odberatel", ocakavane["odberatel"])):
        strana = getattr(fa, kto)
        for pole, hodnota in zip(POLIA_STRANY, ocakavana_strana):
            if getattr(strana, pole) != hodnota:
                chyby.append(f"{kto}.{pole}: precitane {getattr(strana, pole)!r}, ocakavane {hodnota!r}")

    if fa.riadky:
        for pole, hodnota in zip(POLIA_RIADKU, ocakavane["prvy_riadok"]):
            if getattr(fa.riadky[0], pole) != hodnota:
                chyby.append(f"riadok 1 {pole}: precitane {getattr(fa.riadky[0], pole)!r}, ocakavane {hodnota!r}")

    return chyby


def skontroluj_fakturu(cesta: Path) -> list[str]:
    fa = precitaj(cesta)
    chyby: list[str] = skontroluj_golden(cesta, fa)

    for pole in POVINNE_POLIA:
        if not getattr(fa, pole):
            chyby.append(f"chyba pole {pole}")
    for pole in POVINNE_DODAVATEL:
        if not getattr(fa.dodavatel, pole):
            chyby.append(f"dodavatel nema {pole}")
    for pole in POVINNE_ODBERATEL:
        if not getattr(fa.odberatel, pole):
            chyby.append(f"odberatel nema {pole}")

    if not fa.riadky:
        chyby.append("ziadne riadky")
        return chyby

    # Sucet riadkov sa musi presne rovnat zakladu dane uvedenemu na fakture.
    sucet = sum((cislo(r.suma_bez_dph) or Decimal(0)) for r in fa.riadky)
    zaklad = cislo(fa.suma_bez_dph) or Decimal(0)
    if abs(sucet - zaklad) > Decimal("0.01"):
        chyby.append(f"sucet riadkov {sucet} sa nerovna zakladu dane {zaklad}")

    # Kazdy riadok musi mat mnozstvo, cenu aj sumu a musia si navzajom zodpovedat.
    for r in fa.riadky:
        mnozstvo, cena, suma = cislo(r.mnozstvo), cislo(r.jednotkova_cena), cislo(r.suma_bez_dph)
        if mnozstvo is None or cena is None or suma is None:
            chyby.append(f"riadok {r.poradie} ma nekompletne cisla")
            continue
        if abs(mnozstvo * cena - suma) > Decimal("0.01"):
            chyby.append(f"riadok {r.poradie}: {mnozstvo} x {cena} nie je {suma}")
        if not r.nazov:
            chyby.append(f"riadok {r.poradie} nema nazov")

    # Rekapitulacia DPH musi sediet so suhrnom.
    zaklady = sum((cislo(s.zaklad) or Decimal(0)) for s in fa.rekapitulacia_dph)
    if abs(zaklady - zaklad) > Decimal("0.01"):
        chyby.append(f"rekapitulacia {zaklady} sa nerovna zakladu {zaklad}")

    dane = sum((cislo(s.dph) or Decimal(0)) for s in fa.rekapitulacia_dph)
    uvedena = cislo(fa.suma_dph) or Decimal(0)
    if abs(dane - uvedena) > Decimal("0.01"):
        chyby.append(f"DPH v rekapitulacii {dane} sa nerovna uvedenej {uvedena}")

    return chyby


def main() -> int:
    subory = sorted(Path(__file__).resolve().parent.parent.joinpath("vstup").glob("*.pdf"))
    if not subory:
        print("V priecinku vstup/ nie su ziadne PDF, test sa preskakuje.")
        return 0

    zlyhalo = 0
    for cesta in subory:
        chyby = skontroluj_fakturu(cesta)
        print(f"{cesta.name}: {'OK' if not chyby else 'ZLYHALO'}")
        for c in chyby:
            print("    ", c)
        zlyhalo += len(chyby)

        vysledok = spracuj_subor(cesta, CFG)
        stav = "OK" if not vysledok.chyby else "ZLYHALO"
        print(f"    prevod do XML: {stav} ({', '.join(v.nazov for v in vysledok.vystupy)}, {vysledok.cislo_faktury})")
        for c in vysledok.chyby:
            print("     chyba:", c)
        zlyhalo += len(vysledok.chyby)

    print("\nZlyhani:", zlyhalo)
    return 1 if zlyhalo else 0


if __name__ == "__main__":
    raise SystemExit(main())
