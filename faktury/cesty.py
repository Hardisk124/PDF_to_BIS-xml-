"""Cesty k suborom, ktore sa lisia medzi vyvojom a zabalenym exe.

Zabalena aplikacia ma dve rozne miesta:
  - zdroje, ktore su vnutri exe a su len na citanie (UBL schemy),
  - pouzivatelske data, ktore sa daju menit (config.toml, vystupy).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

NAZOV_APLIKACIE = "FakturyBIS3"


def zabalene() -> bool:
    """True, ked aplikacia bezi ako exe zostavene PyInstallerom."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def zdroj(*casti: str) -> Path:
    """Subor, ktory je sucastou programu. V exe je v docasnom priecinku _MEIPASS."""
    if zabalene():
        return Path(sys._MEIPASS).joinpath(*casti)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.joinpath(*casti)


def data() -> Path:
    """Priecinok pre nastavenia a vystupy pouzivatela.

    Vo vyvoji je to koren projektu, aby sa nic nerozsypalo po systeme.
    V zabalenej aplikacii je to profil pouzivatela, lebo do Program Files
    sa zapisovat neda.
    """
    if not zabalene():
        return Path(__file__).resolve().parent.parent

    if os.name == "nt":
        zaklad = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        zaklad = Path.home() / "Library" / "Application Support"
    else:
        zaklad = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")

    priecinok = zaklad / NAZOV_APLIKACIE
    priecinok.mkdir(parents=True, exist_ok=True)
    return priecinok


def config() -> Path:
    return data() / "config.toml"


def vystup() -> Path:
    priecinok = data() / "vystup"
    priecinok.mkdir(parents=True, exist_ok=True)
    return priecinok
