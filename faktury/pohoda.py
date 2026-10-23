"""Prevod extrahovanej faktury do XML importu uctovneho programu POHODA (SK).

Poradie elementov je dane XSD schemami Stormware (invoice.xsd, type.xsd),
nemen ho bez pohladu do nich. Sumy sa pocitaju po polozkach rovnako ako
v POHODE, takze DPH sa od PDF moze lisit o zaokruhlenie v centoch.
"""

from __future__ import annotations

import re
from decimal import Decimal

from lxml import etree

from .config import Config
from .model import Faktura, Strana
from .ubl import NULA, ChybaPrevodu, Vysledok, cislo, d2, d4, datum, kod_uhrady, percento, skontroluj_sucet, zaokruhli

NS_DAT = "http://www.stormware.cz/schema/version_2/data.xsd"
NS_INV = "http://www.stormware.cz/schema/version_2/invoice.xsd"
NS_TYP = "http://www.stormware.cz/schema/version_2/type.xsd"

# Slovenske sadzby DPH od roku 2025 a ich nazvy v POHODE.
SADZBY = {Decimal("23"): "high", Decimal("19"): "low", Decimal("5"): "third", NULA: "none"}
SUHRN = {"low": "priceLow", "high": "priceHigh", "third": "price3"}

# Clenenie DPH a clenenie KV DPH podla toho, ci faktura nesie dan.
CLENENIE_S_DPH = ("UD", "A1")
CLENENIE_BEZ_DPH = ("UDzahrSl", "KN")
# Predkontacia je pre vsetky faktury rovnaka.
PREDKONTACIA = "602001_Prepr"

MAX_TEXT_POLOZKY = 90  # typ:string90


def _e(rodic, ns: str, nazov: str, text: str | None = None, **atributy):
    prvok = etree.SubElement(rodic, f"{{{ns}}}{nazov}", **atributy)
    if text is not None:
        prvok.text = text
    return prvok


def inv(rodic, nazov, text=None, **atributy):
    return _e(rodic, NS_INV, nazov, text, **atributy)


def typ(rodic, nazov, text=None, **atributy):
    return _e(rodic, NS_TYP, nazov, text, **atributy)


def _ids(rodic, nazov: str, hodnota: str) -> None:
    typ(inv(rodic, nazov), "ids", hodnota)


def variabilny_symbol(cislo_faktury: str | None) -> str | None:
    """V26/0687 -> 260687."""
    return re.sub(r"\D", "", cislo_faktury or "")[:20] or None


def psc(hodnota: str | None) -> str | None:
    """97101 -> 971 01, tak ako ho POHODA zobrazuje."""
    text = re.sub(r"\s", "", hodnota or "")
    return f"{text[:3]} {text[3:]}" if re.fullmatch(r"\d{5}", text) else (text or None)


def ulica_a_cislo(ulica: str | None) -> tuple[str | None, str | None]:
    """Obuvnícka 3 -> (Obuvnícka, 3)."""
    m = re.fullmatch(r"(.*\D)\s+(\d[\w/]*)", (ulica or "").strip())
    return (m.group(1), m.group(2)) if m else (ulica or None, None)


def rozdel_text(text: str) -> tuple[str, str | None]:
    """Text polozky ma v POHODE najviac 90 znakov, zvysok ide do poznamky."""
    if len(text) <= MAX_TEXT_POLOZKY:
        return text, None
    rez = text.rfind(" ", 0, MAX_TEXT_POLOZKY + 1)
    rez = rez if rez > 0 else MAX_TEXT_POLOZKY
    return text[:rez].rstrip(), text[rez:].strip()[:MAX_TEXT_POLOZKY] or None


def _adresa(rodic, strana: Strana, s_cislom: bool, osoba: str | None = None) -> None:
    """Spolocne polia partnera aj vlastnej firmy, poradie podla type.xsd."""
    if strana.nazov:
        typ(rodic, "company", strana.nazov)
    if osoba:
        # Kontaktna osoba "Ľudmila Foltánová" -> surname Foltánová, name Ľudmila.
        meno, _, priezvisko = osoba.strip().rpartition(" ")
        typ(rodic, "surname", priezvisko[:32])
        if meno:
            typ(rodic, "name", meno[:32])
    if strana.mesto:
        typ(rodic, "city", strana.mesto)
    ulica, cislo_domu = ulica_a_cislo(strana.ulica) if s_cislom else (strana.ulica, None)
    if ulica:
        typ(rodic, "street", ulica)
    if cislo_domu:
        typ(rodic, "number", cislo_domu)
    if psc(strana.psc):
        typ(rodic, "zip", psc(strana.psc))
    if strana.ico:
        typ(rodic, "ico", strana.ico)
    if strana.dic:
        typ(rodic, "dic", strana.dic)


