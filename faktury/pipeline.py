"""Spojenie krokov: PDF -> text -> extrakcia -> UBL -> validacia -> zapis."""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .sablona import ChybaSablony, precitaj
from .ubl import ChybaPrevodu, do_ubl
from .validate import skontroluj, skontroluj_xsd, xsd_dostupne


@dataclass
class Vysledok:
    subor: Path
    ok: bool
    xml: bytes | None = None
    nazov_xml: str = ""
    cislo_faktury: str = ""
    dodavatel: str = ""
    suma: str = ""
    upozornenia: list[str] = field(default_factory=list)
    chyby: list[str] = field(default_factory=list)
    cesta_vystupu: Path | None = None

    @property
    def stav(self) -> str:
        if not self.ok:
            return "chyba"
        if self.chyby:
            return "neplatne"
        if self.upozornenia:
            return "s upozornenim"
        return "ok"

    def slovnik(self) -> dict:
        return {
            "subor": self.subor.name,
            "stav": self.stav,
            "cislo_faktury": self.cislo_faktury,
            "dodavatel": self.dodavatel,
            "suma": self.suma,
            "vystup": self.cesta_vystupu.name if self.cesta_vystupu else None,
            "chyby": self.chyby,
            "upozornenia": self.upozornenia,
        }


def bezpecny_nazov(text: str, inak: str) -> str:
    ocistene = "".join(z if z.isalnum() or z in "-_." else "_" for z in (text or "").strip())
    ocistene = ocistene.strip("._") or inak
    return ocistene[:80]


def spracuj_subor(cesta: Path, cfg: Config) -> Vysledok:
    """Spracuje jedno PDF. Nikdy nevyhodi vynimku, chyby vrati vo vysledku."""
    vysledok = Vysledok(subor=cesta, ok=False)
    try:
        faktura = precitaj(cesta)
        prevod = do_ubl(faktura, cfg)

        vysledok.xml = prevod.xml
        vysledok.upozornenia.extend(prevod.upozornenia)
        vysledok.cislo_faktury = (faktura.cislo_faktury or "").strip()
        vysledok.dodavatel = (faktura.dodavatel.nazov or "").strip()
        vysledok.suma = (faktura.na_uhradu or faktura.suma_s_dph or "").strip()
        vysledok.nazov_xml = bezpecny_nazov(vysledok.cislo_faktury, cesta.stem) + ".xml"

        vysledok.chyby.extend(skontroluj(prevod.xml))
        if xsd_dostupne():
            vysledok.chyby.extend(skontroluj_xsd(prevod.xml))
        else:
            # Bez schem prebehnu len obchodne pravidla. Mlcat by znamenalo
            # tvarit sa, ze XML je overene viac, nez v skutocnosti je.
            vysledok.upozornenia.append(
                "UBL schemy nie su stiahnute, struktura XML sa neoverila. "
                "Spusti stiahni_schemy.py."
            )
        vysledok.ok = True

    except ChybaSablony as chyba:
        vysledok.chyby.append(
            f"PDF nezodpoveda ocakavanemu vzoru faktury: {chyba}"
        )
    except ChybaPrevodu as chyba:
        vysledok.chyby.append(str(chyba))
    except Exception as chyba:  # noqa: BLE001 - davkove spracovanie nesmie spadnut na jednom subore
        vysledok.chyby.append(f"{type(chyba).__name__}: {chyba}")
        vysledok.upozornenia.append(traceback.format_exc(limit=3))

    return vysledok


def spracuj_vsetky(subory: list[Path], cfg: Config, vlakna: int = 4) -> list[Vysledok]:
    with ThreadPoolExecutor(max_workers=max(1, vlakna)) as bazen:
        return list(bazen.map(lambda s: spracuj_subor(s, cfg), subory))


def zapis(vysledok: Vysledok, priecinok_vystup: Path, priecinok_chyby: Path, striktne: bool) -> Vysledok:
    """Zapise XML do vystupu, alebo protokol o chybe do priecinka chyb."""
    priecinok_vystup.mkdir(parents=True, exist_ok=True)
    priecinok_chyby.mkdir(parents=True, exist_ok=True)

    zapisat = vysledok.xml is not None and (not vysledok.chyby or not striktne)

    if zapisat:
        assert vysledok.xml is not None  # zarucene podmienkou vyssie
        cielovy = priecinok_vystup / vysledok.nazov_xml
        poradie = 2
        while cielovy.exists():
            cielovy = priecinok_vystup / f"{Path(vysledok.nazov_xml).stem}_{poradie}.xml"
            poradie += 1
        cielovy.write_bytes(vysledok.xml)
        vysledok.cesta_vystupu = cielovy

    if vysledok.chyby or not vysledok.ok:
        protokol = priecinok_chyby / f"{vysledok.subor.stem}.txt"
        riadky = [
            f"Subor:  {vysledok.subor}",
            f"Cas:    {datetime.now().isoformat(timespec='seconds')}",
            f"Stav:   {vysledok.stav}",
            f"Vystup: {vysledok.cesta_vystupu or 'nezapisane'}",
            "",
            "CHYBY:",
            *(f"  - {c}" for c in vysledok.chyby or ["ziadne"]),
            "",
            "UPOZORNENIA:",
            *(f"  - {u}" for u in vysledok.upozornenia or ["ziadne"]),
        ]
        protokol.write_text("\n".join(riadky), encoding="utf-8")

    return vysledok


def zapis_suhrn(vysledky: list[Vysledok], cesta: Path) -> None:
    data = [v.slovnik() for v in vysledky]
    cesta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
