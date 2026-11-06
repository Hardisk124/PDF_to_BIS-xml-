"""Kontrola vygenerovaneho XML.

Dve urovne:
1. Vstavane obchodne pravidla EN 16931 a Peppol, ktore v praxi padaju najcastejsie.
2. Volitelna XSD validacia, ak su v priecinku schemas/ stiahnute UBL 2.1 schemy.

Toto nenahradza oficialny Peppol Schematron. Sluzi na to, aby sa zjavne
nepouzitelne XML nedostalo do vystupu.
"""

from __future__ import annotations

import re
import threading
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from lxml import etree

from . import cesty
from .config import KATEGORIE_NULOVEJ_DPH
from .ubl import NS_CAC, NS_CBC

NS = {"cac": NS_CAC, "cbc": NS_CBC}

XSD_INVOICE = cesty.zdroj("schemas", "maindoc", "UBL-Invoice-2.1.xsd")
XSD_CREDITNOTE = cesty.zdroj("schemas", "maindoc", "UBL-CreditNote-2.1.xsd")
XSD_POHODA = cesty.zdroj("schemas", "pohoda", "invoice.xsd")
XSD_TDD = cesty.zdroj("schemas", "tdd", "Peppol-SK-TDD.xsd")
NS_TDD = "urn:peppol:schema:sk-taxdata:1.0"
NS_POHODA_INV = "http://www.stormware.cz/schema/version_2/invoice.xsd"

TOLERANCIA = Decimal("0.02")


def _text(uzol, cesta: str) -> str | None:
    najdene = uzol.find(cesta, NS)
    return najdene.text.strip() if najdene is not None and najdene.text else None


def _dec(uzol, cesta: str) -> Decimal | None:
    hodnota = _text(uzol, cesta)
    try:
        return Decimal(hodnota) if hodnota else None
    except InvalidOperation:
        return None


