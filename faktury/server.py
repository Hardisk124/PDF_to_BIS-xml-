"""Lokalny HTTP server a jeho stranka. Pouziva ho web.py aj zabalena aplikacia."""

from __future__ import annotations

import io
import secrets
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, Header, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import cesty
from .config import KATEGORIE_NULOVEJ_DPH, Config, nacitaj, ploche, uloz, zo_slovnika
from .pipeline import spracuj_vsetky, zapis

MAX_SUBOROV = 100              # kolko faktur sa da nahrat naraz
MAX_BAJTOV_SUBOR = 25 * 1024 * 1024
MAX_BAJTOV_SPOLU = 200 * 1024 * 1024
DNI_DO_UPRATANIA = 7           # po kolkych dnoch sa zmazu stare relacie


def relacie() -> Path:
    priecinok = cesty.vystup() / "relacie"
    priecinok.mkdir(parents=True, exist_ok=True)
    return priecinok


def uprac_stare_relacie(dni: int = DNI_DO_UPRATANIA) -> int:
    """Zmaze vystupy starsie ako zadany pocet dni, aby priecinok nerastol donekonecna."""
    hranica = time.time() - dni * 86400
    zmazane = 0
    for priecinok in relacie().iterdir():
        try:
            if priecinok.is_dir() and priecinok.stat().st_mtime < hranica:
                shutil.rmtree(priecinok, ignore_errors=True)
                zmazane += 1
        except OSError:
            continue
    return zmazane


def _koren_relacie(relacia: str) -> Path | None:
    koren_relacii = relacie().resolve()
    koren = (koren_relacii / relacia).resolve()
    return koren if koren.is_dir() and koren_relacii in koren.parents else None


def _bezpecna_cesta(relacia: str, nazov: str) -> Path | None:
    """Zabrani uniku mimo priecinka relacie cez ../ v nazve."""
    koren = _koren_relacie(relacia)
    if koren is None:
        return None
    cesta = (koren / nazov).resolve()
    return cesta if koren in cesta.parents and cesta.is_file() else None


