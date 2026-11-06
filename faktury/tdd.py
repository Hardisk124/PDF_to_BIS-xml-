"""Peppol SK Tax Data Document (TDD) 1.0.0, hlasenie faktury pre Financnu spravu.

TDD nie je faktura, ale obal pxs:TaxData s hlavickou hlasenia a okresanou kopiou
faktury v pxs:ReportedDocument. Kopia sa robi z hotoveho UBL, takze sumy, DPH aj
kontrola suctov su na jednom mieste (ubl.py).

Co smie byt v ReportedDocument, urcuju pravidla ibr-tdd-25 az ibr-tdd-86. Strom
POVOLENE nizsie ich prepisuje doslova, nic mimo neho sa do TDD nedostane.
Specifikacia: https://docs.peppol.eu/tdd/sk/tdd-sk/
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime

from lxml import etree

from .config import Config
from .model import Faktura
from .ubl import NS_CAC, NS_CBC, Vysledok, do_ubl

NS_TDD = "urn:peppol:schema:sk-taxdata:1.0"
CUSTOMIZATION_ID_TDD = "urn:peppol:taxdata:sk-1"
PROFILE_ID_TDD = "urn:peppol:taxreporting"
SCHEMA_SPID = "0242"
TYP_HLASENIA = "S"  # S = submit, R = resubmit, D = disregard

KRAJINY_EU = set(
    "AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split()
)

PREDPONY = {NS_CBC: "cbc", NS_CAC: "cac"}

# None = skopirovat cely element, slovnik = povolene deti.
_KRAJINA = {"cac:Country": {"cbc:IdentificationCode": None}}
_DPH = {"cbc:CompanyID": None, "cac:TaxScheme": {"cbc:ID": None}}
_KATEGORIA = {"cbc:ID": None, "cbc:Percent": None, "cac:TaxScheme": {"cbc:ID": None}}
_PRIRAZKA = {
    "cbc:ChargeIndicator": None, "cbc:AllowanceChargeReasonCode": None, "cbc:AllowanceChargeReason": None,
    "cbc:MultiplierFactorNumeric": None, "cbc:Amount": None, "cbc:BaseAmount": None,
}
_RIADOK = {
    "cbc:ID": None, "cbc:Note": None, "cbc:InvoicedQuantity": None, "cbc:CreditedQuantity": None,
    "cbc:LineExtensionAmount": None,
    "cac:InvoicePeriod": {"cbc:StartDate": None, "cbc:EndDate": None},
    "cac:AllowanceCharge": _PRIRAZKA,
    "cac:Item": {
        "cbc:Description": None, "cbc:Name": None,
        "cac:CommodityClassification": {"cbc:ItemClassificationCode": None},
        "cac:ClassifiedTaxCategory": _KATEGORIA,
    },
    "cac:Price": {"cbc:PriceAmount": None, "cbc:BaseQuantity": None},
}
POVOLENE = {
    "cbc:CustomizationID": None, "cbc:ProfileID": None, "cbc:ID": None, "cbc:IssueDate": None,
    "cbc:InvoiceTypeCode": None, "cbc:CreditNoteTypeCode": None,
    "cbc:Note": None, "cbc:TaxPointDate": None, "cbc:DocumentCurrencyCode": None, "cbc:TaxCurrencyCode": None,
    "cac:InvoicePeriod": {"cbc:StartDate": None, "cbc:EndDate": None, "cbc:DescriptionCode": None},
    "cac:BillingReference": {"cac:InvoiceDocumentReference": {"cbc:ID": None, "cbc:IssueDate": None}},
    "cac:AccountingSupplierParty": {"cac:Party": {"cac:PostalAddress": _KRAJINA, "cac:PartyTaxScheme": _DPH}},
    "cac:AccountingCustomerParty": {"cac:Party": {
        "cac:PostalAddress": _KRAJINA, "cac:PartyTaxScheme": _DPH,
        "cac:PartyLegalEntity": {"cbc:RegistrationName": None},
    }},
    "cac:TaxRepresentativeParty": {"cac:PostalAddress": _KRAJINA, "cac:PartyTaxScheme": _DPH},
    "cac:Delivery": {"cbc:ActualDeliveryDate": None},
    "cac:PaymentMeans": {
        "cbc:PaymentMeansCode": None, "cbc:PaymentID": None,
        "cac:CardAccount": {"cbc:PrimaryAccountNumberID": None, "cbc:NetworkID": None, "cbc:HolderName": None},
        "cac:PayeeFinancialAccount": {"cbc:ID": None, "cac:FinancialInstitutionBranch": {"cbc:ID": None}},
        "cac:PaymentMandate": {"cbc:ID": None, "cac:PayerFinancialAccount": {"cbc:ID": None}},
    },
    "cac:AllowanceCharge": {**_PRIRAZKA, "cac:TaxCategory": _KATEGORIA},
    "cac:TaxTotal": {"cbc:TaxAmount": None, "cac:TaxSubtotal": {
        "cbc:TaxableAmount": None, "cbc:TaxAmount": None,
        "cac:TaxCategory": {**_KATEGORIA, "cbc:TaxExemptionReasonCode": None, "cbc:TaxExemptionReason": None},
    }},
    "cac:LegalMonetaryTotal": {k: None for k in (
        "cbc:LineExtensionAmount", "cbc:TaxExclusiveAmount", "cbc:TaxInclusiveAmount", "cbc:AllowanceTotalAmount",
        "cbc:ChargeTotalAmount", "cbc:PrepaidAmount", "cbc:PayableRoundingAmount", "cbc:PayableAmount",
    )},
    "cac:InvoiceLine": _RIADOK,
    "cac:CreditNoteLine": _RIADOK,
}
# UBL nazov -> nazov v TDD. Dobropis nema v TDD vlastne riadky ani mnozstvo.
PREMENOVANIE = {
    "cbc:InvoiceTypeCode": f"{{{NS_TDD}}}DocumentTypeCode",
    "cbc:CreditNoteTypeCode": f"{{{NS_TDD}}}DocumentTypeCode",
    "cac:LegalMonetaryTotal": f"{{{NS_TDD}}}MonetaryTotal",
    "cac:InvoiceLine": f"{{{NS_TDD}}}DocumentLine",
    "cac:CreditNoteLine": f"{{{NS_TDD}}}DocumentLine",
    "cbc:CreditedQuantity": f"{{{NS_CBC}}}InvoicedQuantity",
}


def _kratky(tag: str) -> str:
    ns, _, meno = tag[1:].partition("}")
    return f"{PREDPONY.get(ns, ns)}:{meno}"


def _prenes(zdroj, povolene: dict | None, rodic) -> None:
    """Skopiruje zdroj pod rodica, ale len povolene deti. Prazdny kontajner vynecha (PEPPOL-R008)."""
    novy_tag = PREMENOVANIE.get(_kratky(zdroj.tag), zdroj.tag)
    if povolene is None:
        kopia = deepcopy(zdroj)
        kopia.tag = novy_tag
        rodic.append(kopia)
        return
    novy = etree.SubElement(rodic, novy_tag, attrib=dict(zdroj.attrib))
    for dieta in zdroj:
        if isinstance(dieta.tag, str) and _kratky(dieta.tag) in povolene:
            _prenes(dieta, povolene[_kratky(dieta.tag)], novy)
    if len(novy) == 0:
        rodic.remove(novy)


def _pxs(rodic, meno: str, text: str | None = None):
    prvok = etree.SubElement(rodic, f"{{{NS_TDD}}}{meno}")
    prvok.text = text
    return prvok


def _cbc(rodic, meno: str, text: str, **atributy):
    prvok = etree.SubElement(rodic, f"{{{NS_CBC}}}{meno}", **atributy)
    prvok.text = text
    return prvok


def rozsah(krajina_dodavatela: str, krajina_odberatela: str) -> str:
    """DocumentScope: D tuzemsko, IC v ramci EU, INTL mimo EU."""
    if krajina_dodavatela == krajina_odberatela:
        return "D"
    return "IC" if krajina_odberatela in KRAJINY_EU else "INTL"


def do_tdd(faktura: Faktura, cfg: Config, teraz: datetime | None = None) -> Vysledok:
    ubl = do_ubl(faktura, cfg)
    # Bez medzier z UBL, inak by pretty_print nechal v TDD rozbite odsadenie.
    faktura_xml = etree.fromstring(ubl.xml, etree.XMLParser(remove_blank_text=True))
    p = cfg.peppol
    ns = {"cac": NS_CAC, "cbc": NS_CBC}

    def text(cesta: str) -> str:
        return (faktura_xml.findtext(cesta, default="", namespaces=ns) or "").strip()

    teraz = (teraz or datetime.now()).astimezone()
    root = etree.Element(f"{{{NS_TDD}}}TaxData", nsmap={"pxs": NS_TDD, "cbc": NS_CBC, "cac": NS_CAC})
    _cbc(root, "CustomizationID", CUSTOMIZATION_ID_TDD)
    _cbc(root, "ProfileID", PROFILE_ID_TDD)
    _cbc(root, "UUID", str(uuid.uuid4()))
    _cbc(root, "IssueDate", teraz.date().isoformat())
    _cbc(root, "IssueTime", teraz.timetz().isoformat(timespec="seconds"))  # s casovou zonou, ibr-tdd-05
    _pxs(root, "TaxDataTypeCode", TYP_HLASENIA)
    _pxs(root, "DocumentScope", rozsah(
        text("cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode"),
        text("cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode"),
    ))
    _pxs(root, "ReporterRole", p.tdd_rola)

    urad = _pxs(root, "TaxAuthority")
    _cbc(urad, "ID", p.tdd_urad_id)
    if p.tdd_urad_nazov:
        _cbc(urad, "Name", p.tdd_urad_nazov)

    # Hlasiaca strana: pri C2 dodavatel (moj Peppol ID), pri C3 odberatel. Ten isty EndpointID ako vo fakture.
    if p.tdd_rola == "C3":
        strana, schema_hlasiaceho = "cac:AccountingCustomerParty", p.odberatel_endpoint_scheme
    else:
        strana, schema_hlasiaceho = "cac:AccountingSupplierParty", p.moj_endpoint_scheme
    _cbc(_pxs(root, "ReportingParty"), "EndpointID",
         text(f"{strana}/cac:Party/cbc:EndpointID"), schemeID=schema_hlasiaceho)
    _cbc(_pxs(root, "ReceivingParty"), "EndpointID", p.tdd_spid_prijemcu, schemeID=SCHEMA_SPID)
    zastupca = etree.SubElement(_pxs(root, "ReportersRepresentative"), f"{{{NS_CAC}}}PartyIdentification")
    _cbc(zastupca, "ID", p.tdd_spid_reprezentanta, schemeID=SCHEMA_SPID)

    doklad = _pxs(_pxs(root, "ReportedTransaction"), "ReportedDocument")
    for dieta in faktura_xml:
        kluc = _kratky(dieta.tag) if isinstance(dieta.tag, str) else ""
        if kluc in POVOLENE:
            _prenes(dieta, POVOLENE[kluc], doklad)
        if kluc == "cbc:ID":
            # Rovnaky doklad ma vzdy rovnake UUID, aby opravne hlasenie (R) odkazovalo na ten isty.
            # Dodavatel podla IC DPH, bez neho podla EndpointID, aby sa UUID roznych dodavatelov nezrazili.
            dodavatel = (text("cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID")
                         or text("cac:AccountingSupplierParty/cac:Party/cbc:EndpointID"))
            _cbc(doklad, "UUID", str(uuid.uuid5(uuid.NAMESPACE_URL, f"{dodavatel}/{dieta.text}")))

    xml = etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return Vysledok(xml=xml, upozornenia=list(ubl.upozornenia))
