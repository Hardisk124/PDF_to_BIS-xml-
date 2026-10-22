"""Test prevodu do POHODA XML: porovnanie so vzorom z POHODY a clenenie DPH.

Vzor vstup/correct_format_of_new.xml je export z POHODY k fakture vstup/new.pdf.
Ked tieto subory chybaju, porovnanie so vzorom sa preskoci.
"""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lxml import etree

from faktury.config import Config
from faktury.pohoda import NS_INV, NS_TYP, do_pohody, rozdel_text, ulica_a_cislo
from faktury.sablona import precitaj
from faktury.validate import skontroluj_pohodu
from testy.vzorka import faktura, riadok

VSTUP = Path(__file__).resolve().parent.parent / "vstup"
NS = {"inv": NS_INV, "typ": NS_TYP}

# Rozdiely voci vzoru, ktore su zamerne:
#   - liquidation, lock2, markRecord su stav zaznamu v POHODE, import ich nepotrebuje,
#   - kontakt, symbol a nazvy su tak, ako su na PDF (vzor ich ma z adresara POHODY),
#   - polozky maju navyse vlastne clenenie DPH.
IGNOROVANE = {
    "invoiceHeader/liquidation/amountHome", "invoiceHeader/lock2", "invoiceHeader/markRecord",
    "invoiceHeader/symConst", "invoiceHeader/myIdentity/address/phone",
    "invoiceHeader/myIdentity/address/email", "invoiceHeader/partnerIdentity/address/company",
    "invoiceDetail/invoiceItem/text",
    "invoiceDetail/invoiceItem/classificationVAT/ids", "invoiceDetail/invoiceItem/classificationKVDPH/ids",
}


def hodnoty(xml_root) -> dict[str, list[str]]:
    """Cesta listu pod inv:invoice -> hodnoty. Cisla sa porovnavaju ako cisla."""
    faktura_xml = xml_root.find(".//inv:invoice", NS)
    vysledok: dict[str, list[str]] = {}
    for prvok in faktura_xml.iter():
        text = (prvok.text or "").strip()
        if len(prvok) or not text:
            continue
        predkovia = []
        for predok in prvok.iterancestors():
            if predok is faktura_xml:
                break
            predkovia.append(etree.QName(predok).localname)
        cesta = "/".join([*reversed(predkovia), etree.QName(prvok).localname])
        try:
            text = str(Decimal(text).normalize())
        except InvalidOperation:
            pass
        vysledok.setdefault(cesta, []).append(text)
    return vysledok


def clenenie(xml: bytes) -> tuple[tuple[str, str], list[tuple[str, str]]]:
    root = etree.fromstring(xml)

    def par(uzol) -> tuple[str, str]:
        return (uzol.findtext("inv:classificationVAT/typ:ids", namespaces=NS),
                uzol.findtext("inv:classificationKVDPH/typ:ids", namespaces=NS))

    return par(root.find(".//inv:invoiceHeader", NS)), [par(p) for p in root.findall(".//inv:invoiceItem", NS)]


def main() -> int:
    chyby: list[str] = []

    # Clenenie: polozka s DPH UD/A1, bez DPH UN/KN, hlavicka UD/A1 ak aspon jedna ma DPH.
    zmiesana = faktura(
        riadky=[riadok(poradie=1, nazov="Preprava SK", mnozstvo="1", jednotkova_cena="100.00",
                       suma_bez_dph="100.00", sadzba_dph="23"),
                riadok(poradie=2, nazov="Preprava EU", mnozstvo="1", jednotkova_cena="50.00",
                       suma_bez_dph="50.00", sadzba_dph="0")],
        suma_bez_dph="150.00", suma_dph="23.00", suma_s_dph="173.00", na_uhradu="173.00",
    )
    xml = do_pohody(zmiesana, Config()).xml
    if clenenie(xml) != (("UD", "A1"), [("UD", "A1"), ("UN", "KN")]):
        chyby.append(f"zmiesana faktura: clenenie {clenenie(xml)}")
    chyby += [f"zmiesana faktura: {c}" for c in skontroluj_pohodu(xml)]

    bez_dph = faktura(
        riadky=[riadok(poradie=1, nazov="Preprava EU", mnozstvo="1", jednotkova_cena="50.00",
                       suma_bez_dph="50.00", sadzba_dph="0")],
        suma_bez_dph="50.00", suma_dph="0.00", suma_s_dph="50.00", na_uhradu="50.00",
    )
    xml = do_pohody(bez_dph, Config()).xml
    if clenenie(xml) != (("UN", "KN"), [("UN", "KN")]):
        chyby.append(f"faktura bez DPH: clenenie {clenenie(xml)}")

    for vstup, ocakavane in [("Obuvnícka 3", ("Obuvnícka", "3")), ("Jasná 724/16", ("Jasná", "724/16")),
                             ("Hlavná", ("Hlavná", None))]:
        if ulica_a_cislo(vstup) != ocakavane:
            chyby.append(f"ulica_a_cislo({vstup!r}) = {ulica_a_cislo(vstup)}")
    dlhy = " ".join(["slovo"] * 20)
    text, poznamka = rozdel_text(dlhy)
    if len(text) > 90 or f"{text} {poznamka}" != dlhy:
        chyby.append(f"rozdel_text: {text!r} / {poznamka!r}")

    # Skutocne PDF musia prejst XSD POHODY.
    for pdf in sorted(VSTUP.glob("*.pdf")):
        chyby += [f"{pdf.name}: {c}" for c in skontroluj_pohodu(do_pohody(precitaj(pdf), Config()).xml)]

    # Porovnanie so vzorom z POHODY.
    vzor, pdf = VSTUP / "correct_format_of_new.xml", VSTUP / "new.pdf"
    if vzor.exists() and pdf.exists():
        ocakavane = hodnoty(etree.parse(str(vzor)).getroot())
        skutocne = hodnoty(etree.fromstring(do_pohody(precitaj(pdf), Config()).xml))
        for cesta in sorted((set(ocakavane) | set(skutocne)) - IGNOROVANE):
            if ocakavane.get(cesta) != skutocne.get(cesta):
                chyby.append(f"vzor {cesta}: ocakavane {ocakavane.get(cesta)}, je {skutocne.get(cesta)}")
    else:
        print("Vzor alebo new.pdf chyba, porovnanie so vzorom sa preskakuje.")

    for c in chyby:
        print("ZLE ", c)
    print("Zlyhani:", len(chyby))
    return 1 if chyby else 0


if __name__ == "__main__":
    raise SystemExit(main())