def skontroluj(xml: bytes) -> list[str]:
    """Vrati zoznam chyb. Prazdny zoznam znamena, ze vstavane pravidla presli."""
    chyby: list[str] = []
    root = etree.fromstring(xml)
    je_dobropis = root.tag.endswith("}CreditNote")
    predpona_riadku = "cac:CreditNoteLine" if je_dobropis else "cac:InvoiceLine"
    pole_mnozstva = "cbc:CreditedQuantity" if je_dobropis else "cbc:InvoicedQuantity"
    pole_typu = "cbc:CreditNoteTypeCode" if je_dobropis else "cbc:InvoiceTypeCode"

    if not _text(root, "cbc:ID"):
        chyby.append("BR-02: chyba cislo dokladu.")
    if not _text(root, "cbc:IssueDate"):
        chyby.append("BR-03: chyba datum vystavenia.")
    if not _text(root, pole_typu):
        chyby.append("BR-04: chyba kod typu dokladu.")
    mena = _text(root, "cbc:DocumentCurrencyCode")
    if not mena:
        chyby.append("BR-05: chyba kod meny.")

    if not _text(root, "cbc:BuyerReference") and root.find("cac:OrderReference/cbc:ID", NS) is None:
        chyby.append("BR-Peppol: chyba referencia odberatela aj cislo objednavky, staci jedno z nich.")

    for oznacenie, cesta in (("dodavatela", "cac:AccountingSupplierParty"), ("odberatela", "cac:AccountingCustomerParty")):
        strana = root.find(cesta, NS)
        if strana is None:
            chyby.append(f"Chyba cely blok {oznacenie}.")
            continue
        if not _text(strana, "cac:Party/cac:PartyLegalEntity/cbc:RegistrationName"):
            chyby.append(f"BR-06/BR-07: chyba nazov {oznacenie}.")
        if not _text(strana, "cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode"):
            chyby.append(f"BR-09/BR-11: chyba kod krajiny {oznacenie}.")
        if strana.find("cac:Party/cbc:EndpointID", NS) is None:
            chyby.append(f"BR-Peppol: chyba EndpointID {oznacenie}.")
        else:
            endpoint = strana.find("cac:Party/cbc:EndpointID", NS)
            if not endpoint.get("schemeID"):
                chyby.append(
                    f"BR-Peppol: EndpointID {oznacenie} nema atribut schemeID. "
                    "Doplni sa v config.toml podla Peppol EAS zoznamu."
                )

    riadky = root.findall(predpona_riadku, NS)
    if not riadky:
        chyby.append("BR-16: doklad neobsahuje ani jeden riadok.")

    sucet_riadkov = Decimal(0)
    for i, riadok in enumerate(riadky, start=1):
        suma = _dec(riadok, "cbc:LineExtensionAmount")
        if suma is None:
            chyby.append(f"BR-24: riadok {i} nema sumu.")
            continue
        sucet_riadkov += suma
        if riadok.find(pole_mnozstva, NS) is None:
            chyby.append(f"BR-22: riadok {i} nema mnozstvo.")
        if not _text(riadok, "cac:Item/cbc:Name"):
            chyby.append(f"BR-25: riadok {i} nema nazov polozky.")
        cena = _dec(riadok, "cac:Price/cbc:PriceAmount")
        if cena is not None and cena < 0:
            chyby.append(
                f"BR-27: riadok {i} ma zapornu jednotkovu cenu {cena}. "
                "Zaporna moze byt suma riadku, nie cena za jednotku."
            )
        if not _text(riadok, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID"):
            chyby.append(f"BR-CO-04: riadok {i} nema kategoriu DPH.")

    suhrn = root.find("cac:LegalMonetaryTotal", NS)
    if suhrn is None:
        chyby.append("BR-12 az BR-15: chyba blok suhrnnych suciet.")
        return chyby

    bt106 = _dec(suhrn, "cbc:LineExtensionAmount")
    bt109 = _dec(suhrn, "cbc:TaxExclusiveAmount")
    bt112 = _dec(suhrn, "cbc:TaxInclusiveAmount")
    bt113 = _dec(suhrn, "cbc:PrepaidAmount") or Decimal(0)
    bt114 = _dec(suhrn, "cbc:PayableRoundingAmount") or Decimal(0)
    bt115 = _dec(suhrn, "cbc:PayableAmount")

    for kod, hodnota, nazov in (
        ("BR-12", bt106, "sucet riadkov"),
        ("BR-13", bt109, "zaklad dane"),
        ("BR-14", bt112, "suma s DPH"),
        ("BR-15", bt115, "suma na uhradu"),
    ):
        if hodnota is None:
            chyby.append(f"{kod}: chyba {nazov} v suhrne.")

    if bt106 is not None and abs(sucet_riadkov - bt106) > TOLERANCIA:
        chyby.append(
            f"BR-CO-10: sucet riadkov {sucet_riadkov} sa nerovna uvedenej hodnote {bt106}."
        )

    tax_total = root.find("cac:TaxTotal", NS)
    dph_celkom = _dec(tax_total, "cbc:TaxAmount") if tax_total is not None else None
    if tax_total is None:
        chyby.append("BR-CO-14: chyba blok TaxTotal.")
    else:
        sucet_podskupin = Decimal(0)
        zaklady = Decimal(0)
        for podskupina in tax_total.findall("cac:TaxSubtotal", NS):
            zaklad = _dec(podskupina, "cbc:TaxableAmount")
            dan = _dec(podskupina, "cbc:TaxAmount")
            sadzba = _dec(podskupina, "cac:TaxCategory/cbc:Percent")
            kategoria = _text(podskupina, "cac:TaxCategory/cbc:ID")
            if zaklad is None or dan is None:
                chyby.append("BR-45/BR-46: rekapitulacia DPH nema zaklad alebo sumu dane.")
                continue
            sucet_podskupin += dan
            zaklady += zaklad
            if sadzba is not None:
                ocakavana = (zaklad * sadzba / Decimal(100)).quantize(Decimal("0.01"))
                if abs(ocakavana - dan) > TOLERANCIA:
                    chyby.append(
                        f"BR-S-09: pri sadzbe {sadzba} percent ma byt DPH {ocakavana}, v XML je {dan}."
                    )
            if kategoria in KATEGORIE_NULOVEJ_DPH and sadzba and sadzba != 0:
                chyby.append(f"BR-AE-08: kategoria {kategoria} musi mat sadzbu 0, v XML je {sadzba}.")

        if dph_celkom is not None and abs(sucet_podskupin - dph_celkom) > TOLERANCIA:
            chyby.append(
                f"BR-CO-14: sucet rekapitulacie {sucet_podskupin} sa nerovna celkovej DPH {dph_celkom}."
            )
        if bt109 is not None and abs(zaklady - bt109) > TOLERANCIA:
            chyby.append(
                f"BR-CO-11: sucet zakladov v rekapitulacii {zaklady} sa nerovna zakladu dane {bt109}."
            )

    if bt109 is not None and bt112 is not None and dph_celkom is not None:
        if abs((bt109 + dph_celkom) - bt112) > TOLERANCIA:
            chyby.append(
                f"BR-CO-15: zaklad {bt109} plus DPH {dph_celkom} sa nerovna sume s DPH {bt112}."
            )
    if bt112 is not None and bt115 is not None:
        ocakavana = bt112 - bt113 + bt114
        if abs(ocakavana - bt115) > TOLERANCIA:
            chyby.append(
                f"BR-CO-16: suma na uhradu ma byt {ocakavana}, v XML je {bt115}."
            )

    return chyby


# Zostavenie UBL schemy trva zhruba dvestomilisekund, preto sa robi raz.
# libxml2 nezarucuje bezpecne volanie validate() z viacerych vlakien naraz,
# takze zdielana schema je chranena zamkom.
_ZAMOK_SCHEMY = threading.Lock()


@lru_cache(maxsize=4)
def _schema(cesta: Path) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(cesta)))