def vytvor_aplikaciu() -> FastAPI:
    aplikacia = FastAPI(title="PDF faktury do Peppol BIS 3")

    # Server pocuva na 127.0.0.1, co ho chrani pred sietou, ale nie pred strankou
    # otvorenou v prehliadaci toho isteho pouzivatela. Tajomstvo je len v nasej
    # stranke, cudzia stranka ho kvoli pravidlu rovnakeho povodu neprecita.
    tajomstvo = secrets.token_urlsafe(32)
    ponuka = "\n".join(
        f'            <option value="{kod}">{kod} - {popis}</option>'
        for kod, popis in KATEGORIE_NULOVEJ_DPH.items()
    )
    stranka_html = STRANKA.replace("__TAJOMSTVO__", tajomstvo).replace("__KATEGORIE__", ponuka)

    uprac_stare_relacie()

    def overene(hlavicka: str | None) -> bool:
        return bool(hlavicka) and secrets.compare_digest(hlavicka, tajomstvo)

    @aplikacia.get("/", response_class=HTMLResponse)
    def stranka() -> str:
        return stranka_html

    @aplikacia.get("/api/nastavenia")
    def citaj_nastavenia() -> JSONResponse:
        cfg = nacitaj()
        return JSONResponse(
            {
                "nastavenia": ploche(cfg),
                "chybajuce": cfg.chybajuce(),
                "cesta_config": str(cesty.config()),
                "cesta_vystup": str(cesty.vystup()),
            }
        )

    @aplikacia.post("/api/nastavenia")
    async def zapis_nastavenia(udaje: dict, x_tajomstvo: str | None = Header(default=None)) -> JSONResponse:
        if not overene(x_tajomstvo):
            return JSONResponse({"chyba": "Neplatna poziadavka."}, status_code=403)
        # Formular neposiela vsetky polia, chybajuce sa preberu z ulozenych nastaveni.
        novy = zo_slovnika({**ploche(nacitaj()), **udaje})
        uloz(novy)
        return JSONResponse({"ulozene": True, "chybajuce": novy.chybajuce()})

    @aplikacia.post("/prevod")
    async def prevod(
        subory: list[UploadFile], x_tajomstvo: str | None = Header(default=None)
    ) -> JSONResponse:
        if not overene(x_tajomstvo):
            return JSONResponse({"chyba": "Neplatna poziadavka."}, status_code=403)

        cfg: Config = nacitaj()
        pdfka = [s for s in subory if s.filename and s.filename.lower().endswith(".pdf")]
        if not pdfka:
            return JSONResponse({"chyba": "Ziadny PDF subor v poziadavke."}, status_code=400)
        if len(pdfka) > MAX_SUBOROV:
            return JSONResponse(
                {"chyba": f"Naraz sa da spracovat najviac {MAX_SUBOROV} faktur."}, status_code=413
            )

        relacia = uuid.uuid4().hex[:12]
        priecinok = relacie() / relacia
        priecinok.mkdir(parents=True, exist_ok=True)
        docasny = Path(tempfile.mkdtemp(prefix="faktury_"))

        try:
            cesty_pdf = []
            spolu = 0
            for nahrany in pdfka:
                # Citame po castiach, aby velky subor neskoncil cely v pamati.
                cesta = docasny / Path(nahrany.filename or "faktura.pdf").name
                velkost = 0
                with cesta.open("wb") as vystupny:
                    while kus := await nahrany.read(1024 * 1024):
                        velkost += len(kus)
                        spolu += len(kus)
                        if velkost > MAX_BAJTOV_SUBOR or spolu > MAX_BAJTOV_SPOLU:
                            return JSONResponse(
                                {"chyba": f"Subor {cesta.name} alebo cela davka je prilis velka."},
                                status_code=413,
                            )
                        vystupny.write(kus)
                cesty_pdf.append(cesta)

            odpoved = []
            for vysledok in spracuj_vsetky(cesty_pdf, cfg):
                zapis(vysledok, priecinok, priecinok / "chyby", cfg.nastavenia.striktna_validacia)
                odpoved.append(
                    {
                        **vysledok.slovnik(),
                        "upozornenia": [u.splitlines()[0] for u in vysledok.upozornenia],
                    }
                )
        finally:
            shutil.rmtree(docasny, ignore_errors=True)

        pocty = {
            "ok": sum(1 for v in odpoved if v["stav"] == "ok"),
            "upozornenie": sum(1 for v in odpoved if v["stav"] in {"s upozornenim", "neplatne"}),
            "chyba": sum(1 for v in odpoved if v["stav"] == "chyba"),
        }
        return JSONResponse(
            {"relacia": relacia, "vysledky": odpoved, "pocty": pocty, "priecinok": str(priecinok)}
        )

    @aplikacia.get("/subor/{relacia}/{nazov}")
    def stiahni(relacia: str, nazov: str) -> Response:
        cesta = _bezpecna_cesta(relacia, nazov)
        if cesta is None:
            return Response("Subor nenajdeny.", status_code=404)
        return Response(
            cesta.read_bytes(),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{cesta.name}"'},
        )

    @aplikacia.get("/zip/{relacia}")
    def stiahni_zip(relacia: str) -> Response:
        koren = _koren_relacie(relacia)
        if koren is None:
            return Response("Relacia nenajdena.", status_code=404)
        vyrovnavacia = io.BytesIO()
        with zipfile.ZipFile(vyrovnavacia, "w", zipfile.ZIP_DEFLATED) as archiv:
            for cesta in sorted(koren.glob("*.xml")):
                archiv.write(cesta, cesta.name)
        return Response(
            vyrovnavacia.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="faktury_{relacia}.zip"'},
        )

    return aplikacia


