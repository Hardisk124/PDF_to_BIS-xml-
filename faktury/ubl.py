"""Prevod extrahovanej faktury do UBL 2.1 podla Peppol BIS Billing 3.0.

Poradie elementov v UBL je dane XSD sekvenciou a nie je volitelne.
Funkcie nizsie ho drzia zamerne v tomto poradi, nemen ho bez pohladu do schemy.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from lxml import etree

from .config import KATEGORIE_NULOVEJ_DPH, Config
from .model import Faktura, Riadok, Strana

NS_INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CN = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

KODY_DOBROPISU = {"381", "396"}

# Slovenske nazvy jednotiek na kody UN/ECE Recommendation 20.
JEDNOTKY = {
    "ks": "H87", "kus": "H87", "kusov": "H87", "pcs": "H87", "szt": "H87",
    "hod": "HUR", "hodina": "HUR", "hodin": "HUR", "h": "HUR", "nh": "HUR",
    "min": "MIN", "den": "DAY", "dni": "DAY", "dnov": "DAY",
    "mesiac": "MON", "mes": "MON", "mesiacov": "MON",
    "rok": "ANN", "rokov": "ANN",
    "kg": "KGM", "g": "GRM", "t": "TNE", "tona": "TNE", "ton": "TNE", "tony": "TNE",
    "m": "MTR", "bm": "MTR", "km": "KMT",
    "m2": "MTK", "m3": "MTQ",
    "l": "LTR", "liter": "LTR", "ml": "MLT",
    "bal": "XBX", "balenie": "XBX", "ks.": "H87",
    "sada": "SET", "par": "PR", "paleta": "XPX",
    "kwh": "KWH", "mwh": "MWH", "gj": "GJO",
    "%": "P1",
}
JEDNOTKA_INAK = "C62"  # "one", univerzalna jednotka pre sluzby a polozky bez jednotky

SPOSOBY_UHRADY = [
    (("hotov", "cash", "pokladn"), "10"),
    (("sek", "cheque", "check"), "20"),
    (("prevod", "transfer", "ucet", "úcet", "bank"), "30"),
    (("inkas", "direct debit", "sipo"), "49"),
    (("kart", "card", "visa", "master"), "48"),
    (("dobierk", "cod"), "42"),
    (("zapoc", "zápoc", "kompenz", "set-off"), "97"),
]
UHRADA_INAK = "1"  # instrument not defined

# Kluce su male pismena bez diakritiky a medzier, pozri kod_krajiny().
KRAJINY = {
    "svk": "SK", "slovensko": "SK", "slovenska": "SK", "slovenskarepublika": "SK",
    "cze": "CZ", "cesko": "CZ", "ceskarepublika": "CZ",
    "pol": "PL", "polsko": "PL", "polska": "PL", "poland": "PL",
    "hun": "HU", "madarsko": "HU",
    "deu": "DE", "nemecko": "DE", "deutschland": "DE",
    "aut": "AT", "rakusko": "AT", "osterreich": "AT",
}

DVE = Decimal("0.01")
STYRI = Decimal("0.0001")
NULA = Decimal("0")


@dataclass(frozen=True)
class Firma:
    """Strana dokladu v podobe, v akej ide do XML."""

    nazov: str = ""
    ico: str = ""
    dic: str = ""
    ic_dph: str = ""
    ulica: str = ""
    mesto: str = ""
    psc: str = ""
    krajina: str = "SK"
    email: str = ""
    telefon: str = ""
    endpoint_id: str = ""


class ChybaPrevodu(ValueError):
    pass


@dataclass
class Vysledok:
    xml: bytes
    upozornenia: list[str]


# ---------------------------------------------------------------- normalizacia


def cislo(hodnota: str | None) -> Decimal | None:
    """Prevedie sumu z faktury na Decimal. Zvlada medzery, ciarky aj menove znaky."""
    if hodnota is None:
        return None
    text = str(hodnota).strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-+]", "", text)
    if not text or text in {"-", "+", ".", ","}:
        return None
    # 1.234,56 -> 1234.56 ; 1,234.56 -> 1234.56 ; 1234,56 -> 1234.56
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def zaokruhli(hodnota: Decimal) -> Decimal:
    return hodnota.quantize(DVE, rounding=ROUND_HALF_UP)


def d2(hodnota: Decimal | None) -> str:
    return str(zaokruhli(hodnota or NULA))


def d4(hodnota: Decimal | None) -> str:
    """Az styri desatinne miesta bez zbytocnych nul.

    Pouziva format(c, "f"), lebo str(Decimal) vie vratit exponencialny zapis
    typu -2E+2, ktory UBL schema odmietne.
    """
    cislo_ = (hodnota or NULA).quantize(STYRI, rounding=ROUND_HALF_UP)
    text = format(cislo_, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def d_cena(hodnota: Decimal | None) -> str:
    """Jednotkova cena: dve az styri desatinne miesta, nikdy menej ako dve."""
    text = d4(hodnota)
    cele, _, zlomok = text.partition(".")
    return f"{cele}.{(zlomok or '').ljust(2, '0')}"


def percento(hodnota: str | None) -> Decimal | None:
    c = cislo(hodnota)
    return None if c is None else zaokruhli(c)


def datum(hodnota: str | None) -> str | None:
    if not hodnota:
        return None
    text = str(hodnota).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    m = re.fullmatch(r"(\d{1,2})[.\-/ ]\s*(\d{1,2})[.\-/ ]\s*(\d{4})", text)
    if m:
        d, mes, r = m.groups()
        return f"{r}-{int(mes):02d}-{int(d):02d}"
    return None


def bez_diakritiky(text: str) -> str:
    return "".join(z for z in unicodedata.normalize("NFKD", text) if not unicodedata.combining(z))


def kod_jednotky(nazov: str | None) -> str:
    if not nazov:
        return JEDNOTKA_INAK
    kluc = bez_diakritiky(nazov).lower().replace(" ", "")
    return JEDNOTKY.get(kluc, JEDNOTKA_INAK)


def kod_uhrady(popis: str | None, ma_iban: bool) -> str:
    if popis:
        text = popis.strip().lower()
        # Skratky z faktury: PP = prevodny prikaz, H = hotovost, D = dobierka.
        skratky = {"pp": "30", "pk": "30", "h": "10", "hot": "10", "d": "42", "z": "97"}
        if text in skratky:
            return skratky[text]
        for kluce, kod in SPOSOBY_UHRADY:
            if any(k in text for k in kluce):
                return kod
    return "30" if ma_iban else UHRADA_INAK


def ocisti_ic_dph(hodnota: str | None, krajina: str | None) -> str | None:
    if not hodnota:
        return None
    text = re.sub(r"[\s.\-]", "", str(hodnota)).upper()
    if not text:
        return None
    if not re.match(r"^[A-Z]{2}", text):
        predpona = (krajina or "SK").upper()[:2]
        text = predpona + text
    return text


def ocisti_iban(hodnota: str | None) -> str | None:
    if not hodnota:
        return None
    text = re.sub(r"[\s.\-]", "", str(hodnota)).upper()
    return text or None


def ocisti_ico(hodnota: str | None) -> str | None:
    if not hodnota:
        return None
    text = re.sub(r"\D", "", str(hodnota))
    return text or None


def kod_krajiny(hodnota: str | None) -> str | None:
    """ISO kod krajiny z dvojpismenoveho kodu alebo nazvu. None, ked ho nepozna."""
    if not hodnota:
        return None
    text = re.sub(r"[^a-z]", "", bez_diakritiky(str(hodnota)).lower())
    if len(text) == 2:
        return text.upper()
    return KRAJINY.get(text)


# ---------------------------------------------------------------- pomocne XML


def _e(rodic, ns: str, nazov: str, text: str | None = None, **atributy):
    prvok = etree.SubElement(rodic, f"{{{ns}}}{nazov}")
    if text is not None:
        prvok.text = text
    for kluc, hodnota in atributy.items():
        if hodnota is not None:
            prvok.set(kluc.rstrip("_"), hodnota)
    return prvok


def cbc(rodic, nazov, text=None, **atributy):
    return _e(rodic, NS_CBC, nazov, text, **atributy)


def cac(rodic, nazov):
    return _e(rodic, NS_CAC, nazov)


# ---------------------------------------------------------------- strany


def firma_zo_strany(strana: Strana, inak_krajina: str = "SK") -> Firma:
    krajina = kod_krajiny(strana.krajina) or inak_krajina
    return Firma(
        nazov=(strana.nazov or "").strip(),
        ico=ocisti_ico(strana.ico) or "",
        dic=(strana.dic or "").strip(),
        ic_dph=ocisti_ic_dph(strana.ic_dph, krajina) or "",
        ulica=(strana.ulica or "").strip(),
        mesto=(strana.mesto or "").strip(),
        psc=re.sub(r"\s", "", strana.psc or ""),
        krajina=krajina,
        email=(strana.email or "").strip(),
        telefon=(strana.telefon or "").strip(),
    )


def _zapis_stranu(rodic, firma: Firma, endpoint_scheme: str) -> None:
    """Poradie elementov v cac:Party je dane UBL schemou."""
    party = cac(rodic, "Party")

    endpoint = firma.endpoint_id or firma.ic_dph
    if endpoint:
        cbc(party, "EndpointID", endpoint, schemeID=endpoint_scheme or None)

    if firma.ico:
        ident = cac(party, "PartyIdentification")
        cbc(ident, "ID", firma.ico)

    if firma.nazov:
        meno = cac(party, "PartyName")
        cbc(meno, "Name", firma.nazov)

    adresa = cac(party, "PostalAddress")
    if firma.ulica:
        cbc(adresa, "StreetName", firma.ulica)
    if firma.mesto:
        cbc(adresa, "CityName", firma.mesto)
    if firma.psc:
        cbc(adresa, "PostalZone", firma.psc)
    krajina = cac(adresa, "Country")
    cbc(krajina, "IdentificationCode", firma.krajina)

    if firma.ic_dph:
        schema_dph = cac(party, "PartyTaxScheme")
        cbc(schema_dph, "CompanyID", firma.ic_dph)
        tax = cac(schema_dph, "TaxScheme")
        cbc(tax, "ID", "VAT")

    pravna = cac(party, "PartyLegalEntity")
    cbc(pravna, "RegistrationName", firma.nazov or "Neuvedene")
    if firma.ico:
        cbc(pravna, "CompanyID", firma.ico)

    if firma.email or firma.telefon:
        kontakt = cac(party, "Contact")
        if firma.telefon:
            cbc(kontakt, "Telephone", firma.telefon)
        if firma.email:
            cbc(kontakt, "ElectronicMail", firma.email)


# ---------------------------------------------------------------- vypocty


@dataclass
class SkupinaDPH:
    kategoria: str
    sadzba: Decimal
    zaklad: Decimal
    dph: Decimal
    dovod: str | None


def kategoria(zadana: str | None, sadzba: Decimal, nulova: str = "AE") -> str:
    """Kategoria z faktury, alebo odvodena zo sadzby, ked na fakture nie je."""
    return zadana or ("S" if sadzba > 0 else nulova)


def skupiny_dph(
    faktura: Faktura,
    riadky_sumy: list[tuple[Riadok, Decimal, Decimal, str]],
    upozornenia: list[str],
    nulova: str = "AE",
) -> list[SkupinaDPH]:
    """Rekapitulacia DPH z faktury, alebo dopocitana z riadkov, ak na fakture nie je."""
    z_faktury: list[SkupinaDPH] = []
    nepouzitelne = 0
    for r in faktura.rekapitulacia_dph or []:
        sadzba = percento(r.sadzba_dph)
        zaklad = cislo(r.zaklad)
        if sadzba is None or zaklad is None:
            nepouzitelne += 1
            continue
        dph = cislo(r.dph)
        if dph is None:
            dph = zaokruhli(zaklad * sadzba / Decimal(100))
        z_faktury.append(
            SkupinaDPH(kategoria(r.kategoria_dph, sadzba, nulova), sadzba, zaklad, dph, r.dovod_oslobodenia)
        )

    if z_faktury and not nepouzitelne:
        return z_faktury

    if nepouzitelne:
        upozornenia.append(
            f"V rekapitulacii DPH sa nepodarilo precitat {nepouzitelne} riadkov, "
            "cela rekapitulacia sa preto dopocitala z fakturovanych riadkov."
        )
    else:
        upozornenia.append("Faktura nemala citatelnu rekapitulaciu DPH, dopocitala sa z riadkov.")
    zoskupene: dict[tuple[str, Decimal], Decimal] = {}
    for _, sadzba, suma, kat in riadky_sumy:
        kluc = (kat, sadzba)
        zoskupene[kluc] = zoskupene.get(kluc, NULA) + suma

    vysledok = []
    for (kat, sadzba), zaklad in sorted(zoskupene.items(), key=lambda x: (-x[0][1], x[0][0])):
        zaklad = zaokruhli(zaklad)
        vysledok.append(SkupinaDPH(kat, sadzba, zaklad, zaokruhli(zaklad * sadzba / Decimal(100)), None))
    return vysledok


def priprav_riadky(
    faktura: Faktura, upozornenia: list[str], nulova: str = "AE"
) -> list[tuple[Riadok, Decimal, Decimal, str]]:
    """Vrati zoznam (riadok, sadzba, suma bez DPH, kategoria) a dopocita chybajuce sumy."""
    pripravene = []
    for index, riadok in enumerate(faktura.riadky or [], start=1):
        mnozstvo = cislo(riadok.mnozstvo)
        cena = cislo(riadok.jednotkova_cena)
        suma = cislo(riadok.suma_bez_dph)

        if suma is None and mnozstvo is not None and cena is not None:
            suma = zaokruhli(mnozstvo * cena)
            upozornenia.append(f"Riadok {index}: suma bez DPH sa dopocitala z mnozstva a ceny.")
        if suma is None:
            upozornenia.append(f"Riadok {index}: chyba suma bez DPH, pouzila sa nula.")
            suma = NULA

        sadzba = percento(riadok.sadzba_dph)
        if sadzba is None:
            sadzba = NULA
            upozornenia.append(f"Riadok {index}: chybala sadzba DPH, pouzila sa nula.")

        if riadok.jednotka and kod_jednotky(riadok.jednotka) == JEDNOTKA_INAK:
            upozornenia.append(
                f"Riadok {index}: mernu jednotku '{riadok.jednotka}' nepoznam, "
                f"pouzil sa vseobecny kod {JEDNOTKA_INAK}."
            )

        pripravene.append((riadok, sadzba, suma, kategoria(riadok.kategoria_dph, sadzba, nulova)))
    return pripravene


def skontroluj_sucet(sucet_riadkov: Decimal, zaklad_celkom: Decimal) -> None:
    """BR-CO-10: suma riadkov sa musi rovnat zakladu dane na fakture.

    Ked sa lisi, nieco sa precitalo zle alebo sa stratil riadok. Tichou opravou by
    vznikla faktura s inou sumou, nez ma papierovy doklad, preto sa prevod zastavi.
    """
    if abs(sucet_riadkov - zaklad_celkom) > DVE:
        raise ChybaPrevodu(
            f"Sucet fakturovanych riadkov {d2(sucet_riadkov)} sa nezhoduje so zakladom dane "
            f"na fakture {d2(zaklad_celkom)}. Rozdiel je {d2(abs(sucet_riadkov - zaklad_celkom))}. "
            "Niektory riadok sa precital zle alebo chyba, XML sa preto nevytvorilo."
        )


# ---------------------------------------------------------------- hlavny prevod


def do_ubl(faktura: Faktura, cfg: Config) -> Vysledok:
    upozornenia: list[str] = []
    nast = cfg.nastavenia

    typ = faktura.typ_dokladu or "380"
    je_dobropis = typ in KODY_DOBROPISU
    ns_root = NS_CN if je_dobropis else NS_INV
    nazov_root = "CreditNote" if je_dobropis else "Invoice"
    nazov_riadku = "CreditNoteLine" if je_dobropis else "InvoiceLine"
    nazov_mnozstva = "CreditedQuantity" if je_dobropis else "InvoicedQuantity"

    mena = (faktura.mena or nast.mena or "EUR").strip().upper()[:3]

    dodavatel = firma_zo_strany(faktura.dodavatel)
    dodavatel = replace(
        dodavatel,
        endpoint_id=cfg.peppol.moj_endpoint_id or dodavatel.ic_dph or dodavatel.ico,
    )

    odberatel = firma_zo_strany(faktura.odberatel)
    odberatel = replace(odberatel, endpoint_id=odberatel.ic_dph or odberatel.ico)

    nulova = nast.kategoria_nulovej_dph
    riadky_sumy = priprav_riadky(faktura, upozornenia, nulova)
    if not riadky_sumy:
        raise ChybaPrevodu("Faktura neobsahuje ani jeden citatelny fakturovany riadok.")

    skupiny = skupiny_dph(faktura, riadky_sumy, upozornenia, nulova)

    sucet_riadkov = zaokruhli(sum((s for _, _, s, _ in riadky_sumy), NULA))
    zaklad_celkom = cislo(faktura.suma_bez_dph)
    if zaklad_celkom is None:
        zaklad_celkom = sucet_riadkov
    dph_celkom = cislo(faktura.suma_dph)
    if dph_celkom is None:
        dph_celkom = sum((s.dph for s in skupiny), NULA)
    s_dph_celkom = cislo(faktura.suma_s_dph)
    if s_dph_celkom is None:
        s_dph_celkom = zaklad_celkom + dph_celkom

    zaloha = cislo(faktura.zaplatena_zaloha) or NULA
    zaokruhlenie = cislo(faktura.zaokruhlenie) or NULA
    na_uhradu = cislo(faktura.na_uhradu)
    if na_uhradu is None:
        na_uhradu = s_dph_celkom - zaloha + zaokruhlenie

    skontroluj_sucet(sucet_riadkov, zaklad_celkom)

    root = etree.Element(
        f"{{{ns_root}}}{nazov_root}",
        nsmap={None: ns_root, "cac": NS_CAC, "cbc": NS_CBC},
    )

    cbc(root, "CustomizationID", CUSTOMIZATION_ID)
    cbc(root, "ProfileID", PROFILE_ID)
    cbc(root, "ID", (faktura.cislo_faktury or "").strip() or "NEZNAME")

    d_vystavenia = datum(faktura.datum_vystavenia)
    if not d_vystavenia:
        raise ChybaPrevodu("Z faktury sa nepodarilo precitat datum vystavenia.")
    cbc(root, "IssueDate", d_vystavenia)

    d_splatnosti = datum(faktura.datum_splatnosti)
    d_dodania = datum(faktura.datum_dodania)

    if je_dobropis:
        cbc(root, "CreditNoteTypeCode", typ)
    else:
        if d_splatnosti:
            cbc(root, "DueDate", d_splatnosti)
        cbc(root, "InvoiceTypeCode", typ)
    if faktura.poznamka:
        cbc(root, "Note", faktura.poznamka.strip())

    cbc(root, "DocumentCurrencyCode", mena)

    referencia = (
        (faktura.referencia_odberatela or "").strip()
        or (faktura.variabilny_symbol or "").strip()
        or (faktura.cislo_objednavky or "").strip()
    )
    if not referencia and nast.zaloha_referencie == "cislo_faktury":
        referencia = (faktura.cislo_faktury or "").strip()
        if referencia:
            upozornenia.append(
                "Faktura neobsahuje referenciu odberatela ani cislo objednavky. "
                "Ako referencia sa doplnilo cislo faktury, inak by Peppol subor neprisiel cez validaciu."
            )

    if referencia:
        cbc(root, "BuyerReference", referencia)
    else:
        upozornenia.append(
            "Faktura neobsahuje referenciu odberatela ani cislo objednavky. "
            "Peppol vyzaduje aspon jedno z nich, XML tuto poziadavku nesplna."
        )

    if faktura.cislo_objednavky:
        objednavka = cac(root, "OrderReference")
        cbc(objednavka, "ID", faktura.cislo_objednavky.strip())

    _zapis_stranu(cac(root, "AccountingSupplierParty"), dodavatel, cfg.peppol.moj_endpoint_scheme)
    _zapis_stranu(cac(root, "AccountingCustomerParty"), odberatel, cfg.peppol.odberatel_endpoint_scheme)

    if d_dodania:
        dodanie = cac(root, "Delivery")
        cbc(dodanie, "ActualDeliveryDate", d_dodania)

    iban = ocisti_iban(faktura.iban)
    kod = kod_uhrady(faktura.sposob_uhrady, bool(iban))
    if faktura.sposob_uhrady and kod == UHRADA_INAK:
        upozornenia.append(
            f"Formu uhrady '{faktura.sposob_uhrady}' nepoznam, v XML je kod {UHRADA_INAK}."
        )
    prostriedok = cac(root, "PaymentMeans")
    cbc(prostriedok, "PaymentMeansCode", kod)
    if faktura.variabilny_symbol:
        cbc(prostriedok, "PaymentID", faktura.variabilny_symbol.strip())
    if iban:
        ucet = cac(prostriedok, "PayeeFinancialAccount")
        cbc(ucet, "ID", iban)
        if faktura.bic:
            banka = cac(ucet, "FinancialInstitutionBranch")
            cbc(banka, "ID", re.sub(r"\s", "", faktura.bic).upper())
    else:
        upozornenia.append("Faktura neobsahovala citatelny IBAN.")

    if d_splatnosti:
        podmienky = cac(root, "PaymentTerms")
        cbc(podmienky, "Note", f"Splatnost {d_splatnosti}")

    tax_total = cac(root, "TaxTotal")
    cbc(tax_total, "TaxAmount", d2(dph_celkom), currencyID=mena)
    for skupina in skupiny:
        podskupina = cac(tax_total, "TaxSubtotal")
        cbc(podskupina, "TaxableAmount", d2(skupina.zaklad), currencyID=mena)
        cbc(podskupina, "TaxAmount", d2(skupina.dph), currencyID=mena)
        kat = cac(podskupina, "TaxCategory")
        cbc(kat, "ID", skupina.kategoria)
        cbc(kat, "Percent", d2(skupina.sadzba))
        if skupina.kategoria in KATEGORIE_NULOVEJ_DPH:
            cbc(kat, "TaxExemptionReason", skupina.dovod or KATEGORIE_NULOVEJ_DPH[skupina.kategoria])
        schema = cac(kat, "TaxScheme")
        cbc(schema, "ID", "VAT")

    suhrn = cac(root, "LegalMonetaryTotal")
    cbc(suhrn, "LineExtensionAmount", d2(sucet_riadkov), currencyID=mena)
    cbc(suhrn, "TaxExclusiveAmount", d2(zaklad_celkom), currencyID=mena)
    cbc(suhrn, "TaxInclusiveAmount", d2(s_dph_celkom), currencyID=mena)
    if zaloha:
        cbc(suhrn, "PrepaidAmount", d2(zaloha), currencyID=mena)
    if zaokruhlenie:
        cbc(suhrn, "PayableRoundingAmount", d2(zaokruhlenie), currencyID=mena)
    cbc(suhrn, "PayableAmount", d2(na_uhradu), currencyID=mena)

    for index, (riadok, sadzba, suma, kat) in enumerate(riadky_sumy, start=1):
        prvok = cac(root, nazov_riadku)
        cbc(prvok, "ID", str(riadok.poradie or index))
        mnozstvo = cislo(riadok.mnozstvo)
        cbc(
            prvok,
            nazov_mnozstva,
            d4(mnozstvo if mnozstvo is not None else Decimal(1)),
            unitCode=kod_jednotky(riadok.jednotka),
        )
        cbc(prvok, "LineExtensionAmount", d2(suma), currencyID=mena)

        polozka = cac(prvok, "Item")
        if riadok.popis and riadok.popis.strip() and riadok.popis.strip() != (riadok.nazov or "").strip():
            cbc(polozka, "Description", riadok.popis.strip())
        cbc(polozka, "Name", (riadok.nazov or riadok.popis or "Polozka").strip()[:200])
        dan_polozky = cac(polozka, "ClassifiedTaxCategory")
        cbc(dan_polozky, "ID", kat)
        cbc(dan_polozky, "Percent", d2(sadzba))
        schema = cac(dan_polozky, "TaxScheme")
        cbc(schema, "ID", "VAT")

        cena_prvok = cac(prvok, "Price")
        jednotkova = cislo(riadok.jednotkova_cena)
        if jednotkova is None:
            jednotkova = (suma / mnozstvo) if mnozstvo else suma
        # BR-27: cena za jednotku je vzdy kladna, zapornost dokladu nesie suma riadku.
        cbc(cena_prvok, "PriceAmount", d_cena(abs(jednotkova)), currencyID=mena)

    xml = etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return Vysledok(xml=xml, upozornenia=upozornenia)

