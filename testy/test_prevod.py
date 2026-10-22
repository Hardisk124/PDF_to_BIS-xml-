"""Test jadra bez volania API: Faktura -> UBL -> validacia."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from faktury.config import Config, Nastavenia, Peppol
from faktury.ubl import cislo, d_cena, datum, do_ubl, kod_jednotky, kod_krajiny, kod_uhrady
from faktury.validate import skontroluj, skontroluj_xsd, xsd_dostupne
from faktury.ubl import ChybaPrevodu
from testy.vzorka import VSETKY

# Tieto vzorky musia prevod zastavit, nie prejst.
MUSIA_ZLYHAT = {"zaloha_a_nesedi_sucet": "sa nezhoduje so zakladom dane"}

CFG = Config(
    peppol=Peppol(moj_endpoint_scheme="0158", odberatel_endpoint_scheme="0158"),
    nastavenia=Nastavenia(),
)


def test_normalizacia() -> list[str]:
    chyby = []
    pripady = [
        ("1 234,56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
        ("861.00", "861.00"), ("-246,00", "-246.00"), ("12,50 EUR", "12.50"),
        ("", None), (None, None), ("-", None),
    ]
    for vstup, ocakavane in pripady:
        vysledok = cislo(vstup)
        skutocne = None if vysledok is None else str(vysledok)
        if skutocne != ocakavane:
            chyby.append(f"cislo({vstup!r}) = {skutocne}, ocakavane {ocakavane}")

    for vstup, ocakavane in [("15.03.2026", "2026-03-15"), ("1.1.2026", "2026-01-01"),
                             ("2026-03-15", "2026-03-15"), ("nezmysel", None)]:
        if datum(vstup) != ocakavane:
            chyby.append(f"datum({vstup!r}) = {datum(vstup)}, ocakavane {ocakavane}")

    for vstup, ocakavane in [("hod", "HUR"), ("ks", "H87"), ("deň", "DAY"), ("t", "TNE"),
                             ("m2", "MTK"), ("cosi ine", "C62"), (None, "C62")]:
        if kod_jednotky(vstup) != ocakavane:
            chyby.append(f"kod_jednotky({vstup!r}) = {kod_jednotky(vstup)}, ocakavane {ocakavane}")

    for vstup, ocakavane in [("PP", "30"), ("prevodom", "30"), ("hotovosť", "10"), (None, "1")]:
        if kod_uhrady(vstup, False) != ocakavane:
            chyby.append(f"kod_uhrady({vstup!r}) = {kod_uhrady(vstup, False)}, ocakavane {ocakavane}")

    for vstup, ocakavane in [("SK", "SK"), ("Česká republika", "CZ"), ("Österreich", "AT"),
                             ("SVK", "SK"), ("Narnia", None), (None, None)]:
        if kod_krajiny(vstup) != ocakavane:
            chyby.append(f"kod_krajiny({vstup!r}) = {kod_krajiny(vstup)}, ocakavane {ocakavane}")

    from decimal import Decimal
    for vstup, ocakavane in [(Decimal("20"), "20.00"), (Decimal("20.5"), "20.50"),
                             (Decimal("0.1234"), "0.1234"), (None, "0.00")]:
        if d_cena(vstup) != ocakavane:
            chyby.append(f"d_cena({vstup}) = {d_cena(vstup)}, ocakavane {ocakavane}")
    return chyby


def main() -> int:
    zlyhalo = 0

    chyby = test_normalizacia()
    print("normalizacia:", "OK" if not chyby else "ZLYHALO")
    for c in chyby:
        print("   ", c)
    zlyhalo += len(chyby)

    vystup = Path(__file__).resolve().parent / "vystup"
    vystup.mkdir(exist_ok=True)

    for nazov, tvorca in VSETKY.items():
        ocakavana_chyba = MUSIA_ZLYHAT.get(nazov)
        try:
            vysledok = do_ubl(tvorca(), CFG)
        except ChybaPrevodu as chyba:
            if ocakavana_chyba and ocakavana_chyba in str(chyba):
                print(f"{nazov}: OK  (spravne odmietnute)")
            else:
                print(f"{nazov}: ZLYHALO - neocakavana ChybaPrevodu: {chyba}")
                zlyhalo += 1
            continue
        except Exception as chyba:
            print(f"{nazov}: ZLYHALO pri prevode - {type(chyba).__name__}: {chyba}")
            zlyhalo += 1
            continue

        if ocakavana_chyba:
            print(f"{nazov}: ZLYHALO - prevod mal skoncit chybou, ale presiel")
            zlyhalo += 1
            continue

        (vystup / f"{nazov}.xml").write_bytes(vysledok.xml)
        chyby = skontroluj(vysledok.xml) + skontroluj_xsd(vysledok.xml)
        stav = "OK" if not chyby else "ZLYHALO"
        print(f"{nazov}: {stav}  ({len(vysledok.xml)} B)")
        for c in chyby:
            print("    chyba:", c)
        for u in vysledok.upozornenia:
            print("    pozn.:", u)
        zlyhalo += len(chyby)

    print("\nXSD schemy:", "k dispozicii" if xsd_dostupne() else "nestiahnute, XSD kontrola preskocena")
    print("Zlyhani:", zlyhalo)
    return 1 if zlyhalo else 0


if __name__ == "__main__":
    raise SystemExit(main())
