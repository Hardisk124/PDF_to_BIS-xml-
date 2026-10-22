"""Citanie faktury GRIMMEL, s.r.o. podla pevneho rozlozenia stran.

Vzor je jeden a nemeni sa, preto sa cita podla suradnic stlpcov, nie podla
regularnych vyrazov nad plochym textom. Ploche riesenie by zlyhalo uz na
rekapitulacii DPH, ktora je na tej istej riadkovej urovni ako zoznam
vlastnych objednavok vlavo.

Ked sa vzor niekedy zmeni, upravuju sa konstanty nizsie, nie zvysok programu.
Nove suradnice zistis takto (jednotka je bod, teda 1/72 palca od laveho horneho rohu):

    python -c "
    import pdfplumber
    with pdfplumber.open('vstup/faktura.pdf') as pdf:
        for s in pdf.pages[0].extract_words():
            print(round(s['top'],1), round(s['x0']), s['text'])
    "
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

# pdfminer sa staznuje na drobnosti v takmer kazdej realnej fakture.
warnings.filterwarnings("ignore", module="pdfminer")
warnings.filterwarnings("ignore", module="pdfplumber")

from .model import Faktura, RekapitulaciaDPH, Riadok, Strana
from .ubl import kod_krajiny

# --- suradnice stlpcov v bodoch od laveho okraja (strana je siroka 595) ------

X_POPIS_DO = 315.0        # text polozky konci tu
X_CENA = (315.0, 360.0)   # jednotkova cena
X_MNOZSTVO = (360.0, 415.0)
X_DPH = (415.0, 470.0)
X_SUMA = (470.0, 600.0)

X_REKAP_SADZBA = (325.0, 395.0)
X_REKAP_ZAKLAD = (395.0, 470.0)
X_REKAP_DPH = (470.0, 600.0)

X_ODBERATEL_OD = 330.0    # blok odberatela je zarovnany sem
X_DODAVATEL = (100.0, 220.0)   # nazov, ulica a mesto dodavatela
X_DODAVATEL_ID = 220.0         # ICO, DIC a IC DPH dodavatela

# Blok dodavatela zabera od popisky "Dodavatel:" najviac tolkoto riadkov.
RIADKOV_DODAVATELA = 8
# Identifikatory odberatela zacinaju vlavo od jeho adresneho bloku.
X_ODBERATEL_ID_OD = X_ODBERATEL_OD - 100.0

TOLERANCIA_RIADKU = 4.0   # o kolko bodov sa smu lisit slova v tom istom riadku
MAX_STRAN = 30            # poistka proti PDF s tisickami stran

VZOR_ICO = re.compile(r"IČO:\s*(\d+)")
VZOR_DIC = re.compile(r"DIČ:\s*(\d{6,})")
VZOR_IC_DPH = re.compile(r"DPH:\s*([A-Z]{2}\d+)")


class ChybaSablony(ValueError):
    """Faktura nezodpoveda ocakavanemu vzoru."""


@dataclass
class Slovo:
    text: str
    x0: float
    x1: float
    top: float


@dataclass
class RiadokStrany:
    top: float
    slova: list[Slovo] = field(default_factory=list)

    def text(self, x_od: float = 0.0, x_do: float = 10_000.0) -> str:
        vybrane = [s for s in self.slova if x_od <= s.x0 < x_do]
        return " ".join(s.text for s in sorted(vybrane, key=lambda s: s.x0)).strip()

    def za(self, popiska: str, x_do: float = 10_000.0) -> str:
        """Text napravo od zadanej popisky v tom istom riadku."""
        konec = self._konec_popisky(popiska)
        if konec is None:
            return ""
        return self.text(konec + 1.0, x_do)

    def _konec_popisky(self, popiska: str) -> float | None:
        slova_popisky = popiska.split()
        usporiadane = sorted(self.slova, key=lambda s: s.x0)
        for i in range(len(usporiadane) - len(slova_popisky) + 1):
            vyrez = usporiadane[i:i + len(slova_popisky)]
            if all(a.text == b for a, b in zip(vyrez, slova_popisky)):
                return vyrez[-1].x1
        return None

    def obsahuje(self, uryvok: str) -> bool:
        return uryvok.lower() in self.text().lower()


def _riadky_strany(strana) -> list[RiadokStrany]:
    """Zoskupi slova do vizualnych riadkov podla zvislej polohy."""
    slova = [
        Slovo(s["text"], float(s["x0"]), float(s["x1"]), float(s["top"]))
        for s in strana.extract_words(use_text_flow=False, keep_blank_chars=False)
    ]
    riadky: list[RiadokStrany] = []
    for slovo in sorted(slova, key=lambda s: (s.top, s.x0)):
        if riadky and slovo.top - riadky[-1].top <= TOLERANCIA_RIADKU:
            riadky[-1].slova.append(slovo)
        else:
            riadky.append(RiadokStrany(top=slovo.top, slova=[slovo]))
    return riadky


def _najdi(riadky: list[RiadokStrany], uryvok: str, od: int = 0) -> int | None:
    for i in range(od, len(riadky)):
        if riadky[i].obsahuje(uryvok):
            return i
    return None


def _za(riadky: list[RiadokStrany], popiska: str, x_do: float = 10_000.0) -> str | None:
    """Text za popiskou v prvom riadku, ktory ju obsahuje."""
    index = _najdi(riadky, popiska)
    if index is None:
        return None
    return riadky[index].za(popiska, x_do) or None


def _identifikatory(text: str) -> tuple[str | None, str | None, str | None]:
    """ICO, DIC a IC DPH z textu, kazdy alebo None."""
    def hladaj(vzor: re.Pattern, kde: str) -> str | None:
        m = vzor.search(kde)
        return m.group(1) if m else None

    return hladaj(VZOR_ICO, text), hladaj(VZOR_DIC, text), hladaj(VZOR_IC_DPH, text.replace(" ", ""))


def _je_cislo(text: str) -> bool:
    return bool(re.search(r"\d", text)) and bool(re.match(r"^[\d\s.,+-]+[A-Za-z%]{0,4}$", text.strip()))


TVAR_SUMY = re.compile(r"^-?\d+(\.\d{1,4})?$")


def _suma(text: str) -> str | None:
    """Vytiahne cislo z retazca ako '6 724.80EUR'. Vrati ho v tvare 6724.80.

    Na tomto vzore su tisice oddelene medzerou a desatiny bodkou, takze
    medzery sa zahadzuju a bodka je vzdy desatinna. Ked vysledok nema tvar
    cisla, vrati None a volajuci to ohlasi ako chybu vzoru.
    """
    if not text:
        return None
    ocistene = re.sub(r"[^\d,.\-]", "", text.replace(" ", "")).replace(",", ".")
    return ocistene if TVAR_SUMY.match(ocistene) else None


def _mnozstvo_a_jednotka(text: str) -> tuple[str | None, str | None]:
    """Rozdeli '25.502t' alebo '2 500 ks' na cislo a mernu jednotku."""
    if not text:
        return None, None
    m = re.match(r"^\s*([\d\s.,]+?)\s*([A-Za-zá-žÁ-Ž.%]*)\s*$", text)
    if not m:
        return None, None
    return _suma(m.group(1)), (m.group(2).strip() or None)


def _overene(hodnota: str | None, nazov: str, poradie: int) -> str:
    if hodnota is None:
        raise ChybaSablony(f"{nazov.capitalize()} v riadku {poradie} sa neda precitat.")
    return hodnota


def _psc_a_mesto(text: str) -> tuple[str | None, str | None]:
    m = re.match(r"^\s*(\d{3}\s?\d{2})\s+(.+?)\s*$", text)
    if m:
        return m.group(1).replace(" ", ""), m.group(2)
    return None, (text.strip() or None)


def _krajina(text: str, ic_dph: str | None) -> str | None:
    if ic_dph and re.match(r"^[A-Z]{2}", ic_dph):
        return ic_dph[:2]
    return kod_krajiny(text)


# --- jednotlive bloky -------------------------------------------------------


def _dodavatel(riadky: list[RiadokStrany]) -> Strana:
    zaciatok = _najdi(riadky, "Dodávateľ:")
    if zaciatok is None:
        raise ChybaSablony("Na fakture nie je blok dodavatela.")

    nazov = ulica = mesto_riadok = ""
    ico = dic = ic_dph = None

    for riadok in riadky[zaciatok:zaciatok + RIADKOV_DODAVATELA]:
        vlavo = riadok.text(*X_DODAVATEL)
        vpravo = riadok.text(X_DODAVATEL_ID)

        if not nazov and vlavo:
            nazov = vlavo
        elif not ulica and vlavo:
            ulica = vlavo
        elif not mesto_riadok and vlavo:
            mesto_riadok = vlavo

        nove_ico, nove_dic, nove_ic_dph = _identifikatory(vpravo)
        ico, dic, ic_dph = ico or nove_ico, dic or nove_dic, ic_dph or nove_ic_dph

    # Mesto dodavatela ma tvar "SK - 971 01 Prievidza".
    krajina = psc = mesto = None
    m = re.match(r"^\s*([A-Z]{2})\s*-\s*(\d{3}\s?\d{2})\s+(.+?)\s*$", mesto_riadok)
    if m:
        krajina, psc, mesto = m.group(1), m.group(2).replace(" ", ""), m.group(3)
    else:
        psc, mesto = _psc_a_mesto(mesto_riadok)

    if not nazov or not ulica:
        raise ChybaSablony("Nazov alebo ulica dodavatela sa neda precitat.")
    if not psc or not mesto:
        raise ChybaSablony(
            f"Riadok s adresou dodavatela '{mesto_riadok}' nema ocakavany tvar "
            "'SK - 971 01 Prievidza'."
        )
    krajina = krajina or (ic_dph[:2] if ic_dph else None)
    if not krajina:
        raise ChybaSablony("Krajinu dodavatela sa nepodarilo urcit.")

    return Strana(
        nazov=nazov, ico=ico, dic=dic, ic_dph=ic_dph,
        ulica=ulica, mesto=mesto, psc=psc, krajina=krajina,
        email=None, telefon=None,
    )


def _odberatel(riadky: list[RiadokStrany]) -> Strana:
    zaciatok = _najdi(riadky, "Odberateľ:")
    if zaciatok is None:
        raise ChybaSablony("Na fakture nie je blok odberatela.")

    # Riadok s identifikatormi ukoncuje adresny blok.
    koniec = _najdi(riadky, "IČ DPH:", zaciatok + 1)
    if koniec is None:
        raise ChybaSablony("Na fakture chyba riadok s ICO a IC DPH odberatela.")

    adresa = [
        riadky[i].text(X_ODBERATEL_OD)
        for i in range(zaciatok + 1, koniec)
        if riadky[i].text(X_ODBERATEL_OD)
    ]
    ico, dic, ic_dph = _identifikatory(riadky[koniec].text(X_ODBERATEL_ID_OD))

    if len(adresa) < 3:
        raise ChybaSablony(
            f"Adresa odberatela ma {len(adresa)} riadkov, ocakavaju sa aspon tri "
            "(nazov, ulica, PSC a mesto)."
        )

    nazov, ulica = adresa[0], adresa[1]
    psc, mesto = _psc_a_mesto(adresa[2])
    if not psc or not mesto:
        raise ChybaSablony(
            f"Riadok '{adresa[2]}' v adrese odberatela nema ocakavany tvar 'PSC mesto'."
        )
    riadok_krajiny = adresa[3] if len(adresa) > 3 else ""
    krajina = _krajina(riadok_krajiny, ic_dph)
    if not krajina:
        raise ChybaSablony(
            f"Krajinu odberatela sa nepodarilo urcit z riadku '{riadok_krajiny}' ani z IC DPH."
        )

    return Strana(
        nazov=nazov, ico=ico, dic=dic, ic_dph=ic_dph,
        ulica=ulica, mesto=mesto, psc=psc, krajina=krajina,
        email=None, telefon=None,
    )


def _polozky(riadky: list[RiadokStrany], prva_strana: bool) -> tuple[list[Riadok], str | None]:
    """Vrati fakturovane riadky jednej strany a kod meny.

    Na prvej strane musi byt hlavicka tabulky, inak to nie je ocakavany vzor.
    Na dalsich stranach hlavicka ani suhrn byt nemusia, polozky sa poznaju
    podla tvaru riadku. Prazdny zoznam je platny vysledok, nie chyba.
    """
    zaciatok = _najdi(riadky, "Označenie dodávky")
    koniec = _najdi(riadky, "Cena s DPH celkom")
    if prva_strana and zaciatok is None:
        raise ChybaSablony("Na fakture sa nenasla tabulka poloziek.")

    od = 0 if zaciatok is None else zaciatok + 1
    do = len(riadky) if koniec is None else koniec

    mena = None
    polozky: list[Riadok] = []
    popisy: list[list[str]] = []

    for riadok in riadky[od:do]:
        cena = [s for s in riadok.slova if X_CENA[0] <= s.x0 < X_CENA[1] and _je_cislo(s.text)]
        suma = [s for s in riadok.slova if X_SUMA[0] <= s.x0 < X_SUMA[1]]
        dph = riadok.text(*X_DPH)
        mnozstvo = riadok.text(*X_MNOZSTVO)

        je_polozka = bool(cena) and "%" in dph and bool(mnozstvo) and any(_je_cislo(s.text) for s in suma)

        if je_polozka:
            suma_text = " ".join(s.text for s in sorted(suma, key=lambda s: s.x0))
            if mena is None:
                m = re.search(r"([A-Z]{3})\s*$", suma_text)
                mena = m.group(1) if m else None

            pocet, jednotka = _mnozstvo_a_jednotka(mnozstvo)
            if pocet is None:
                raise ChybaSablony(
                    f"Mnozstvo '{mnozstvo}' v riadku {len(polozky) + 1} sa neda precitat."
                )
            polozky.append(
                Riadok(
                    poradie=len(polozky) + 1,
                    nazov=None, popis=None,
                    mnozstvo=pocet,
                    jednotka=jednotka,
                    jednotkova_cena=_overene(_suma(riadok.text(*X_CENA)), "jednotkova cena", len(polozky) + 1),
                    suma_bez_dph=_overene(_suma(suma_text), "suma riadku", len(polozky) + 1),
                    sadzba_dph=re.sub(r"[^\d.,]", "", dph) or "0",
                    kategoria_dph=None,
                )
            )
            popisy.append([riadok.text(0, X_POPIS_DO)])
        elif polozky and riadok.text(0, X_POPIS_DO) and not riadok.text(X_POPIS_DO):
            # Pokracovanie popisu predchadzajucej polozky na dalsom riadku.
            popisy[-1].append(riadok.text(0, X_POPIS_DO))

    if prva_strana and not polozky:
        raise ChybaSablony("V tabulke poloziek nie je ani jeden citatelny riadok.")

    for polozka, casti in zip(polozky, popisy):
        cely = " ".join(c for c in casti if c).strip()
        polozka.nazov = cely[:200] or "Polozka"
        polozka.popis = cely if len(cely) > 200 else None

    return polozky, mena


def _rekapitulacia(riadky: list[RiadokStrany]) -> tuple[list[RekapitulaciaDPH], dict[str, str | None]]:
    zaciatok = _najdi(riadky, "Sadzba")
    if zaciatok is None:
        raise ChybaSablony("Na fakture sa nenasla rekapitulacia DPH.")

    sadzby: list[RekapitulaciaDPH] = []
    suhrn: dict[str, str | None] = {"zaklad": None, "dph": None, "zaokruhlenie": None, "zaloha": None}

    for riadok in riadky[zaciatok + 1:]:
        # Riadok "Ciastka k uhrade" tabulku uzatvara, nizsie uz ziadne sadzby nie su.
        if riadok.obsahuje("Čiastka k úhrade"):
            break
        vpravo = riadok.text(X_REKAP_SADZBA[0])
        zaklad = _suma(riadok.text(*X_REKAP_ZAKLAD))
        dph = _suma(riadok.text(*X_REKAP_DPH))

        if riadok.obsahuje("Zaokrúhl."):
            suhrn["zaokruhlenie"] = zaklad or dph
            continue
        if riadok.obsahuje("Záloha"):
            suhrn["zaloha"] = zaklad or dph
            continue
        if vpravo.startswith("Spolu"):
            suhrn["zaklad"], suhrn["dph"] = zaklad, dph
            continue

        m = re.match(r"^(\d+)\s*%", vpravo)
        if m and zaklad:
            sadzba = m.group(1)
            sadzby.append(
                RekapitulaciaDPH(
                    sadzba_dph=sadzba, zaklad=zaklad,
                    dph=dph or "0.00", kategoria_dph=None, dovod_oslobodenia=None,
                )
            )

    return sadzby, suhrn


def _dovod_oslobodenia(riadky: list[RiadokStrany]) -> str | None:
    """Najde vetu o oslobodeni od dane pod tabulkou poloziek."""
    zaciatok = _najdi(riadky, "Cena s DPH celkom")
    koniec = _najdi(riadky, "Sadzba")
    if zaciatok is None or koniec is None:
        return None
    veta = " ".join(riadky[i].text() for i in range(zaciatok + 1, koniec))
    if "oslobodené od dane" not in veta and "193" not in veta:
        return None
    zaciatok_vety = veta.find("Dodanie")
    if zaciatok_vety >= 0:
        veta = veta[zaciatok_vety:]
    # Pod vetou byvaju dalsie popisky, ktore do dovodu oslobodenia nepatria.
    for zarazka in ("Pečiatka", "Príloha", "Vystavil", "Naša objed."):
        index = veta.find(zarazka)
        if index > 0:
            veta = veta[:index]
    return " ".join(veta.split()) or None


# --- hlavna funkcia ---------------------------------------------------------


def precitaj(cesta: Path) -> Faktura:
    """Precita fakturu podla pevneho vzoru. Vyhodi ChybaSablony, ak vzor nesedi."""
    try:
        with pdfplumber.open(cesta) as pdf:
            if not pdf.pages:
                raise ChybaSablony("PDF neobsahuje ziadnu stranu.")
            if len(pdf.pages) > MAX_STRAN:
                raise ChybaSablony(
                    f"PDF ma {len(pdf.pages)} stran, spracuva sa najviac {MAX_STRAN}."
                )
            strany = [_riadky_strany(s) for s in pdf.pages]
    except ChybaSablony:
        raise
    except Exception as chyba:
        raise ChybaSablony(
            f"Subor sa neda otvorit ako PDF ({type(chyba).__name__}). "
            "Je poskodeny alebo to nie je PDF."
        ) from chyba

    hlavicka = strany[0]
    posledna = strany[-1]

    cislo = _za(hlavicka, "Faktúra číslo:")
    if not cislo:
        raise ChybaSablony("Na fakture sa nenaslo cislo faktury.")

    polozky: list[Riadok] = []
    mena = None
    for index, strana in enumerate(strany):
        cast, mena_strany = _polozky(strana, prva_strana=index == 0)
        for polozka in cast:
            polozka.poradie = len(polozky) + 1
            polozky.append(polozka)
        mena = mena or mena_strany
    if not polozky:
        raise ChybaSablony("Na fakture sa nenasla ani jedna polozka.")

    sadzby, suhrn = _rekapitulacia(posledna)
    dovod = _dovod_oslobodenia(posledna)

    s_dph = _suma(_za(posledna, "Cena s DPH celkom") or "")
    na_uhradu = _suma(_za(posledna, "Čiastka k úhrade") or "")

    return Faktura(
        cislo_faktury=cislo,
        datum_vystavenia=_za(hlavicka, "Deň vystavenia faktúry:"),
        datum_splatnosti=_za(hlavicka, "Deň splatnosti:"),
        datum_dodania=_za(hlavicka, "Deň dodania služby:"),
        typ_dokladu="380",
        mena=mena or "EUR",
        dodavatel=_dodavatel(hlavicka).model_copy(update={
            "email": _za(posledna, "E-mail:", 300.0),
            "telefon": _za(posledna, "Telefón:", 300.0),
        }),
        odberatel=_odberatel(hlavicka),
        variabilny_symbol=None,
        konstantny_symbol=_za(hlavicka, "Konšt. symbol:"),
        cislo_objednavky=_za(hlavicka, "Objednávka číslo:"),
        referencia_odberatela=None,
        iban=_za(hlavicka, "IBAN:", 300.0),
        bic=_za(hlavicka, "BIC:", 300.0),
        sposob_uhrady=_za(hlavicka, "Forma úhrady:"),
        poznamka=dovod,
        riadky=polozky,
        rekapitulacia_dph=[
            RekapitulaciaDPH(
                sadzba_dph=s.sadzba_dph, zaklad=s.zaklad, dph=s.dph,
                kategoria_dph=s.kategoria_dph, dovod_oslobodenia=dovod,
            )
            for s in sadzby
        ],
        suma_bez_dph=suhrn["zaklad"],
        suma_dph=suhrn["dph"],
        suma_s_dph=s_dph,
        zaplatena_zaloha=suhrn["zaloha"],
        zaokruhlenie=suhrn["zaokruhlenie"],
        na_uhradu=na_uhradu or s_dph,
    )
