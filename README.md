# Faktúry → Peppol BIS Billing 3.0 a POHODA

Prevedie vystavené PDF faktúry na UBL 2.1 XML podľa Peppol BIS Billing 3.0, na XML
importu do účtovného programu POHODA (SK), alebo na oba naraz. Formát sa volí v nastaveniach.
Číta jeden pevný vzor faktúry podľa súradníc stĺpcov. Beží celé lokálne,
nič neodchádza z počítača a prevod nič nestojí.

Tri spôsoby spustenia nad rovnakým jadrom:

- **`FakturyBIS3.exe`** je aplikácia pre Windows s vlastným oknom. Na bežné používanie.
- **`cli.py`** prejde priečinok a zapíše XML do výstupného priečinka. Na väčšie dávky.
- **`web.py`** spustí tú istú stránku v prehliadači. Na vývoj.

## Aplikácia pre Windows

`dist\FakturyBIS3.exe` je jeden súbor, nič sa neinštaluje. Skopíruj ho kamkoľvek a spusti.
Nastavenia a výstupy si ukladá do `%APPDATA%\FakturyBIS3`.

Pri prvom spustení otvor kartu Nastavenia, vyber výstupné formáty a pri Peppol vyplň EAS kódy.
Aplikácia na chýbajúce nastavenia upozorní sama.

### Zostavenie exe

```bat
py -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install -r requirements.txt -r requirements-windows.txt
zostav.bat
```

Výsledok je `dist\FakturyBIS3.exe`, zhruba 44 MB.