def do_pohody(faktura: Faktura, cfg: Config) -> Vysledok:
    upozornenia: list[str] = []
    if faktura.typ_dokladu not in (None, "380"):
        raise ChybaPrevodu(f"Typ dokladu {faktura.typ_dokladu} sa do POHODY zatial neprevadza, len bezne faktury.")

    d_vystavenia = datum(faktura.datum_vystavenia)
    if not d_vystavenia:
        raise ChybaPrevodu("Z faktury sa nepodarilo precitat datum vystavenia.")

    polozky = []
    for index, riadok in enumerate(faktura.riadky, start=1):
        zaklad = cislo(riadok.suma_bez_dph)
        sadzba = percento(riadok.sadzba_dph)
        if zaklad is None:
            raise ChybaPrevodu(f"Riadok {index}: chyba suma bez DPH.")
        if sadzba not in SADZBY:
            raise ChybaPrevodu(f"Riadok {index}: sadzbu DPH {riadok.sadzba_dph} % POHODA (SK) nepozna.")
        polozky.append((riadok, zaklad, sadzba, zaokruhli(zaklad * sadzba / 100)))
    if not polozky:
        raise ChybaPrevodu("Faktura neobsahuje ani jeden citatelny fakturovany riadok.")

    sucet_riadkov = zaokruhli(sum((p[1] for p in polozky), NULA))
    zaklad_faktury = cislo(faktura.suma_bez_dph)
    skontroluj_sucet(sucet_riadkov, sucet_riadkov if zaklad_faktury is None else zaklad_faktury)

    # POHODA pocita DPH po polozkach, faktura z PDF zo suctu. Rozdiel do pol centu
    # na polozku je zaokruhlenie, vacsi znamena, ze sa nieco precitalo zle.
    dph_polozky = sum((p[3] for p in polozky), NULA)
    dph_faktury = cislo(faktura.suma_dph)
    if dph_faktury is not None and abs(dph_polozky - dph_faktury) > Decimal("0.005") * len(polozky):
        upozornenia.append(
            f"DPH spocitana po polozkach {d2(dph_polozky)} sa lisi od DPH na fakture {d2(dph_faktury)}."
        )

    root = etree.Element(
        f"{{{NS_DAT}}}dataPack",
        nsmap={"dat": NS_DAT, "inv": NS_INV, "typ": NS_TYP},
        version="2.0", id="FakturyBIS3", ico=faktura.dodavatel.ico or "",
        application="FakturyBIS3", note="Import faktur z PDF",
    )
    polozka_balika = _e(root, NS_DAT, "dataPackItem", version="2.0", id=(faktura.cislo_faktury or "1")[:64])
    faktura_xml = inv(polozka_balika, "invoice", version="2.0")

    # --- hlavicka
    h = inv(faktura_xml, "invoiceHeader")
    inv(h, "invoiceType", "issuedInvoice")
    typ(inv(h, "number"), "numberRequested", (faktura.cislo_faktury or "").strip()[:32])
    vs = variabilny_symbol(faktura.cislo_faktury)
    if vs:
        inv(h, "symVar", vs)
    inv(h, "date", d_vystavenia)
    inv(h, "dateTax", datum(faktura.datum_dodania) or d_vystavenia)
    inv(h, "dateAccounting", d_vystavenia)
    d_splatnosti = datum(faktura.datum_splatnosti)
    if d_splatnosti:
        inv(h, "dateDue", d_splatnosti)
    _ids(h, "accounting", PREDKONTACIA)
    # Faktura ma vzdy bud vsetky polozky s DPH, alebo vsetky bez nej, preto staci
    # clenenie v hlavicke. Zmiesane sadzby znamenaju, ze sa nieco precitalo zle.
    s_dph = [sadzba > 0 for _, _, sadzba, _ in polozky]
    if any(s_dph) and not all(s_dph):
        upozornenia.append(
            "Faktura ma polozky s DPH aj bez DPH. Clenenie UD/A1 plati pre celu fakturu, skontroluj ju."
        )
    clenenie = CLENENIE_S_DPH if any(s_dph) else CLENENIE_BEZ_DPH
    _ids(h, "classificationVAT", clenenie[0])
    _ids(h, "classificationKVDPH", clenenie[1])
    if faktura.uvodny_text:
        inv(h, "text", faktura.uvodny_text[:240])

    partner = typ(inv(h, "partnerIdentity"), "address", linkToAddress="true")
    odberatel = faktura.odberatel
    _adresa(partner, odberatel, s_cislom=False)
    typ(partner, "VATPayerType", "payer" if odberatel.ic_dph else "non-payer")
    if odberatel.ic_dph:
        typ(partner, "icDph", odberatel.ic_dph)
    if odberatel.krajina:
        typ(typ(partner, "country"), "ids", odberatel.krajina)

    ja = typ(inv(h, "myIdentity"), "address")
    dodavatel = faktura.dodavatel
    _adresa(ja, dodavatel, s_cislom=True, osoba=dodavatel.kontakt)
    if dodavatel.ic_dph:
        typ(ja, "icDph", dodavatel.ic_dph)
    if dodavatel.telefon:
        typ(ja, "phone", re.sub(r"\s", "", dodavatel.telefon))
    if dodavatel.email:
        typ(ja, "email", dodavatel.email)
    if dodavatel.web:
        typ(ja, "www", dodavatel.web)

    if faktura.cislo_objednavky:
        inv(h, "numberOrder", faktura.cislo_objednavky.strip()[:32])
    d_objednavky = datum(faktura.datum_objednavky)
    if d_objednavky:
        inv(h, "dateOrder", d_objednavky)

    # Forma uhrady "Príkazom" je v POHODE predvolena pre prevod. Ostatne sa
    # posielaju len typom, POHODA si k nemu vyberie svoju formu uhrady.
    uhrada = inv(h, "paymentType")
    druh = {"30": "draft", "10": "cash", "42": "delivery", "48": "creditcard"}.get(
        kod_uhrady(faktura.sposob_uhrady, bool(faktura.iban)), "draft"
    )
    if druh == "draft":
        typ(uhrada, "ids", "Príkazom")
    typ(uhrada, "paymentType", druh)

    m = re.fullmatch(r"\s*(\d[\d-]*)\s*/\s*(\d{4})\s*", faktura.cislo_uctu or "")
    if m:
        ucet = inv(h, "account")
        if faktura.banka:
            typ(ucet, "ids", faktura.banka[:19])
        typ(ucet, "accountNo", m.group(1))
        typ(ucet, "bankCode", m.group(2))
    elif faktura.cislo_uctu:
        upozornenia.append(f"Cislo uctu '{faktura.cislo_uctu}' nema tvar cislo/kod banky, do POHODY sa nezapisalo.")

    if faktura.konstantny_symbol:
        inv(h, "symConst", faktura.konstantny_symbol.strip())

    # --- polozky
    detail = inv(faktura_xml, "invoiceDetail")
    for riadok, zaklad, sadzba, dph in polozky:
        p = inv(detail, "invoiceItem")
        text, poznamka = rozdel_text(" ".join(filter(None, [riadok.nazov, riadok.popis])) or "Polozka")
        inv(p, "text", text)
        mnozstvo = cislo(riadok.mnozstvo)
        inv(p, "quantity", d4(mnozstvo if mnozstvo is not None else Decimal(1)))
        if riadok.jednotka:
            inv(p, "unit", riadok.jednotka[:10])
        inv(p, "coefficient", "1.0")
        inv(p, "payVAT", "false")
        inv(p, "rateVAT", SADZBY[sadzba])
        inv(p, "discountPercentage", "0.0")
        suma = inv(p, "homeCurrency")
        jednotkova = cislo(riadok.jednotkova_cena)
        if jednotkova is None:
            jednotkova = zaklad / mnozstvo if mnozstvo else zaklad
        typ(suma, "unitPrice", d4(jednotkova))
        typ(suma, "price", d2(zaklad))
        typ(suma, "priceVAT", d2(dph))
        typ(suma, "priceSum", d2(zaklad + dph))
        if poznamka:
            inv(p, "note", poznamka)
        inv(p, "PDP", "false")

    # --- suhrn
    suhrn = inv(faktura_xml, "invoiceSummary")
    inv(suhrn, "roundingDocument", "none")
    inv(suhrn, "roundingVAT", "none")
    domaca = inv(suhrn, "homeCurrency")
    typ(domaca, "priceNone", d2(sum((z for _, z, s, _ in polozky if s == 0), NULA)))
    for nazov, pole in SUHRN.items():
        zaklad = sum((z for _, z, s, _ in polozky if SADZBY[s] == nazov), NULA)
        dph = sum((d for _, _, s, d in polozky if SADZBY[s] == nazov), NULA)
        typ(domaca, pole, d2(zaklad))
        typ(domaca, f"{pole}VAT", d2(dph))
        typ(domaca, f"{pole}Sum", d2(zaklad + dph))
    typ(typ(domaca, "round"), "priceRound", "0")

    xml = etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="Windows-1250")
    return Vysledok(xml=xml, upozornenia=upozornenia)
