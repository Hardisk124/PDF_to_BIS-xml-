#!/usr/bin/env python3
"""Vstupny bod aplikacie pre desktop.

Spusti lokalny server na volnom porte a otvori ho v okne aplikacie.
Ked sa okno nepodari otvorit, pouzije sa predvoleny prehliadac.
Server pocuva len na 127.0.0.1, zvonku nie je pristupny.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn

from faktury import cesty
from faktury.server import vytvor_aplikaciu

NAZOV_OKNA = "Faktury do Peppol BIS 3"


def zapni_log() -> Path | None:
    """Presmeruje vypisy do suboru.

    Zabalena aplikacia bezi bez konzoly, takze sys.stdout a sys.stderr su None.
    Kazde print() by v takom procese skoncilo vynimkou, preto sa este pred
    cimkolvek inym nahradia zapisom do suboru v profile pouzivatela.
    """
    if not cesty.zabalene():
        return None
    cesta = cesty.data() / "beh.log"
    try:
        subor = open(cesta, "a", encoding="utf-8", buffering=1)
    except OSError:
        subor = open(os.devnull, "w", encoding="utf-8")
        cesta = None
    sys.stdout = subor
    sys.stderr = subor
    print(f"\n--- spustenie {datetime.now().isoformat(timespec='seconds')} ---")
    return cesta


def volny_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cakaj_na_server(port: int, sekundy: float = 20.0) -> bool:
    koniec = time.monotonic() + sekundy
    while time.monotonic() < koniec:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def spusti_server(port: int) -> uvicorn.Server:
    konfiguracia = uvicorn.Config(
        vytvor_aplikaciu(), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(konfiguracia)
    vlakno = threading.Thread(target=server.run, daemon=True, name="server")
    vlakno.start()
    return server


def otvor_okno(url: str) -> bool:
    """Skusi otvorit okno aplikacie. False znamena, ze sa nepodarilo."""
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.create_window(NAZOV_OKNA, url, width=1100, height=820, min_size=(720, 560))
        webview.start()
        return True
    except Exception as chyba:
        print(f"Okno aplikacie sa nepodarilo otvorit ({chyba}), otvaram prehliadac.")
        return False


def samotest(cesta_protokolu: Path) -> int:
    """Overi, ze zabalena aplikacia vie vsetko, co potrebuje.

    Okenna aplikacia nema konzolu, takze vysledok sa zapisuje do suboru.
    Spustenie:  FakturyBIS3.exe --samotest [cesta_protokolu]
    """
    import urllib.request

    riadky: list[str] = []
    zlyhania = 0

    def skus(nazov: str, funkcia):
        nonlocal zlyhania
        try:
            riadky.append(f"OK    {nazov}: {funkcia()}")
        except Exception as chyba:
            zlyhania += 1
            riadky.append(f"CHYBA {nazov}: {type(chyba).__name__}: {chyba}")
            riadky.append(traceback.format_exc())

    skus("zabalene", lambda: cesty.zabalene())
    skus("priecinok dat", lambda: cesty.data())
    skus("cesta ku konfiguracii", lambda: cesty.config())
    skus("priecinok vystupov", lambda: cesty.vystup())

    def schemy():
        from faktury.validate import XSD_INVOICE, xsd_dostupne
        if not xsd_dostupne():
            raise FileNotFoundError(f"UBL schema nenajdena na {XSD_INVOICE}")
        return XSD_INVOICE

    skus("UBL schemy v balicku", schemy)

    def citanie_pdf():
        from faktury.sablona import precitaj
        return f"modul pdfplumber nacitany ({precitaj.__name__})"

    skus("citanie PDF", citanie_pdf)

    def prevod_a_validacia():
        """Postavi malu fakturu, prevedie ju a overi validaciu vratane XSD."""
        from faktury.config import nacitaj
        from faktury.model import Faktura, RekapitulaciaDPH, Riadok, Strana
        from faktury.pohoda import do_pohody
        from faktury.ubl import do_ubl
        from faktury.validate import XSD_POHODA, skontroluj, skontroluj_pohodu, skontroluj_xsd

        prazdna = dict(nazov=None, ico=None, dic=None, ic_dph=None, ulica=None,
                       mesto=None, psc=None, krajina=None, email=None, telefon=None, web=None, kontakt=None)
        skuska = Faktura(
            cislo_faktury="TEST-1", datum_vystavenia="2026-01-15", datum_splatnosti="2026-01-29",
            datum_dodania=None, typ_dokladu="380", mena="EUR",
            dodavatel=Strana(**{**prazdna, "nazov": "Dodavatel s.r.o.", "ico": "11111111",
                               "ic_dph": "SK2020111111", "ulica": "Ulica 1", "mesto": "Zilina",
                               "psc": "01001", "krajina": "SK"}),
            odberatel=Strana(**{**prazdna, "nazov": "Odberatel s.r.o.", "ico": "22222222",
                                "ic_dph": "SK2020222222", "ulica": "Hlavna 1", "mesto": "Bratislava",
                                "psc": "81101", "krajina": "SK"}),
            variabilny_symbol="1", konstantny_symbol=None, cislo_objednavky="OBJ-1", datum_objednavky=None,
            referencia_odberatela=None, iban="SK3112000000198742637541", bic=None, cislo_uctu=None, banka=None,
            sposob_uhrady="prevodom", poznamka=None, uvodny_text=None,
            riadky=[Riadok(poradie=1, nazov="Polozka", popis=None, mnozstvo="1", jednotka="ks",
                           jednotkova_cena="100.00", suma_bez_dph="100.00", sadzba_dph="23",
                           kategoria_dph="S")],
            rekapitulacia_dph=[RekapitulaciaDPH(sadzba_dph="23", zaklad="100.00", dph="23.00",
                                                kategoria_dph="S", dovod_oslobodenia=None)],
            suma_bez_dph="100.00", suma_dph="23.00", suma_s_dph="123.00",
            zaplatena_zaloha=None, zaokruhlenie=None, na_uhradu="123.00",
        )
        vysledok = do_ubl(skuska, nacitaj())
        chyby = [c for c in skontroluj(vysledok.xml) + skontroluj_xsd(vysledok.xml)
                 if "EndpointID" not in c and "referencia" not in c]
        if not XSD_POHODA.exists():
            raise FileNotFoundError(f"POHODA schema nenajdena na {XSD_POHODA}")
        pohoda = do_pohody(skuska, nacitaj())
        chyby += [f"POHODA: {c}" for c in skontroluj_pohodu(pohoda.xml)]
        if chyby:
            raise ValueError("; ".join(chyby))
        return f"UBL {len(vysledok.xml)} B, POHODA {len(pohoda.xml)} B, validacia presla"

    skus("prevod a validacia", prevod_a_validacia)

    port = volny_port()
    skus("spustenie servera", lambda: (spusti_server(port), cakaj_na_server(port))[1])

    def stranka_odpoveda():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as odpoved:
            telo = odpoved.read().decode()
        if "Peppol" not in telo:
            raise ValueError("stranka nema ocakavany obsah")
        return f"{odpoved.status}, {len(telo)} B"

    skus("stranka odpoveda", stranka_odpoveda)

    def nastavenia_odpovedaju():
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nastavenia", timeout=10) as odpoved:
            udaje = json.loads(odpoved.read())
        return f"chybajuce: {udaje['chybajuce'] or 'nic'}"

    skus("API nastaveni", nastavenia_odpovedaju)

    def okno_dostupne():
        import webview
        return f"pywebview {getattr(webview, '__version__', 'neznama verzia')}"

    skus("okno aplikacie", okno_dostupne)

    riadky.append("")
    riadky.append(f"ZLYHANI: {zlyhania}")
    cesta_protokolu.parent.mkdir(parents=True, exist_ok=True)
    cesta_protokolu.write_text("\n".join(riadky), encoding="utf-8")
    return 1 if zlyhania else 0


def main() -> int:
    zapni_log()

    if "--samotest" in sys.argv:
        index = sys.argv.index("--samotest")
        zvysok = sys.argv[index + 1:]
        cesta = Path(zvysok[0]) if zvysok else cesty.data() / "samotest.txt"
        return samotest(cesta)

    port = volny_port()
    url = f"http://127.0.0.1:{port}"

    spusti_server(port)
    if not cakaj_na_server(port):
        print("Server sa nepodarilo spustit.", file=sys.stderr)
        return 1

    print(f"{NAZOV_OKNA}")
    print(f"Nastavenia: {cesty.config()}")
    print(f"Vystupy:    {cesty.vystup()}")
    print(f"Adresa:     {url}")

    if otvor_okno(url):
        return 0

    webbrowser.open(url)
    print("\nOkno zatvor ukoncenim tohto programu (Ctrl+C).")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def _zaznam_padu(cesta: Path, popis: str) -> None:
    try:
        with cesta.open("a", encoding="utf-8") as f:
            f.write(f"\n--- pad {datetime.now().isoformat(timespec='seconds')} ---\n{popis}\n")
    except OSError:
        pass


if __name__ == "__main__":
    # Nutne, aby sa zabalene exe pri vytvoreni podprocesu nespustilo znova od zaciatku.
    multiprocessing.freeze_support()

    # Bez konzoly by sa neodchytena vynimka stratila a okno by len zmizlo.
    log = cesty.data() / "chyba.log"
    threading.excepthook = lambda args: _zaznam_padu(
        log, "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    )
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _zaznam_padu(log, traceback.format_exc())
        raise