STRANKA = r"""<!doctype html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Faktury do Peppol BIS 3</title>
<style>
:root{--ram:#dcdcd6;--tlm:#6b6b66;--pod:#faf9f6;--karta:#fff;--text:#1a1a18;
      --ok:#1f7a4d;--upoz:#9a6a00;--chyba:#b02a24;--zvyr:#2b5fd9;}
@media (prefers-color-scheme:dark){:root{--ram:#3a3a36;--tlm:#9b9b94;--pod:#15151a;--karta:#1e1e24;--text:#eceae4;--zvyr:#7aa2ff;}}
*{box-sizing:border-box}
body{margin:0;background:var(--pod);color:var(--text);
     font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.obal{max-width:980px;margin:0 auto;padding:28px 20px 64px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:21px;margin:0;letter-spacing:-.01em}
nav{display:flex;gap:4px}
nav button{background:none;border:1px solid transparent;color:var(--tlm);padding:6px 14px;border-radius:7px;cursor:pointer;font:inherit}
nav button.vybrane{background:var(--karta);border-color:var(--ram);color:var(--text)}
.vyzva{background:color-mix(in srgb,var(--upoz) 12%,var(--karta));border:1px solid color-mix(in srgb,var(--upoz) 35%,var(--ram));
       border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:14px}
.vyzva a{color:var(--zvyr);cursor:pointer;text-decoration:underline}
#zona{border:2px dashed var(--ram);border-radius:12px;padding:44px 24px;text-align:center;background:var(--karta);cursor:pointer;transition:.15s}
#zona.aktivna{border-color:var(--ok);background:color-mix(in srgb,var(--ok) 7%,var(--karta))}
#zona strong{display:block;font-size:17px;margin-bottom:5px}
#zona span{color:var(--tlm);font-size:14px}
input[type=file]{display:none}
.akcie{display:flex;gap:10px;align-items:center;margin:18px 0;flex-wrap:wrap}
button.tl{font:inherit;padding:9px 18px;border-radius:8px;border:1px solid var(--ram);background:var(--karta);color:var(--text);cursor:pointer}
button.tl.hlavne{background:var(--text);color:var(--pod);border-color:var(--text)}
button.tl:disabled{opacity:.45;cursor:default}
#stav{color:var(--tlm);font-size:14px}
table{width:100%;border-collapse:collapse;margin-top:6px;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--ram);vertical-align:top}
th{font-weight:600;color:var(--tlm);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.suma{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.znacka{font-weight:600;white-space:nowrap}
.znacka.ok{color:var(--ok)}.znacka.upoz{color:var(--upoz)}.znacka.chyba{color:var(--chyba)}
.detail{color:var(--tlm);font-size:13px;margin-top:4px}
.detail b{color:var(--chyba);font-weight:600}
.tabulka-obal{overflow-x:auto}
form.nastavenia{background:var(--karta);border:1px solid var(--ram);border-radius:12px;padding:22px}
fieldset{border:0;padding:0;margin:0 0 26px}
legend{font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--tlm);padding:0;margin-bottom:12px}
.mriezka{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
label{display:block;font-size:13px;color:var(--tlm);margin-bottom:5px}
input[type=text],input[type=password],select{width:100%;font:inherit;padding:8px 10px;border:1px solid var(--ram);
  border-radius:7px;background:var(--pod);color:var(--text)}
.napoveda{font-size:13px;color:var(--tlm);margin:8px 0 0}
.napoveda a{color:var(--zvyr)}
.cesty{font-size:12px;color:var(--tlm);margin-top:22px;word-break:break-all}
</style>
</head>
<body>
<div class="obal">
<header>
  <h1>Faktury do Peppol BIS Billing 3.0</h1>
  <nav>
    <button id="zal-prevod" class="vybrane">Prevod</button>
    <button id="zal-nastavenia">Nastavenia</button>
  </nav>
</header>

<div id="vyzva" class="vyzva" hidden></div>

<section id="panel-prevod">
  <div id="zona">
    <strong>Presun sem PDF faktury</strong>
    <span>alebo klikni a vyber ich</span>
    <input type="file" id="vyber" multiple accept="application/pdf">
  </div>
  <div class="akcie">
    <button class="tl hlavne" id="spusti" disabled>Previest</button>
    <button class="tl" id="zip" disabled>Stiahnut vsetko ako ZIP</button>
    <span id="stav"></span>
  </div>
  <div class="tabulka-obal"><table id="tabulka" hidden>
    <thead><tr><th>Stav</th><th>Subor</th><th>Cislo</th><th>Dodavatel</th><th class="suma">Suma</th><th>XML</th></tr></thead>
    <tbody></tbody>
  </table></div>
</section>

<section id="panel-nastavenia" hidden>
  <form class="nastavenia" id="formular">
    <fieldset>
      <legend>Peppol identifikatory</legend>
      <div class="mriezka">
        <div><label for="moj_endpoint_id">Moj EndpointID</label>
             <input type="text" id="moj_endpoint_id" placeholder="prazdne = IC DPH z faktury"></div>
        <div><label for="moj_endpoint_scheme">Moj EAS kod</label>
             <input type="text" id="moj_endpoint_scheme" placeholder="napr. 0158"></div>
        <div><label for="odberatel_endpoint_scheme">EAS kod odberatela</label>
             <input type="text" id="odberatel_endpoint_scheme" placeholder="napr. 0158"></div>
      </div>
      <p class="napoveda">Nazov firmy, ICO, IC DPH, adresa aj IBAN sa citaju priamo z faktury.
        Tu zostavaju len EAS kody, ktore na doklade nie su. Zoznam je na
        <a href="https://docs.peppol.eu/poacc/billing/3.0/codelist/eas/" target="_blank" rel="noreferrer">docs.peppol.eu</a>,
        over si ich u svojho Access Pointu.</p>
    </fieldset>

    <fieldset>
      <legend>Spracovanie</legend>
      <div class="mriezka">
        <div><label for="kategoria_nulovej_dph">Kategoria pri nulovej DPH</label>
          <select id="kategoria_nulovej_dph">
__KATEGORIE__
          </select></div>
        <div><label for="zaloha_referencie">Ked chyba referencia odberatela</label>
          <select id="zaloha_referencie">
            <option value="cislo_faktury">Doplnit cislo faktury</option>
            <option value="ziadna">Nechat prazdne</option>
          </select></div>
        <div><label for="mena">Predvolena mena</label><input type="text" id="mena" maxlength="3"></div>
      </div>
      <p class="napoveda">Peppol vyzaduje referenciu odberatela alebo cislo objednavky.
        Ked na fakture ani jedno nie je, doplnenim cisla faktury zostane subor platny.</p>
    </fieldset>

    <div class="akcie">
      <button type="submit" class="tl hlavne">Ulozit</button>
      <span id="stav-nastavenia"></span>
    </div>
    <div class="cesty" id="cesty"></div>
  </form>
</section>
</div>

<script>
const TAJOMSTVO = "__TAJOMSTVO__";
const $ = id => document.getElementById(id);
const POLIA = ["moj_endpoint_id","moj_endpoint_scheme","odberatel_endpoint_scheme",
               "kategoria_nulovej_dph","zaloha_referencie","mena"];
let subory = [], relacia = null;

function panel(ktory){
  $("panel-prevod").hidden = ktory !== "prevod";
  $("panel-nastavenia").hidden = ktory !== "nastavenia";
  $("zal-prevod").classList.toggle("vybrane", ktory === "prevod");
  $("zal-nastavenia").classList.toggle("vybrane", ktory === "nastavenia");
}
$("zal-prevod").onclick = () => panel("prevod");
$("zal-nastavenia").onclick = () => panel("nastavenia");

async function nacitajNastavenia(){
  const d = await (await fetch("/api/nastavenia")).json();
  for (const p of POLIA) if ($(p)) $(p).value = d.nastavenia[p] ?? "";
  $("cesty").textContent = `Nastavenia: ${d.cesta_config}   |   Vystupy: ${d.cesta_vystup}`;
  ukazVyzvu(d.chybajuce);
}

function ukazVyzvu(chybajuce){
  const v = $("vyzva");
  if (!chybajuce || !chybajuce.length){ v.hidden = true; return; }
  v.hidden = false;
  v.innerHTML = `Pred prvym pouzitim doplň: <b>${chybajuce.join(", ")}</b>. `
              + `<a id="odkaz-nastavenia">Otvorit nastavenia</a>`;
  $("odkaz-nastavenia").onclick = () => panel("nastavenia");
}

$("formular").onsubmit = async e => {
  e.preventDefault();
  const udaje = {};
  for (const p of POLIA) if ($(p)) udaje[p] = $(p).value;
  const d = await (await fetch("/api/nastavenia", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Tajomstvo": TAJOMSTVO},
    body: JSON.stringify(udaje)
  })).json();
  $("stav-nastavenia").textContent = "Ulozene.";
  setTimeout(() => $("stav-nastavenia").textContent = "", 2500);
  ukazVyzvu(d.chybajuce);
};

const zona = $("zona");
zona.onclick = () => $("vyber").click();
$("vyber").onchange = () => prijmi([...$("vyber").files]);
["dragenter","dragover"].forEach(u => zona.addEventListener(u, e => {e.preventDefault(); zona.classList.add("aktivna");}));
["dragleave","drop"].forEach(u => zona.addEventListener(u, e => {e.preventDefault(); zona.classList.remove("aktivna");}));
zona.addEventListener("drop", e => prijmi([...e.dataTransfer.files]
  .filter(f => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"))));

function prijmi(zoznam){
  subory = zoznam;
  $("spusti").disabled = !subory.length;
  $("stav").textContent = subory.length ? `${subory.length} suborov pripravenych` : "";
}

$("spusti").onclick = async () => {
  $("spusti").disabled = true; $("zip").disabled = true;
  $("stav").textContent = "Spracuvam...";
  const data = new FormData();
  subory.forEach(f => data.append("subory", f));
  let d;
  try { d = await (await fetch("/prevod", {method:"POST", headers:{"X-Tajomstvo":TAJOMSTVO}, body:data})).json(); }
  catch(e){ $("stav").textContent = "Server neodpovedal: " + e.message; $("spusti").disabled = false; return; }
  if (d.chyba){ $("stav").textContent = d.chyba; $("spusti").disabled = false; return; }
  relacia = d.relacia;
  vykresli(d.vysledky);
  $("stav").textContent = `Hotovo: ${d.pocty.ok} ok, ${d.pocty.upozornenie} s upozornenim, ${d.pocty.chyba} chybnych`;
  $("spusti").disabled = false;
  $("zip").disabled = !d.vysledky.some(v => v.vystup);
};

$("zip").onclick = () => { if (relacia) location.href = `/zip/${relacia}`; };

const trieda = {ok:"ok", "s upozornenim":"upoz", neplatne:"chyba", chyba:"chyba"};
function vykresli(vysledky){
  const telo = document.querySelector("#tabulka tbody");
  telo.innerHTML = "";
  for (const v of vysledky){
    const r = telo.insertRow();
    r.insertCell().innerHTML = `<span class="znacka ${trieda[v.stav]||""}">${v.stav}</span>`;
    const c = r.insertCell();
    c.textContent = v.subor;
    const poznamky = [...v.chyby.map(t => `<b>${esc(t)}</b>`), ...v.upozornenia.map(esc)];
    if (poznamky.length) c.insertAdjacentHTML("beforeend", `<div class="detail">${poznamky.join("<br>")}</div>`);
    r.insertCell().textContent = v.cislo_faktury || "";
    r.insertCell().textContent = v.dodavatel || "";
    const s = r.insertCell(); s.className = "suma"; s.textContent = v.suma || "";
    r.insertCell().innerHTML = v.vystup
      ? `<a href="/subor/${relacia}/${encodeURIComponent(v.vystup)}">${esc(v.vystup)}</a>` : "";
  }
  $("tabulka").hidden = false;
}
function esc(t){ const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }

nacitajNastavenia();
</script>
</body>
</html>
"""
