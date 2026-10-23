"""Vzorove faktury na test prevodu bez volania API."""

from faktury.model import Faktura, RekapitulaciaDPH, Riadok, Strana


def strana(**kw) -> Strana:
    zaklad = dict(nazov=None, ico=None, dic=None, ic_dph=None, ulica=None,
                  mesto=None, psc=None, krajina=None, email=None, telefon=None, web=None, kontakt=None)
    return Strana(**{**zaklad, **kw})


def riadok(**kw) -> Riadok:
    zaklad = dict(poradie=1, nazov=None, popis=None, mnozstvo=None, jednotka=None,
                  jednotkova_cena=None, suma_bez_dph=None, sadzba_dph=None, kategoria_dph=None)
    return Riadok(**{**zaklad, **kw})


def faktura(**kw) -> Faktura:
    zaklad = dict(
        cislo_faktury="2026001", datum_vystavenia="2026-03-01", datum_splatnosti="2026-03-15",
        datum_dodania="2026-02-28", typ_dokladu="380", mena="EUR",
        dodavatel=strana(nazov="Dodavatel s.r.o.", ico="11111111", dic="2020111111",
                         ic_dph="SK2020111111", ulica="Priemyselna 5", mesto="Zilina",
                         psc="01001", krajina="SK", email="fakturacia@dodavatel.sk", telefon="+421900111222"),
        odberatel=strana(nazov="Odberatel a.s.", ico="22222222", ic_dph="SK2020222222",
                         ulica="Hlavna 1", mesto="Bratislava", psc="81101", krajina="SK"),
        variabilny_symbol="2026001", konstantny_symbol="0308", cislo_objednavky="OBJ-77", datum_objednavky="2026-02-20",
        referencia_odberatela=None, iban="SK31 1200 0000 1987 4263 7541", bic="GIBASKBX",
        cislo_uctu="1987426375/1200", banka="SLSP",
        sposob_uhrady="prevodom", poznamka=None, uvodny_text=None,
        riadky=[], rekapitulacia_dph=[],
        suma_bez_dph=None, suma_dph=None, suma_s_dph=None,
        zaplatena_zaloha=None, zaokruhlenie=None, na_uhradu=None,
    )
    return Faktura(**{**zaklad, **kw})


def jednoducha() -> Faktura:
    """Bezna faktura s jednou sadzbou, vsetky sumy uvedene."""
    return faktura(
        riadky=[
            riadok(poradie=1, nazov="Konzultacie", mnozstvo="10", jednotka="hod",
                   jednotkova_cena="50.00", suma_bez_dph="500.00", sadzba_dph="23"),
            riadok(poradie=2, nazov="Licencia", mnozstvo="1", jednotka="ks",
                   jednotkova_cena="200.00", suma_bez_dph="200.00", sadzba_dph="23"),
        ],
        rekapitulacia_dph=[RekapitulaciaDPH(sadzba_dph="23", zaklad="700.00", dph="161.00",
                                            kategoria_dph="S", dovod_oslobodenia=None)],
        suma_bez_dph="700.00", suma_dph="161.00", suma_s_dph="861.00", na_uhradu="861.00",
    )


def dve_sadzby_ciarky() -> Faktura:
    """Ciarky ako desatinny oddelovac, dve sadzby, ziadna rekapitulacia, zaokruhlenie."""
    return faktura(
        cislo_faktury="FA 2026/00042",
        datum_vystavenia="15.03.2026",
        datum_splatnosti="29.03.2026",
        riadky=[
            riadok(poradie=1, nazov="Ubytovanie", mnozstvo="3", jednotka="den",
                   jednotkova_cena="80,00", suma_bez_dph="240,00", sadzba_dph="5"),
            riadok(poradie=2, nazov="Strava", mnozstvo="6", jednotka="ks",
                   jednotkova_cena="12,50", suma_bez_dph="75,00", sadzba_dph="23"),
        ],
        rekapitulacia_dph=[],
        suma_bez_dph=None, suma_dph=None, suma_s_dph=None,
        zaokruhlenie="0,02", na_uhradu=None,
    )


def prenesena_povinnost() -> Faktura:
    """Reverse charge, nulova sadzba, kategoria AE."""
    return faktura(
        cislo_faktury="RC-9",
        riadky=[riadok(poradie=1, nazov="Stavebne prace", mnozstvo="1", jednotka="ks",
                       jednotkova_cena="5000.00", suma_bez_dph="5000.00",
                       sadzba_dph="0", kategoria_dph="AE")],
        rekapitulacia_dph=[RekapitulaciaDPH(sadzba_dph="0", zaklad="5000.00", dph="0.00",
                                            kategoria_dph="AE",
                                            dovod_oslobodenia="Prenesenie danovej povinnosti podla par. 69 ods. 12")],
        suma_bez_dph="5000.00", suma_dph="0.00", suma_s_dph="5000.00", na_uhradu="5000.00",
    )


def dobropis() -> Faktura:
    """Dobropis, zaporne sumy, typ 381."""
    return faktura(
        cislo_faktury="DOB-3", typ_dokladu="381", datum_splatnosti=None,
        riadky=[riadok(poradie=1, nazov="Vratka licencie", mnozstvo="1", jednotka="ks",
                       jednotkova_cena="-200.00", suma_bez_dph="-200.00", sadzba_dph="23")],
        rekapitulacia_dph=[RekapitulaciaDPH(sadzba_dph="23", zaklad="-200.00", dph="-46.00",
                                            kategoria_dph="S", dovod_oslobodenia=None)],
        suma_bez_dph="-200.00", suma_dph="-46.00", suma_s_dph="-246.00", na_uhradu="-246.00",
    )


def zaloha_a_nesedi_sucet() -> Faktura:
    """Odpocitana zaloha a zamerne nesediaci sucet riadkov."""
    return faktura(
        cislo_faktury="ZAL-1",
        riadky=[riadok(poradie=1, nazov="Dodavka tovaru", mnozstvo="4", jednotka="ks",
                       jednotkova_cena="100.00", suma_bez_dph="400.00", sadzba_dph="23")],
        rekapitulacia_dph=[RekapitulaciaDPH(sadzba_dph="23", zaklad="400.00", dph="92.00",
                                            kategoria_dph="S", dovod_oslobodenia=None)],
        suma_bez_dph="450.00", suma_dph="92.00", suma_s_dph="492.00",
        zaplatena_zaloha="100.00", na_uhradu="392.00",
    )


VSETKY = {
    "jednoducha": jednoducha,
    "dve_sadzby_ciarky": dve_sadzby_ciarky,
    "prenesena_povinnost": prenesena_povinnost,
    "dobropis": dobropis,
    "zaloha_a_nesedi_sucet": zaloha_a_nesedi_sucet,
}
