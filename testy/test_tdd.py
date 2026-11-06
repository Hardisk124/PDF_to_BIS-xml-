"""Test Peppol SK TDD: Faktura -> TDD -> validacia, vratane skutocneho PDF 851."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lxml import etree

from faktury.config import Config, Nastavenia, Peppol
from faktury.tdd import NS_TDD, do_tdd, rozsah
from faktury.ubl import NS_CAC, NS_CBC, ChybaPrevodu, Firma, do_ubl, endpoint_podla_schemy
from faktury.validate import XSD_TDD, skontroluj_tdd
from testy.vzorka import VSETKY

NS = {"pxs": NS_TDD, "cac": NS_CAC, "cbc": NS_CBC}
PEPPOL = dict(moj_endpoint_scheme="0245", odberatel_endpoint_scheme="0158")
CFG = Config(
    peppol=Peppol(**PEPPOL, tdd_spid_prijemcu="SKFS00001", tdd_spid_reprezentanta="SKAP00001"),
    nastavenia=Nastavenia(vystup_tdd=True),
)
DOKLAD = "pxs:ReportedTransaction/pxs:ReportedDocument"
# ibr-tdd-30 a ibr-tdd-38: co smie byt v stranach hlaseneho dokladu.
STRANY = {
    "cac:AccountingSupplierParty/cac:Party": {"PostalAddress", "PartyTaxScheme"},
    "cac:AccountingCustomerParty/cac:Party": {"PostalAddress", "PartyTaxScheme", "PartyLegalEntity"},
}


def _deti(uzol) -> set[str]:
    return {etree.QName(d).localname for d in uzol}


def skontroluj_strukturu(nazov: str, tdd: bytes, ubl: bytes) -> list[str]:
    chyby = [f"{nazov}: {c}" for c in skontroluj_tdd(tdd)]
    root, faktura = etree.fromstring(tdd), etree.fromstring(ubl)
    doklad = root.find(DOKLAD, NS)
    for cesta, povolene in STRANY.items():
        navyse = _deti(doklad.find(cesta, NS)) - povolene
        if navyse:
            chyby.append(f"{nazov}: {cesta} obsahuje zakazane {sorted(navyse)}")
    for zakazane in ("cbc:BuyerReference", "cbc:DueDate", "cac:PaymentTerms", ".//cbc:EndpointID", ".//cac:Contact"):
        if doklad.find(zakazane, NS) is not None:
            chyby.append(f"{nazov}: v ReportedDocument je zakazany {zakazane}")
    # Sumy a riadky musia byt totozne s BIS fakturou.
    for tdd_cesta, ubl_cesta in (("pxs:MonetaryTotal/cbc:PayableAmount", "cac:LegalMonetaryTotal/cbc:PayableAmount"),
                                 ("cac:TaxTotal/cbc:TaxAmount", "cac:TaxTotal/cbc:TaxAmount")):
        if doklad.findtext(tdd_cesta, namespaces=NS) != faktura.findtext(ubl_cesta, namespaces=NS):
            chyby.append(f"{nazov}: {tdd_cesta} sa nezhoduje s BIS")
    riadky_ubl = len(faktura.findall("cac:InvoiceLine", NS)) + len(faktura.findall("cac:CreditNoteLine", NS))
    if len(doklad.findall("pxs:DocumentLine", NS)) != riadky_ubl:
        chyby.append(f"{nazov}: pocet riadkov sa nezhoduje s BIS")
    return chyby


def test_vzorky() -> list[str]:
    chyby = []
    for nazov, tvorca in VSETKY.items():
        try:
            tdd = do_tdd(tvorca(), CFG).xml
        except ChybaPrevodu:
            continue  # vzorky, ktore ma prevod odmietnut, testuje test_prevod.py
        chyby += skontroluj_strukturu(nazov, tdd, do_ubl(tvorca(), CFG).xml)
    return chyby


def test_chybajuce_spid() -> list[str]:
    bez_spid = Config(peppol=Peppol(**PEPPOL), nastavenia=Nastavenia(vystup_tdd=True))
    chyby_tdd = " ".join(skontroluj_tdd(do_tdd(VSETKY["jednoducha"](), bez_spid).xml))
    chyby = []
    for ocakavane in ("SPID prijemcu", "SPID reprezentanta"):
        if ocakavane not in chyby_tdd:
            chyby.append(f"prazdne SPID: chyba hlaska '{ocakavane}'")
    if len(bez_spid.chybajuce()) != 2:
        chyby.append(f"chybajuce() ma hlasit 2 SPID, hlasi {bez_spid.chybajuce()}")
    return chyby


def test_pravidla() -> list[str]:
    chyby = []
    for vstup, ocakavane in ((("SK", "SK"), "D"), (("SK", "CZ"), "IC"), (("SK", "US"), "INTL")):
        if rozsah(*vstup) != ocakavane:
            chyby.append(f"rozsah{vstup} = {rozsah(*vstup)}, ocakavane {ocakavane}")
    firma = Firma(ico="36787965", dic="2022389633", ic_dph="SK2022389633")
    for schema, ocakavane in (("0245", "2022389633"), ("0158", "36787965"), ("9950", "SK2022389633"), ("", "SK2022389633")):
        if endpoint_podla_schemy(firma, schema) != ocakavane:
            chyby.append(f"endpoint pre {schema!r} = {endpoint_podla_schemy(firma, schema)}, ocakavane {ocakavane}")
    return chyby


def test_pdf_851() -> list[str]:
    pdf = Path(__file__).resolve().parent.parent / "vstup" / "851.pdf"
    if not pdf.exists():
        print("   vstup/851.pdf chyba, preskakuje sa")
        return []
    from faktury.sablona import precitaj

    faktura = precitaj(pdf)
    ubl, tdd = do_ubl(faktura, CFG).xml, do_tdd(faktura, CFG).xml
    chyby = skontroluj_strukturu("851.pdf", tdd, ubl)
    hlasiaca = etree.fromstring(tdd).find("pxs:ReportingParty/cbc:EndpointID", NS)
    if (hlasiaca.get("schemeID"), hlasiaca.text) != ("0245", "2022389633"):
        chyby.append(f"851.pdf: ReportingParty je {hlasiaca.get('schemeID')}:{hlasiaca.text}, ocakavane 0245:2022389633")
    odberatel = etree.fromstring(ubl).find("cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", NS)
    if (odberatel.get("schemeID"), odberatel.text) != ("0158", "31686184"):
        chyby.append(f"851.pdf: EndpointID odberatela v BIS je {odberatel.get('schemeID')}:{odberatel.text}")
    return chyby


def main() -> int:
    zlyhalo = 0
    for test in (test_pravidla, test_vzorky, test_chybajuce_spid, test_pdf_851):
        chyby = test()
        print(f"{test.__name__}:", "OK" if not chyby else "ZLYHALO")
        for c in chyby:
            print("   ", c)
        zlyhalo += len(chyby)
    print("\nTDD XSD:", "k dispozicii" if XSD_TDD.exists() else "nestiahnuta, XSD kontrola preskocena")
    print("Zlyhani:", zlyhalo)
    return 1 if zlyhalo else 0


if __name__ == "__main__":
    raise SystemExit(main())
