"""Nacitanie a zapis konfiguracie.

Udaje oboch stran sa citaju priamo z faktury, preto tu zostavaju len tie
nastavenia, ktore sa z dokladu vycitat nedaju: Peppol identifikatory
a niekolko rozhodnuti, ktore zavisia od dohody s uctovnikom.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cesty

# Jediny zdroj pravdy pre kategorie nulovej DPH. Pouziva ho validacia konfiguracie,
# ponuka v nastaveniach aj nahradny text dovodu oslobodenia v XML.
KATEGORIE_NULOVEJ_DPH = {
    "AE": "Prenesenie danovej povinnosti na prijemcu",
    "K": "Dodanie do ineho clenskeho statu EU",
    "G": "Vyvoz mimo EU",
    "E": "Oslobodene od dane",
    "O": "Mimo rozsahu DPH",
}


@dataclass(frozen=True)
class Peppol:
    """Identifikatory, ktore Peppol vyzaduje a na fakture nie su."""

    moj_endpoint_id: str = ""        # prazdne znamena pouzit IC DPH z faktury
    moj_endpoint_scheme: str = ""
    odberatel_endpoint_scheme: str = ""


@dataclass(frozen=True)
class Nastavenia:
    mena: str = "EUR"
    # Akou kategoriou EN 16931 oznacit nulovu sadzbu. Zavisi od toho, ci ide
    # o prenesenie danovej povinnosti, dodanie do EU alebo vyvoz.
    kategoria_nulovej_dph: str = "AE"
    # Peppol vyzaduje referenciu odberatela alebo cislo objednavky. Ked na
    # fakture ani jedno nie je, doplni sa cislo faktury, inak by bol subor neplatny.
    zaloha_referencie: str = "cislo_faktury"
    # Predvolene zapnute: faktura, ktora neprejde validaciou, sa nema dostat
    # do vystupu, lebo odtial ide zakaznikovi.
    striktna_validacia: bool = True
    # Ktore XML vytvarat. Aspon jeden format musi zostat zapnuty.
    vystup_ubl: bool = True
    vystup_pohoda: bool = True
    # Predkontacia v POHODE, napriklad 602001_Prepr. Prazdna sa do XML nezapise.
    predkontacia: str = "602001_Prepr"


@dataclass(frozen=True)
class Config:
    peppol: Peppol = field(default_factory=Peppol)
    nastavenia: Nastavenia = field(default_factory=Nastavenia)
    # Vyplnene, ked sa subor s nastaveniami neda precitat. Prazdne znamena v poriadku.
    problem: str = ""

    def chybajuce(self) -> list[str]:
        """Nastavenia, bez ktorych vznikne neplatny Peppol subor."""
        chyba = []
        if self.problem:
            chyba.append(self.problem)
        # EAS kody potrebuje len Peppol, POHODA ich nepouziva.
        if self.nastavenia.vystup_ubl and not self.peppol.moj_endpoint_scheme:
            chyba.append("EAS kod tvojej firmy")
        if self.nastavenia.vystup_ubl and not self.peppol.odberatel_endpoint_scheme:
            chyba.append("EAS kod odberatela")
        return chyba


def nacitaj(cesta: Path | None = None) -> Config:
    """Nacita konfiguraciu. Chybajuci subor nie je chyba, vrati sa predvolena."""
    cesta = cesta or cesty.config()
    if not cesta.exists():
        return Config()

    try:
        with cesta.open("rb") as f:
            surove = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as chyba:
        # Poskodeny subor nesmie zhodit cely program. Vratia sa predvolene
        # hodnoty a pouzivatel sa o probleme dozvie v nastaveniach.
        return Config(problem=f"Subor s nastaveniami {cesta.name} sa neda precitat: {chyba}")

    return zo_slovnika({**surove.get("peppol", {}), **surove.get("nastavenia", {})})


RIADIACE_ZNAKY = re.compile(r"[\x00-\x1f\x7f]")


def _ocisti(text: str) -> str:
    """Odstrani riadiace znaky. Novy riadok vo vnutri hodnoty by rozbil TOML."""
    return RIADIACE_ZNAKY.sub("", text).strip()


def _toml_hodnota(hodnota) -> str:
    if isinstance(hodnota, bool):
        return "true" if hodnota else "false"
    if isinstance(hodnota, int):
        return str(hodnota)
    escapovana = _ocisti(str(hodnota)).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escapovana}"'


def uloz(cfg: Config, cesta: Path | None = None) -> Path:
    """Zapise konfiguraciu cez docasny subor, aby sa pri pade nic nestratilo."""
    cesta = cesta or cesty.config()
    cesta.parent.mkdir(parents=True, exist_ok=True)

    riadky = [
        "# Nastavenia prevodnika faktur do Peppol BIS Billing 3.0",
        "",
        "[peppol]",
        *(f"{k} = {_toml_hodnota(v)}" for k, v in asdict(cfg.peppol).items()),
        "",
        "[nastavenia]",
        *(f"{k} = {_toml_hodnota(v)}" for k, v in asdict(cfg.nastavenia).items()),
    ]

    docasny = cesta.with_suffix(".toml.tmp")
    docasny.write_text("\n".join(riadky) + "\n", encoding="utf-8")
    docasny.replace(cesta)
    return cesta


def ploche(cfg: Config) -> dict:
    """Vsetky nastavenia v jednom plochom slovniku, ako ich pozna formular."""
    return {**asdict(cfg.peppol), **asdict(cfg.nastavenia)}


def zo_slovnika(udaje: dict) -> Config:
    """Postavi Config z plocheho slovnika (formular alebo config.toml)."""

    def txt(kluc: str, inak: str = "") -> str:
        return _ocisti(str(udaje.get(kluc, inak)))

    kategoria = txt("kategoria_nulovej_dph", "AE").upper()
    vystup_ubl = bool(udaje.get("vystup_ubl", True))
    vystup_pohoda = bool(udaje.get("vystup_pohoda", True))
    return Config(
        peppol=Peppol(
            moj_endpoint_id=txt("moj_endpoint_id"),
            moj_endpoint_scheme=txt("moj_endpoint_scheme"),
            odberatel_endpoint_scheme=txt("odberatel_endpoint_scheme"),
        ),
        nastavenia=Nastavenia(
            mena=txt("mena", "EUR").upper() or "EUR",
            kategoria_nulovej_dph=kategoria if kategoria in KATEGORIE_NULOVEJ_DPH else "AE",
            zaloha_referencie=txt("zaloha_referencie", "cislo_faktury"),
            striktna_validacia=bool(udaje.get("striktna_validacia", True)),
            # Bez formatu by sa nic nevytvorilo, preto vtedy ostava zapnuty UBL.
            vystup_ubl=vystup_ubl or not vystup_pohoda,
            vystup_pohoda=vystup_pohoda,
            predkontacia=txt("predkontacia", "602001_Prepr"),
        ),
    )
