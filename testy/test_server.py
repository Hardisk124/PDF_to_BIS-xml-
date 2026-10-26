"""Test lokalneho servera: nahranie, stiahnutie, ZIP, nastavenia a ochrany.

Server sa testuje nad docasnym priecinkom, aby test nesiahal na skutocne
nastavenia ani vystupy pouzivatela.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from faktury import cesty

KOREN = Path(__file__).resolve().parent.parent
FAKTURY = sorted((KOREN / "vstup").glob("*.pdf"))


def priprav_docasne(priecinok: Path) -> None:
    """Presmeruje nastavenia aj vystupy do docasneho priecinka."""
    (priecinok / "vystup").mkdir(parents=True, exist_ok=True)
    cesty.data = lambda: priecinok
    cesty.config = lambda: priecinok / "config.toml"
    cesty.vystup = lambda: priecinok / "vystup"
    # Export ide inak natvrdo na D:\\exp_xml, test tam nesmie zapisovat.
    cesty.KOREN_EXPORTU = priecinok / "exp_xml"


def main() -> int:
    if not FAKTURY:
        print("V priecinku vstup/ nie su ziadne PDF, test sa preskakuje.")
        return 0

    docasny = Path(tempfile.mkdtemp(prefix="test_server_"))
    zlyhalo = 0
    try:
        priprav_docasne(docasny)
        import faktury.config as konfig
        import faktury.server as srv

        konfig.cesty = cesty
        srv.cesty = cesty

        klient = TestClient(srv.vytvor_aplikaciu())

        def over(popis: str, skutocne, ocakavane) -> None:
            nonlocal zlyhalo
            ok = skutocne == ocakavane
            print(f"{'OK   ' if ok else 'ZLE  '} {popis}: {skutocne}")
            if not ok:
                print(f"       ocakavane: {ocakavane}")
                zlyhalo += 1

        stranka = klient.get("/")
        over("GET /", stranka.status_code, 200)
        najdene = re.search(r'const TAJOMSTVO = "([^"]+)"', stranka.text)
        if not najdene:
            print("ZLE   stranka neobsahuje tajomstvo")
            return 1
        hlavicky = {"X-Tajomstvo": najdene.group(1)}

        # Bez tajomstva sa nesmie da nic zmenit ani spracovat.
        over("POST /prevod bez tajomstva",
             klient.post("/prevod", files=[("subory", ("a.pdf", b"x", "application/pdf"))]).status_code, 403)
        over("POST /api/nastavenia bez tajomstva",
             klient.post("/api/nastavenia", json={"mena": "EUR"}).status_code, 403)

        # Formular neposiela striktnu validaciu, ulozenie ju nesmie prepisat.
        konfig.uloz(konfig.Config(nastavenia=konfig.Nastavenia(striktna_validacia=False)))
        klient.post("/api/nastavenia", headers=hlavicky, json={"mena": "EUR"})
        over("striktna validacia zostala vypnuta",
             klient.get("/api/nastavenia").json()["nastavenia"]["striktna_validacia"], False)

        over("bez formatu sa neulozi", klient.post("/api/nastavenia", headers=hlavicky,
             json={"vystup_ubl": False, "vystup_pohoda": False}).status_code, 400)

        # Nastavenia sa ulozia a nacitaju spat.
        ulozenie = klient.post("/api/nastavenia", headers=hlavicky, json={
            "moj_endpoint_scheme": "0158", "odberatel_endpoint_scheme": "0158",
            "mena": "eur", "kategoria_nulovej_dph": "ae",
        })
        over("ulozenie nastaveni", ulozenie.status_code, 200)
        over("po ulozeni nic nechyba", ulozenie.json()["chybajuce"], [])
        over("mena sa normalizovala", klient.get("/api/nastavenia").json()["nastavenia"]["mena"], "EUR")

        # Prevod skutocnych faktur.
        subory = [("subory", (f.name, f.read_bytes(), "application/pdf")) for f in FAKTURY]
        odpoved = klient.post("/prevod", headers=hlavicky, files=subory)
        over("POST /prevod", odpoved.status_code, 200)
        data = odpoved.json()
        over("pocet vysledkov", len(data["vysledky"]), len(FAKTURY))
        over("ziadna chyba", data["pocty"]["chyba"], 0)
        over("kazda ma XML", all(v["vystup"] for v in data["vysledky"]), True)

        relacia = data["relacia"]
        over("UBL aj POHODA pre kazdu", all(len(v["vystup"]) == 2 for v in data["vysledky"]), True)
        over("UBL v exp_xml/bis", len(list((docasny / "exp_xml" / "bis").glob("*.xml"))), len(FAKTURY))
        over("POHODA v exp_xml/pohoda", len(list((docasny / "exp_xml" / "pohoda").glob("*_pohoda.xml"))), len(FAKTURY))
        prve = data["vysledky"][0]["vystup"][0]
        stiahnute = klient.get(f"/subor/{relacia}/{prve}")
        over("stiahnutie XML", stiahnute.status_code, 200)
        over("je to Invoice", b"<Invoice" in stiahnute.content, True)

        zip_odpoved = klient.get(f"/zip/{relacia}")
        over("stiahnutie ZIP", zip_odpoved.status_code, 200)
        over("ZIP zacina PK", zip_odpoved.content[:2], b"PK")

        # Pokusy o unik z priecinka relacie.
        for cesta in (f"/subor/{relacia}/..%2F..%2Fconfig.toml",
                      f"/subor/{relacia}/....//config.toml",
                      "/subor/neexistuje/nieco.xml",
                      "/zip/neexistuje"):
            over(f"odmietnute {cesta}", klient.get(cesta).status_code, 404)

        # Prilis vela suborov naraz.
        vela = [("subory", (f"f{i}.pdf", b"%PDF-1.4", "application/pdf"))
                for i in range(srv.MAX_SUBOROV + 1)]
        over("prilis vela suborov", klient.post("/prevod", headers=hlavicky, files=vela).status_code, 413)

        # Upratovanie starych relacii nesmie zmazat cerstve vystupy.
        srv.uprac_stare_relacie(dni=7)
        over("cerstva relacia zostala", (srv.relacie() / relacia).is_dir(), True)
        over("upratanie starsich ako 0 dni", srv.uprac_stare_relacie(dni=0) >= 1, True)

        print("\nZlyhani:", zlyhalo)
        return 1 if zlyhalo else 0
    finally:
        shutil.rmtree(docasny, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
