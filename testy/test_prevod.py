"""Test jadra bez volania API: Faktura -> UBL -> validacia."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from faktury.config import Config, Nastavenia, Peppol
from lxml import etree

from faktury.ubl import (
    NS_CAC, NS_CBC, Firma, cislo, d_cena, datum, do_ubl, endpoint_podla_schemy, kod_jednotky, kod_krajiny, kod_uhrady,
)
from faktury.validate import skontroluj, skontroluj_xsd, xsd_dostupne
from faktury.ubl import ChybaPrevodu
from testy.vzorka import VSETKY

NS = {"cac": NS_CAC, "cbc": NS_CBC}

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
                             ("SVK", "SK"), ("Maďarská republika", "HU"), ("Narnia", None), (None, None)]:
        if kod_krajiny(vstup) != ocakavane:
            chyby.append(f"kod_krajiny({vstup!r}) = {kod_krajiny(vstup)}, ocakavane {ocakavane}")

    from decimal import Decimal
    for vstup, ocakavane in [(Decimal("20"), "20.00"), (Decimal("20.5"), "20.50"),
                             (Decimal("0.1234"), "0.1234"), (None, "0.00")]:
        if d_cena(vstup) != ocakavane:
            chyby.append(f"d_cena({vstup}) = {d_cena(vstup)}, ocakavane {ocakavane}")
    return chyby


def test_endpoint() -> list[str]:
    """EndpointID podla EAS kodu: 0245 = DIC bez SK, 0158 = ICO, 9950 = IC DPH."""
    firma = Firma(ico="36787965", dic="2022389633", ic_dph="SK2022389633")
    chyby = []
    for schema, ocakavane in (("0245", "2022389633"), ("0158", "36787965"), ("9950", "SK2022389633"), ("", "SK2022389633")):
        if endpoint_podla_schemy(firma, schema) != ocakavane:
            chyby.append(f"endpoint pre {schema!r} = {endpoint_podla_schemy(firma, schema)}, ocakavane {ocakavane}")
    return chyby


def test_bis_851() -> list[str]:
    """Skutocna faktura 851 podla vzoru V260837A.xml: EndpointID, ICO 0158, zapis v OR, variabilny symbol."""
    pdf = Path(__file__).resolve().parent.parent / "vstup" / "851.pdf"
    if not pdf.exists():
        print("   vstup/851.pdf chyba, preskakuje sa")
        return []
    from faktury.sablona import precitaj

    # Rucne zadany EndpointID s predponou SK sa pri 0245 musi orezat na DIC.
    cfg = Config(peppol=Peppol(moj_endpoint_id="SK2022389633", moj_endpoint_scheme="0245",
                               odberatel_endpoint_scheme="0158"), nastavenia=Nastavenia())
    xml = do_ubl(precitaj(pdf), cfg).xml
    chyby = [f"851.pdf: {c}" for c in skontroluj(xml) + skontroluj_xsd(xml)]
    bis = etree.fromstring(xml)
    for strana, ocakavane in (("Supplier", ("0245", "2022389633")), ("Customer", ("0158", "31686184"))):
        e = bis.find(f"cac:Accounting{strana}Party/cac:Party/cbc:EndpointID", NS)
        if (e.get("schemeID"), e.text) != ocakavane:
            chyby.append(f"851.pdf: EndpointID {strana} je {e.get('schemeID')}:{e.text}, ocakavane {ocakavane}")
    ico = bis.findall(".//cac:PartyIdentification/cbc:ID", NS) + bis.findall(".//cac:PartyLegalEntity/cbc:CompanyID", NS)
    if len(ico) != 4 or any(e.get("schemeID") != "0158" for e in ico):
        chyby.append(f"851.pdf: ICO bez schemeID 0158: {[(e.text, e.get('schemeID')) for e in ico]}")
    forma = bis.findtext("cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyLegalForm", namespaces=NS)
    if not (forma or "").startswith("Register u OR"):
        chyby.append(f"851.pdf: chyba CompanyLegalForm dodavatela, je {forma!r}")
    if bis.find("cac:AccountingCustomerParty//cbc:CompanyLegalForm", NS) is not None:
        chyby.append("851.pdf: odberatel nema mat CompanyLegalForm")
    if bis.findtext("cac:PaymentMeans/cbc:PaymentID", namespaces=NS) != "260851":
        chyby.append(f"851.pdf: PaymentID je {bis.findtext('cac:PaymentMeans/cbc:PaymentID', namespaces=NS)}")
    return chyby


def main() -> int:
    zlyhalo = 0

    for test in (test_normalizacia, test_endpoint, test_bis_851):
        chyby = test()
        print(f"{test.__name__}:", "OK" if not chyby else "ZLYHALO")
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
