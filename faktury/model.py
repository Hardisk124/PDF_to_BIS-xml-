"""Datovy model faktury. Vyplna ho vylucne sablona.py z poloh slov v PDF.

Vsetky sumy su retazce v tvare precitanom z faktury. Na Decimal sa prevadzaju
az v ubl.py, aby sa po ceste nic nestratilo zaokruhlovanim cez float.

Ziadne pole nema predvolenu hodnotu zamerne. Chybajuci udaj tak musi byt
vyslovne None a neda sa prehliadnut tym, ze by sa ticho doplnil prazdny retazec.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

KategoriaDPH = Literal["S", "Z", "E", "AE", "K", "G", "O"]

POPIS_KATEGORIE = (
    "Kategoria DPH podla EN 16931: "
    "S = standardna sadzba, Z = nulova sadzba, E = oslobodene od dane, "
    "AE = prenesenie danovej povinnosti (reverse charge), "
    "K = dodanie do EU (intra-community), G = vyvoz mimo EU, "
    "O = mimo rozsahu DPH (dodavatel nie je platca DPH)."
)


class Strana(BaseModel):
    """Dodavatel alebo odberatel."""

    nazov: Optional[str] = Field(description="Obchodne meno firmy.")
    ico: Optional[str] = Field(description="ICO, len cislice.")
    dic: Optional[str] = Field(description="DIC, bez predpony krajiny.")
    ic_dph: Optional[str] = Field(
        description="IC DPH vratane predpony krajiny, napriklad SK2020123456. Null ak nie je platca DPH."
    )
    ulica: Optional[str] = Field(description="Ulica a cislo domu.")
    mesto: Optional[str] = Field(description="Mesto alebo obec.")
    psc: Optional[str] = Field(description="PSC bez medzier.")
    krajina: Optional[str] = Field(
        description="Dvojpismenovy ISO 3166-1 alpha-2 kod krajiny, napriklad SK, CZ, DE."
    )
    email: Optional[str] = Field(description="Kontaktny e-mail.")
    telefon: Optional[str] = Field(description="Kontaktny telefon.")


class Riadok(BaseModel):
    """Jeden fakturovany riadok."""

    poradie: int = Field(description="Poradove cislo riadku od 1.")
    nazov: Optional[str] = Field(description="Kratky nazov polozky alebo sluzby.")
    popis: Optional[str] = Field(description="Dlhsi popis, ak sa lisi od nazvu. Inak null.")
    mnozstvo: Optional[str] = Field(description="Fakturovane mnozstvo ako cislo, napriklad 2 alebo 1.5.")
    jednotka: Optional[str] = Field(
        description="Merna jednotka tak, ako je uvedena na fakture, napriklad ks, hod, kg, m2, mesiac."
    )
    jednotkova_cena: Optional[str] = Field(description="Jednotkova cena bez DPH.")
    suma_bez_dph: Optional[str] = Field(description="Suma riadku bez DPH po odpocitani zliav.")
    sadzba_dph: Optional[str] = Field(description="Sadzba DPH riadku v percentach ako cislo, napriklad 23 alebo 0.")
    kategoria_dph: Optional[KategoriaDPH] = Field(description=POPIS_KATEGORIE)


class RekapitulaciaDPH(BaseModel):
    """Jeden riadok rekapitulacie DPH, teda sucet za jednu sadzbu."""

    sadzba_dph: Optional[str] = Field(description="Sadzba v percentach ako cislo.")
    zaklad: Optional[str] = Field(description="Zaklad dane pre tuto sadzbu.")
    dph: Optional[str] = Field(description="Suma DPH pre tuto sadzbu.")
    kategoria_dph: Optional[KategoriaDPH] = Field(description=POPIS_KATEGORIE)
    dovod_oslobodenia: Optional[str] = Field(
        description="Textovy dovod oslobodenia alebo preneseia danovej povinnosti, ak je na fakture uvedeny. Inak null."
    )


class Faktura(BaseModel):
    """Cela faktura tak, ako je citatelna z PDF."""

    cislo_faktury: Optional[str] = Field(description="Cislo faktury.")
    datum_vystavenia: Optional[str] = Field(description="Datum vystavenia vo formate YYYY-MM-DD.")
    datum_splatnosti: Optional[str] = Field(description="Datum splatnosti vo formate YYYY-MM-DD.")
    datum_dodania: Optional[str] = Field(
        description="Datum dodania alebo datum danovej povinnosti vo formate YYYY-MM-DD."
    )
    typ_dokladu: Optional[Literal["380", "381", "384", "389"]] = Field(
        description=(
            "Kod typu dokladu: 380 = bezna faktura, 381 = dobropis alebo kreditna nota, "
            "384 = opravna faktura, 389 = samofakturacia."
        )
    )
    mena: Optional[str] = Field(description="Trojpismenovy ISO 4217 kod meny, napriklad EUR.")

    dodavatel: Strana = Field(description="Vystavovatel faktury.")
    odberatel: Strana = Field(description="Prijemca faktury.")

    variabilny_symbol: Optional[str] = Field(description="Variabilny symbol platby.")
    konstantny_symbol: Optional[str] = Field(description="Konstantny symbol platby.")
    cislo_objednavky: Optional[str] = Field(description="Cislo objednavky odberatela, ak je uvedene.")
    referencia_odberatela: Optional[str] = Field(
        description="Ina referencia pozadovana odberatelom, napriklad cislo zmluvy alebo stredisko."
    )

    iban: Optional[str] = Field(description="IBAN uctu dodavatela bez medzier.")
    bic: Optional[str] = Field(description="BIC alebo SWIFT banky dodavatela.")
    sposob_uhrady: Optional[str] = Field(
        description="Sposob uhrady tak, ako je na fakture, napriklad prevodom, hotovost, karta, dobierka, inkaso."
    )
    poznamka: Optional[str] = Field(description="Doplnujuca poznamka z faktury, ktora patri k celemu dokladu.")

    riadky: list[Riadok] = Field(description="Vsetky fakturovane riadky v poradi, v akom su na fakture.")
    rekapitulacia_dph: list[RekapitulaciaDPH] = Field(
        description="Rekapitulacia DPH po sadzbach. Ak nie je na fakture uvedena, vrat prazdny zoznam."
    )

    suma_bez_dph: Optional[str] = Field(description="Celkovy zaklad dane, teda sucet vsetkych riadkov bez DPH.")
    suma_dph: Optional[str] = Field(description="Celkova suma DPH.")
    suma_s_dph: Optional[str] = Field(description="Celkova suma vratane DPH.")
    zaplatena_zaloha: Optional[str] = Field(description="Uz uhradena zaloha, ktora sa odpocitava. Inak null.")
    zaokruhlenie: Optional[str] = Field(description="Suma zaokruhlenia, ak je na fakture samostatne. Inak null.")
    na_uhradu: Optional[str] = Field(description="Suma, ktoru ma odberatel realne zaplatit.")