Ak chceš inštalátor so zástupcom v ponuke Štart, nainštaluj
[Inno Setup](https://jrsoftware.org/isdl.php) a spusti:

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" instalator.iss
```

### Kontrola po zostavení

```bat
dist\FakturyBIS3.exe --samotest protokol.txt
```

Overí zabalené schémy, cesty, prevod, validáciu, server aj okno a zapíše výsledok do súboru.
Aplikácia nemá konzolu, preto sa protokol píše na disk.

## Inštalácia pre vývoj

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python stiahni_schemy.py     # voliteľné, zapne XSD kontrolu
```

## Nastavenie

Údaje oboch strán, IBAN aj sumy sa čítajú priamo z faktúry. V nastaveniach zostávajú
len veci, ktoré na doklade nie sú.

**EAS kódy sú povinné.** Sú to Peppol identifikátory schémy. Over si ich v
[zozname EAS kódov](https://docs.peppol.eu/poacc/billing/3.0/codelist/eas/)
alebo u svojho Access Pointu. Hodnoty v priloženom `config.toml` sú len zástupné.

**Výstupné formáty.** Peppol UBL ide do `FAKTURA.xml`, POHODA do `FAKTURA_pohoda.xml`.
Aspoň jeden musí zostať zapnutý. EAS kódy potrebuje len Peppol.

**POHODA XML** sa importuje cez *Súbor → Dátová komunikácia → XML import/export*. Pravidlá:

- Členenie DPH a KV DPH je v hlavičke dokladu: faktúra s DPH nad 0 % dostane `UD` a `A1`,
  faktúra s 0 % dostane `UDzahrSl` a `KN`. Na jednej faktúre sú sadzby vždy jednotné. Keby neboli,
  faktúra dostane upozornenie, lebo to znamená, že sa niečo prečítalo zle.
- Variabilný symbol sú číslice z čísla faktúry (`V26/0687` → `260687`).
- DPH sa počíta po položkách ako v POHODE, môže sa preto od PDF líšiť o zaokrúhlenie v centoch.
- Kontakt dodávateľa (telefón, e-mail, web) sa z PDF nečíta,
  je natvrdo v `KONTAKT_DODAVATELA` vo `faktury/sablona.py`.
- Predkontácia je pre všetky faktúry `602001_Prepr` (konštanta `PREDKONTACIA` vo `faktury/pohoda.py`).
- Text položky má v POHODE najviac 90 znakov, zvyšok popisu ide do poznámky položky.

**Kategória pri nulovej DPH** rozhoduje, ako sa označí oslobodené plnenie. Predvolené `AE`
je prenesenie daňovej povinnosti, čo zodpovedá vete o článkoch 193 až 196 na faktúre.
Ak ti účtovník povie inak, prepni na `K` pri dodaní do EÚ alebo `G` pri vývoze.

**Záloha referencie** rieši to, že Peppol vyžaduje referenciu odberateľa alebo číslo
objednávky, a na tvojich faktúrach ani jedno nie je vyplnené. Predvolene sa doplní číslo
faktúry, inak by súbor neprešiel validáciou.

**Striktná validácia je zapnutá.** Faktúra, ktorá neprejde kontrolou, sa do výstupu nezapíše
a dostane protokol v `chyby/`. Je to zámerné, lebo tieto XML idú zákazníkovi. Vypnúť sa to dá
v `config.toml`, ale potom si výstup musíš kontrolovať sám.

## Spustenie

```bash
./.venv/bin/python app.py                          # aplikácia s vlastným oknom
./.venv/bin/python cli.py                          # vstup/ -> D:\exp_xml
./.venv/bin/python cli.py --vstup D:/faktury/2026 --vlakna 8
./.venv/bin/python cli.py --striktne               # nezapíše XML, ktoré neprejde validáciou
```

XML idú natvrdo do `D:\exp_xml\bis` (Peppol BIS) a `D:\exp_xml\pohoda` (POHODA),
priečinky sa vytvoria samé. Existujúci súbor sa neprepíše, nový dostane `_2`, `_3`. Faktúra s chybou alebo upozornením dostane protokol v `chyby/`,
plus je tam `suhrn.json` s prehľadom celej dávky.

## Ako to funguje

```
PDF → slová so súradnicami (pdfplumber) → čítanie podľa stĺpcov → UBL a/alebo POHODA XML → validácia → zápis
```

Faktúra sa nečíta z plochého textu, ale podľa polohy slov na strane. Ploché čítanie by
zlyhalo už na rekapitulácii DPH, ktorá je na tej istej riadkovej úrovni ako zoznam
vlastných objednávok vľavo.

| Súbor | Čo robí |
|---|---|
| `faktury/sablona.py` | Čítanie faktúry podľa súradníc. Tu sa upravuje, keď sa zmení vzor |
| `faktury/model.py` | Štruktúra faktúry |
| `faktury/ubl.py` | Normalizácia súm a dátumov, stavba Peppol UBL XML |
| `faktury/pohoda.py` | Stavba XML importu do POHODY |
| `faktury/validate.py` | Obchodné pravidlá a XSD |
| `faktury/pipeline.py` | Spojenie krokov, ošetrenie chýb |
| `faktury/server.py` | HTTP server a stránka, spoločná pre okno aj prehliadač |
| `faktury/cesty.py` | Cesty, ktoré sa líšia medzi vývojom a zabaleným exe |

### Keď sa vzor faktúry zmení

Všetky súradnice stĺpcov sú konštanty na začiatku `faktury/sablona.py`. Keď dodávateľský
program zmení rozloženie, upravujú sa ony a nič iné. Pomôcka na zistenie nových súradníc:

```bash
./.venv/bin/python -c "
import pdfplumber
with pdfplumber.open('vstup/faktura.pdf') as pdf:
    for s in pdf.pages[0].extract_words():
        print(round(s['top'],1), round(s['x0']), s['text'])
"
```

## Validácia

Dve úrovne. Vstavané obchodné pravidlá EN 16931 a Peppol kontrolujú súčty riadkov proti
základu dane, rekapituláciu DPH, povinné polia a kategórie DPH. XSD kontrola overí
štruktúru proti oficiálnej UBL 2.1 schéme, ak sú schémy stiahnuté. POHODA XML sa overuje
proti XSD schéme faktúry od Stormware (`schemas/pohoda/`, sťahuje ju tiež `stiahni_schemy.py`).

**Toto nenahrádza oficiálny Peppol Schematron.** Ten beží nad XSLT 2.0, čo Python sám
nevie. Kým budeš XML posielať prvýkrát, prežeň dávku cez
[oficiálny validátor](https://ecosio.com/en/peppol-and-xml-document-validator/)
alebo cez validátor svojho Access Pointu.

## Keď niečo nesedí

Program radšej odmietne faktúru, než by vyrobil XML s inou sumou, než je na papieri.
Konkrétne skončí chybou, keď:

- súčet fakturovaných riadkov sa nerovná základu dane uvedenému na faktúre,
- množstvo, cena alebo suma niektorého riadku sa nedá prečítať,
- adresa dodávateľa alebo odberateľa nemá očakávaný tvar,
- krajinu odberateľa sa nepodarí určiť.

Všetko ostatné prejde ako upozornenie a zapíše sa do protokolu v `chyby/`.

Aplikácia nemá konzolu, takže si píše dva denníky do `%APPDATA%\FakturyBIS3`:
`beh.log` s bežnými výpismi a `chyba.log` s neodchytenými pádmi.

## Obmedzenia

- Číta iba tento jeden vzor faktúry. Iné PDF odmietne s hláškou, nevyrobí z neho nezmysel.
- Zľavy a prirážky na úrovni dokladu sa nemapujú na `AllowanceCharge`. Na tomto vzore sa nevyskytujú.
- Viacstranové faktúry sú ošetrené, ale odskúšané na skutočnom doklade nie sú. Prvú takú si
  skontroluj ručne. Poistkou je kontrola súčtu riadkov, ktorá chýbajúcu položku zachytí.
- Spracuje najviac 30 strán a 100 faktúr naraz.

## Testy

```bash
./.venv/bin/python testy/test_prevod.py      # jadro: Faktura → UBL → validácia
./.venv/bin/python testy/test_sablona.py     # čítanie skutočných PDF vrátane očakávaných hodnôt
./.venv/bin/python testy/test_server.py      # server: nahranie, sťahovanie, ZIP, ochrany
./.venv/bin/python testy/test_pohoda.py      # POHODA: členenie DPH, XSD, porovnanie so vzorom z POHODY
```