def skontroluj_xsd(xml: bytes) -> list[str]:
    """XSD validacia. Vrati prazdny zoznam aj vtedy, ked schemy nie su stiahnute."""
    root = etree.fromstring(xml)
    cesta = XSD_CREDITNOTE if root.tag.endswith("}CreditNote") else XSD_INVOICE
    if not cesta.exists():
        return []
    with _ZAMOK_SCHEMY:
        schema = _schema(cesta)
        if schema.validate(etree.ElementTree(root)):
            return []
        return [f"XSD: {chyba.message}" for chyba in schema.error_log]


def skontroluj_pohodu(xml: bytes) -> list[str]:
    """XSD validacia faktury v POHODA XML. Obal dataPack sa neoveruje, len faktura v nom."""
    if not XSD_POHODA.exists():
        return []
    faktura = etree.fromstring(xml).find(f".//{{{NS_POHODA_INV}}}invoice")
    if faktura is None:
        return ["XSD: v XML nie je ziadna faktura."]
    with _ZAMOK_SCHEMY:
        schema = _schema(XSD_POHODA)
        if schema.validate(etree.ElementTree(faktura)):
            return []
        return [f"XSD: {chyba.message}" for chyba in schema.error_log]


def xsd_dostupne() -> bool:
    return XSD_INVOICE.exists()


def skontroluj_tdd(xml: bytes) -> list[str]:
    """Peppol SK TDD: XSD a fatalne pravidla ibr-tdd, ktore XSD nezachyti.

    Obsah ReportedDocument vznika z UBL, ktore uz preslo obchodnymi pravidlami,
    preto sa tu kontroluje hlavne hlavicka hlasenia.
    """
    root = etree.fromstring(xml)
    ns = {**NS, "pxs": NS_TDD}

    def text(cesta: str) -> str:
        return (root.findtext(cesta, default="", namespaces=ns) or "").strip()

    def schema(cesta: str) -> str:
        prvok = root.find(cesta, ns)
        return (prvok.get("schemeID") or "") if prvok is not None else ""

    chyby: list[str] = []
    if text("cbc:CustomizationID") != "urn:peppol:taxdata:sk-1":
        chyby.append("ibr-tdd-01: CustomizationID musi byt urn:peppol:taxdata:sk-1.")
    if text("cbc:ProfileID") != "urn:peppol:taxreporting":
        chyby.append("ibr-tdd-02: ProfileID musi byt urn:peppol:taxreporting.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d+)?(Z|[+-]\d{2}:\d{2})", text("cbc:IssueTime")):
        chyby.append("ibr-tdd-05: IssueTime musi obsahovat casovu zonu.")
    for cesta, povolene, kod in (
        ("pxs:TaxDataTypeCode", {"S", "R", "D"}, "ibr-tdd-06"),
        ("pxs:DocumentScope", {"D", "IC", "INTL"}, "ibr-tdd-08"),
        ("pxs:ReporterRole", {"C2", "C3"}, "ibr-tdd-09"),
    ):
        if text(cesta) not in povolene:
            chyby.append(f"{kod}: {cesta} ma hodnotu '{text(cesta)}', povolene su {', '.join(sorted(povolene))}.")
    if not text("pxs:TaxAuthority/cbc:ID"):
        chyby.append("ibr-tdd-13: chyba ID danoveho uradu, doplni sa v nastaveniach.")
    if not text("pxs:ReportingParty/cbc:EndpointID") or not re.fullmatch(r"\d{4}", schema("pxs:ReportingParty/cbc:EndpointID")):
        chyby.append("ibr-tdd-15/17: chyba Peppol ID hlasiacej strany alebo jeho EAS kod.")
    if not text("pxs:ReceivingParty/cbc:EndpointID") or schema("pxs:ReceivingParty/cbc:EndpointID") != "0242":
        chyby.append("ibr-tdd-19/20: chyba SPID prijemcu (Financna sprava), doplni sa v nastaveniach.")
    cesta_zastupcu = "pxs:ReportersRepresentative/cac:PartyIdentification/cbc:ID"
    if not text(cesta_zastupcu) or schema(cesta_zastupcu) != "0242":
        chyby.append("ibr-tdd-22/23: chyba SPID reprezentanta (tvoj Access Point), doplni sa v nastaveniach.")
    if not root.findall("pxs:ReportedTransaction/pxs:ReportedDocument/pxs:DocumentLine", ns):
        chyby.append("BR-16: hlaseny doklad neobsahuje ani jeden riadok.")
    # PEPPOL-EN16931-R008: ziadne prazdne elementy. Chybajuce SPID uz je hlasene vyssie.
    if not chyby and any(len(e) == 0 and not (e.text or "").strip() for e in root.iter() if isinstance(e.tag, str)):
        chyby.append("PEPPOL-R008: TDD obsahuje prazdny element.")

    if XSD_TDD.exists():
        with _ZAMOK_SCHEMY:
            xsd = _schema(XSD_TDD)
            if not xsd.validate(etree.ElementTree(root)):
                chyby.extend(f"XSD: {chyba.message}" for chyba in xsd.error_log)
    return chyby
