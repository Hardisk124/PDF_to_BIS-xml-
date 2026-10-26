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
from faktury.pohoda import CLENENIE_BEZ_DPH, NS_INV, NS_TYP, do_pohody, rozdel_text, ulica_a_cislo
from faktury.sablona import precitaj
from faktury.validate import skontroluj_pohodu
from testy.vzorka import faktura, riadok

VSTUP = Path(__file__).resolve().parent.parent / "vstup"
NS = {"inv": NS_INV, "typ": NS_TYP}

# Rozdiely voci vzoru, ktore su zamerne:
#   - liquidation, lock2, markRecord su stav zaznamu v POHODE, import ich nepotrebuje,
#   - coefficient sa pri textovych polozkach nepouziva (POHODA hlasi upozornenie 602),
#   - kontakt dodavatela je natvrdo, symbol a nazov odberatela su z PDF (vzor ich ma z adresara POHODY).
IGNOROVANE = {
    "invoiceHeader/liquidation/amountHome", "invoiceHeader/lock2", "invoiceHeader/markRecord",
    "invoiceHeader/symConst", "invoiceHeader/myIdentity/address/phone",
    "invoiceHeader/myIdentity/address/email", "invoiceHeader/partnerIdentity/address/company",
    "invoiceDetail/invoiceItem/coefficient",
    "invoiceDetail/invoiceItem/text",
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


def clenenie(xml: bytes) -> tuple[str, str]:
    hlavicka = etree.fromstring(xml).find(".//inv:invoiceHeader", NS)
    return (hlavicka.findtext("inv:classificationVAT/typ:ids", namespaces=NS),
            hlavicka.findtext("inv:classificationKVDPH/typ:ids", namespaces=NS))


def main() -> int:
    chyby: list[str] = []

    # Clenenie: faktura s DPH UD/A1, bez DPH podla CLENENIE_BEZ_DPH. Zmiesane sadzby sa nemaju stat, preto upozornenie.
    zmiesana = faktura(
        riadky=[riadok(poradie=1, nazov="Preprava SK", mnozstvo="1", jednotkova_cena="100.00",
                       suma_bez_dph="100.00", sadzba_dph="23"),
                riadok(poradie=2, nazov="Preprava EU", mnozstvo="1", jednotkova_cena="50.00",
                       suma_bez_dph="50.00", sadzba_dph="0")],
        suma_bez_dph="150.00", suma_dph="23.00", suma_s_dph="173.00", na_uhradu="173.00",
    )
    vysledok = do_pohody(zmiesana, Config())
    if clenenie(vysledok.xml) != ("UD", "A1"):
        chyby.append(f"zmiesana faktura: clenenie {clenenie(vysledok.xml)}")
    if not any("s DPH aj bez DPH" in u for u in vysledok.upozornenia):
        chyby.append("zmiesana faktura: chyba upozornenie na zmiesane sadzby")
    chyby += [f"zmiesana faktura: {c}" for c in skontroluj_pohodu(vysledok.xml)]

    bez_dph = faktura(
        riadky=[riadok(poradie=1, nazov="Preprava EU", mnozstvo="1", jednotkova_cena="50.00",
                       suma_bez_dph="50.00", sadzba_dph="0")],
        suma_bez_dph="50.00", suma_dph="0.00", suma_s_dph="50.00", na_uhradu="50.00",
    )
    xml = do_pohody(bez_dph, Config()).xml
    if clenenie(xml) != CLENENIE_BEZ_DPH:
        chyby.append(f"faktura bez DPH: clenenie {clenenie(xml)}")

    for vstup, ocakavane in [("Obuvnícka 3", ("Obuvnícka", "3")), ("Jasná 724/16", ("Jasná", "724/16")),
                             ("Hlavná", ("Hlavná", None))]:
        if ulica_a_cislo(vstup) != ocakavane:
            chyby.append(f"ulica_a_cislo({vstup!r}) = {ulica_a_cislo(vstup)}")
    dlhy = " ".join(["slovo"] * 20)
    text, poznamka = rozdel_text(dlhy)
    if len(text) > 90 or f"{text} {poznamka}" != dlhy:
        chyby.append(f"rozdel_text: {text!r} / {poznamka!r}")

    # Kontakt dodavatela je natvrdo, nie z faktury.
    if VSTUP.joinpath("new.pdf").exists():
        ja = etree.fromstring(do_pohody(precitaj(VSTUP / "new.pdf"), Config()).xml).find(".//inv:myIdentity/typ:address", NS)
        kontakt = [ja.findtext(f"typ:{p}", namespaces=NS) for p in ("surname", "name", "phone", "email", "www")]
        if kontakt != [None, None, "+421918067976", "info@grimmel.sk", "www.grimmel.sk"]:
            chyby.append(f"kontakt dodavatela: {kontakt}")

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
