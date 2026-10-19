#!/usr/bin/env python3
"""
Kulturköpfe – täglicher Abruf „Heute läuft“

Liest die Spielplanseiten der Häuser und sucht dort nach strukturierten
Termindaten: schema.org-Events (JSON-LD) oder iCal-Dateien (.ics).
Das Ergebnis landet in heute.json, die die App beim Öffnen lädt.

Für die Museen werden die Ausstellungsseiten gelesen und die Ausstellungen
gesammelt, die heute laufen (Beginn bis Ende umfasst das heutige Datum).

Häuser, bei denen nichts gefunden wird, bekommen den Status "fehler".
Die App zeigt dann einfach nur den Spielplan- bzw. Ausstellungslink an.
"""
import datetime as dt
import html
import os
import json
import re
import sys
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Berlin")          # gilt auch für Hamburg, München, Wien
HEUTE = dt.datetime.now(TZ).date()
UA = "Kulturkoepfe-Spielplanabruf/1.0 (+https://klausbuss.github.io/kulturkoepfe/)"
TIMEOUT = 20
PAUSE = 1.0                             # Sekunden zwischen zwei Anfragen

# Allgemeine Pfade, die nacheinander probiert werden, wenn die gezielten
# Adressen nichts liefern.
PFADE = ["/spielplan", "/de/spielplan", "/programm", "/de/programm",
         "/kalender", "/de/kalender", "/termine", "/konzerte", "/"]

# id: (Website, [gezielte Spielplan-Adressen])
# Leere Liste = nur die allgemeinen Pfade probieren.
# None = nicht abrufen (z. B. Ballett, das die Opern-Website mitbenutzt).
HAEUSER = {
    # Berlin
    "so":  ("staatsoper-berlin.de", ["https://www.staatsoper-berlin.de/de/spielplan/"]),
    "do":  ("deutscheoperberlin.de", ["https://deutscheoperberlin.de/de_DE/calendar"]),
    "ko":  ("komische-oper-berlin.de", ["https://www.komische-oper-berlin.de/spielplan/"]),
    "sb":  ("staatsballett-berlin.de", ["https://www.staatsballett-berlin.de/de/spielplan"]),
    "dt":  ("deutschestheater.de", ["https://www.deutschestheater.de/programm/"]),
    "be":  ("berliner-ensemble.de", ["https://www.berliner-ensemble.de/spielplan"]),
    "vb":  ("volksbuehne.berlin", ["https://www.volksbuehne.berlin/de/spielplan"]),
    "mg":  ("gorki.de", ["https://www.gorki.de/de/spielplan"]),
    "sh":  ("schaubuehne.de", ["https://www.schaubuehne.de/de/spielplan/"]),
    "hau": ("hebbel-am-ufer.de", ["https://www.hebbel-am-ufer.de/programm/spielplan-tickets/"]),
    "fp":  ("palast.berlin", []),
    "pa":  ("parkaue.de", ["https://www.parkaue.de/spielplan/"]),
    "gr":  ("grips-theater.de", ["https://www.grips-theater.de/de/spielplan"]),
    "bf":  ("berlinerfestspiele.de", ["https://www.berlinerfestspiele.de/kalender"]),
    "ph":  ("berliner-philharmoniker.de", ["https://www.berliner-philharmoniker.de/konzerte/kalender/"]),
    "kh":  ("konzerthaus.de", ["https://www.konzerthaus.de/de/programm"]),
    "pbs": ("boulezsaal.de", ["https://www.boulezsaal.de/de/programm"]),
    "roc": ("roc-berlin.de", ["https://www.dso-berlin.de/de/konzerte/", "https://www.rsb-online.de/konzerte/"]),
    # Hamburg
    "hh-sh":  ("schauspielhaus.de", []),
    "hh-th":  ("thalia-theater.de", []),
    "hh-kn":  ("kampnagel.de", []),
    "hh-so":  ("die-hamburgische-staatsoper.de", []),
    "hh-hb":  None,
    "hh-elb": ("elbphilharmonie.de", ["https://www.elbphilharmonie.de/de/programm/"]),
    "hh-ndr": ("ndr.de", ["https://www.ndr.de/orchester_chor/elbphilharmonieorchester/konzerte/index.html"]),
    # München
    "m-resi": ("residenztheater.de", []),
    "m-mk":   ("muenchner-kammerspiele.de", []),
    "m-vt":   ("muenchner-volkstheater.de", []),
    "m-bso":  ("staatsoper.de", []),
    "m-bsb":  None,
    "m-gp":   ("gaertnerplatztheater.de", []),
    "m-mph":  ("mphil.de", []),
    "m-br":   ("brso.de", []),
    # Wien
    "w-burg": ("burgtheater.at", []),
    "w-vt":   ("volkstheater.at", []),
    "w-jos":  ("josefstadt.org", []),
    "w-so":   ("wiener-staatsoper.at", []),
    "w-sb":   None,
    "w-vo":   ("volksoper.at", []),
    "w-tadw": ("theater-wien.at", []),
    "w-kh":   ("konzerthaus.at", []),
    "w-mv":   ("musikverein.at", []),
    "w-sym":  ("wienersymphoniker.at", []),
}

# Museen: id: (Website, [gezielte Ausstellungsadressen]), None = nicht abrufen
SMB = "https://www.smb.museum/museen-einrichtungen/"
AUSSTELLUNGS_PFADE = ["/ausstellungen", "/de/ausstellungen", "/ausstellungen/aktuell",
                      "/exhibitions", "/en/exhibitions", "/programm", "/"]
MUSEEN = {
    # Berlin
    "b-nm":  ("smb.museum", [SMB + "neues-museum/ausstellungen/aktuell/"]),
    "b-pm":  None,                       # wegen Sanierung geschlossen
    "b-ang": ("smb.museum", [SMB + "alte-nationalgalerie/ausstellungen/aktuell/"]),
    "b-nng": ("smb.museum", [SMB + "neue-nationalgalerie/ausstellungen/aktuell/"]),
    "b-gg":  ("smb.museum", [SMB + "gemaeldegalerie/ausstellungen/aktuell/"]),
    "b-hbf": ("smb.museum", [SMB + "hamburger-bahnhof/ausstellungen/aktuell/"]),
    "b-hf":  ("humboldtforum.org", ["https://www.humboldtforum.org/de/ausstellungen/"]),
    "b-jmb": ("jmberlin.de", ["https://www.jmberlin.de/ausstellungen"]),
    "b-dhm": ("dhm.de", ["https://www.dhm.de/ausstellungen/"]),
    "b-mfn": ("museumfuernaturkunde.berlin", ["https://www.museumfuernaturkunde.berlin/besuch-planen/die-ausstellung"]),
    "b-kw":  ("kw-berlin.de", ["https://www.kw-berlin.de/"]),
    "b-hkw": ("hkw.de", ["https://www.hkw.de/programm"]),
    "b-gb":  ("berlinerfestspiele.de", ["https://www.berlinerfestspiele.de/gropius-bau/programm/ausstellungen"]),
    "b-fut": ("futurium.de", ["https://futurium.de/de/ausstellung"]),
    # Hamburg
    "hh-kh":    ("hamburger-kunsthalle.de", ["https://www.hamburger-kunsthalle.de/ausstellungen"]),
    "hh-mkg":   ("mkg-hamburg.de", ["https://www.mkg-hamburg.de/ausstellungen"]),
    "hh-dth":   ("deichtorhallen.de", ["https://www.deichtorhallen.de/de/ausstellungen"]),
    "hh-bkf":   ("buceriuskunstforum.de", ["https://www.buceriuskunstforum.de/ausstellungen"]),
    "hh-markk": ("markk-hamburg.de", ["https://markk-hamburg.de/ausstellungen/"]),
    "hh-mhg":   None,                    # wegen Modernisierung geschlossen
    "hh-am":    ("shmh.de", ["https://www.shmh.de/altonaer-museum/ausstellungen/"]),
    "hh-amh":   ("amh.de", ["https://amh.de/ausstellungen/"]),
    "hh-imm":   ("imm-hamburg.de", ["https://imm-hamburg.de/ausstellungen/"]),
    "hh-mda":   ("shmh.de", ["https://www.shmh.de/museum-der-arbeit/ausstellungen/"]),
    # München
    "m-ap":  ("pinakothek.de", ["https://www.pinakothek.de/de/alte-pinakothek/ausstellungen"]),
    "m-pdm": ("pinakothek.de", ["https://www.pinakothek.de/de/pinakothek-der-moderne/ausstellungen"]),
    "m-mb":  ("museum-brandhorst.de", ["https://www.museum-brandhorst.de/ausstellungen/"]),
    "m-lb":  ("lenbachhaus.de", ["https://www.lenbachhaus.de/entdecken/ausstellungen"]),
    "m-dm":  ("deutsches-museum.de", ["https://www.deutsches-museum.de/museumsinsel/ausstellungen"]),
    "m-bnm": ("bayerisches-nationalmuseum.de", ["https://www.bayerisches-nationalmuseum.de/ausstellungen"]),
    "m-hdk": ("hausderkunst.de", ["https://hausderkunst.de/ausstellungen"]),
    "m-gly": ("antike-am-koenigsplatz.mwn.de", ["https://antike-am-koenigsplatz.mwn.de/"]),
    "m-m5k": ("museum-fuenf-kontinente.de", ["https://www.museum-fuenf-kontinente.de/ausstellungen/"]),
    "m-nsd": ("nsdoku.de", ["https://www.nsdoku.de/ausstellungen"]),
    # Wien
    "w-khm":   ("khm.at", ["https://www.khm.at/besuchen/ausstellungen/"]),
    "w-alb":   ("albertina.at", ["https://www.albertina.at/ausstellungen/"]),
    "w-bel":   ("belvedere.at", ["https://www.belvedere.at/ausstellungen"]),
    "w-leo":   ("leopoldmuseum.org", ["https://www.leopoldmuseum.org/de/ausstellungen"]),
    "w-mumok": ("mumok.at", ["https://www.mumok.at/de/ausstellungen"]),
    "w-mak":   ("mak.at", ["https://www.mak.at/ausstellungen"]),
    "w-nhm":   ("nhm.at", ["https://www.nhm.at/ausstellungen"]),
    "w-wm":    ("wienmuseum.at", ["https://www.wienmuseum.at/ausstellungen"]),
    "w-tmw":   ("technischesmuseum.at", ["https://www.technischesmuseum.at/ausstellungen"]),
    "w-wmw":   ("weltmuseumwien.at", ["https://www.weltmuseumwien.at/ausstellungen/"]),
}

sitzung = requests.Session()
sitzung.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
_robots = {}


def erlaubt(url):
    """robots.txt der Website respektieren."""
    host = "{0.scheme}://{0.netloc}".format(urlparse(url))
    if host not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = sitzung.get(host + "/robots.txt", timeout=TIMEOUT)
            rp.parse(r.text.splitlines() if r.ok else [])
        except requests.RequestException:
            rp.parse([])
        _robots[host] = rp
    return _robots[host].can_fetch(UA, url)


def laden(url):
    if not erlaubt(url):
        raise PermissionError("robots.txt verbietet den Abruf")
    time.sleep(PAUSE)
    r = sitzung.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
    return r


def text(s):
    if not s:
        return ""
    if isinstance(s, list):
        s = s[0] if s else ""
    if isinstance(s, dict):
        s = s.get("name", "")
    s = BeautifulSoup(html.unescape(str(s)), "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", s).strip()


def zeitpunkt(wert):
    """ISO-Datum oder Datum mit Uhrzeit -> (date, 'HH:MM' oder '')."""
    if not wert:
        return None, ""
    wert = str(wert).strip()
    try:
        if len(wert) == 10:
            return dt.date.fromisoformat(wert), ""
        d = dt.datetime.fromisoformat(wert.replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone(TZ)
        return d.date(), d.strftime("%H:%M")
    except ValueError:
        return None, ""


# ---------- schema.org / JSON-LD ----------

def events_jsonld(soup, basis):
    gefunden = []

    def ist_event(obj):
        typ = obj.get("@type", [])
        typ = [typ] if isinstance(typ, str) else typ
        return any(isinstance(t, str) and t.endswith("Event") for t in typ)

    def durchgehen(obj):
        if isinstance(obj, list):
            for x in obj:
                durchgehen(x)
        elif isinstance(obj, dict):
            if ist_event(obj) and obj.get("startDate"):
                datum, uhr = zeitpunkt(obj.get("startDate"))
                if datum:
                    gefunden.append({
                        "datum": datum, "zeit": uhr,
                        "titel": text(obj.get("name")),
                        "ort": text(obj.get("location")),
                        "url": urljoin(basis, obj["url"]) if isinstance(obj.get("url"), str) else "",
                    })
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    durchgehen(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            durchgehen(json.loads(tag.string or tag.get_text() or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return gefunden


# ---------- Microdata (itemtype=".../Event") ----------

def events_microdata(soup, basis):
    gefunden = []
    for el in soup.find_all(attrs={"itemtype": re.compile(r"Event", re.I)}):
        st = el.find(attrs={"itemprop": "startDate"})
        if not st:
            continue
        datum, uhr = zeitpunkt(st.get("content") or st.get("datetime") or st.get_text())
        def eigen(prop):
            for x in el.find_all(attrs={"itemprop": prop}):
                besitzer = x.find_parent(attrs={"itemscope": True})
                if besitzer is el or (x is not el and besitzer is None):
                    return x
            return None
        name = eigen("name")
        link = eigen("url") or el.find("a", href=True)
        if datum and name:
            gefunden.append({"datum": datum, "zeit": uhr, "titel": text(name.get("content") or name.get_text()),
                             "ort": "", "url": urljoin(basis, link.get("href") or link.get("content") or "") if link else ""})
    return gefunden


# ---------- Notlösungen für Spielpläne ohne Standarddaten ----------

MONATE = "Jan|Feb|Mär|Mar|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Oct|Nov|Dez|Dec"
MONATSNAMEN = ["Jan\\w*", "Feb\\w*", "M(ä|ae|a)r\\w*", "Apr\\w*", "Ma[iy]", "Jun\\w*",
               "Jul\\w*", "Aug\\w*", "Sep\\w*", "O[ck]t\\w*", "Nov\\w*", "De[zc]\\w*"]
KEIN_TITEL = re.compile(
    r"^(\W*|\d{1,2}[.:]\d{2}.*|.*\b(" + MONATE + r")\w*\.? \d{4}|"
    r"(Mo|Di|Mi|Do|Fr|Sa|So)\.?,? \d.*|((Mo|Di|Mi|Do|Fr|Sa|So)\W*){3,}|"
    r"((Mo|Di|Mi|Do|Fr|Sa|So)\.?\s+)?(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)\.?|"
    r".*(Uhr|Karten|Tickets?|Merken|Ausverkauft|Restkarten|mehr|Info).*|"
    r"(Alle )?Termine|Details|Weiterlesen|Zum Stück.*|Zur Produktion.*|Programm|Spielplan)$", re.I)
ORT_WORTE = re.compile(r"(saal|bühne|foyer|kammer|studio|halle|anfahrt|spielstätte|kaufhaus)", re.I)
TITEL_KLASSE = re.compile(r"(title|titel|headline|heading|name)", re.I)
TITEL_KLASSE_STARK = re.compile(r"(title|titel|headline)", re.I)
DATUM = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.(\s?\d{2,4})?")
# 19:30 · 19.30 Uhr · 19 Uhr
UHRZEIT = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b"
                     r"|\b([01]?\d|2[0-3])[.]([0-5]\d)(?=\s*(Uhr|[–-]\s*\d{1,2}[.:]\d{2}\s*Uhr))"
                     r"|\b([01]?\d|2[0-3])\s*Uhr")


def uhrzeit_aus(t, ab=0):
    for m in UHRZEIT.finditer(t, ab):
        if m.group(1):
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        if m.group(3):
            return f"{int(m.group(3)):02d}:{m.group(4)}"
        return f"{int(m.group(6)):02d}:00"
    return ""


NEBENSACHE = re.compile(r"(place|venue|location|ort\b|date|datum|time|zeit|caption|meta|ticket|button)", re.I)


def ist_nebensache(x, grenze):
    """Liegt das Element in einem Orts-, Datums- oder Ticketbereich?"""
    while x is not None and x is not grenze:
        if NEBENSACHE.search(" ".join(x.get("class", []))):
            return True
        x = x.parent
    return False


def titel_kandidaten(el):
    """Mögliche Stücktitel in einem Element als (Rang, Text), beste zuerst."""
    kand = []
    for x in el.find_all(class_=TITEL_KLASSE):
        kl = " ".join(x.get("class", [])).lower()
        if "sub" in kl or "unter" in kl or "venue" in kl or "location" in kl or "place" in kl:
            continue
        if "day-name" in kl or "weekday" in kl or "wochentag" in kl:
            continue          # z. B. Burgtheater: s-event__day-name = „So. Sonntag“
        kand.append((0 if TITEL_KLASSE_STARK.search(kl) else 1, x))
    for x in el.find_all(["h2", "h3", "h4", "h5"]):
        kand.append((1, x))
    for x in el.find_all(["strong", "b"]):
        kand.append((2, x))
    for x in el.find_all("a", href=True):
        if not x["href"].startswith("#") and not ORT_WORTE.search(x["href"]):
            kand.append((3, x))
    gesehen, aus = set(), []
    for rang, x in sorted(kand, key=lambda k: k[0]):
        if ist_nebensache(x, el):
            continue
        t = text(x.get_text())
        if not t or len(t) < 3 or len(t) > 120 or KEIN_TITEL.match(t) or DATUM.fullmatch(t) or t in gesehen:
            continue
        if rang == 3 and ORT_WORTE.search(t) and len(t) < 30:
            continue
        gesehen.add(t)
        aus.append((rang, t))
    return aus


def link_in(el, basis):
    for a in el.find_all("a", href=True):
        h = a["href"]
        if not h.startswith(("#", "mailto:", "tel:")) and not ORT_WORTE.search(h):
            return urljoin(basis, h)
    return ""


def eintrag_um(start, basis, datum, uhr=""):
    """Vom Datumselement nach oben gehen, bis genau ein Titel gefunden ist."""
    el, notbehelf = start, None
    for _ in range(8):
        el = el.parent
        if el is None or el.name in ("body", "html"):
            break
        titel = titel_kandidaten(el)
        if not titel:
            continue
        inhalt = el.get_text(" ")
        # zu weit nach oben gelaufen, wenn mehrere Einträge mit eigener Uhrzeit drin sind
        if len(set(m.group(0) for m in UHRZEIT.finditer(inhalt))) > 2 and len(titel) > 3:
            break
        zeit = uhr
        if not zeit:
            # bevorzugt die Uhrzeit nach dem Datum (davor steht oft „Einführung 18:30“)
            m = DATUM.search(inhalt)
            zeit = uhrzeit_aus(inhalt, m.end() if m else 0) or uhrzeit_aus(inhalt)
        if not zeit:
            # Uhrzeit steht manchmal eine Ebene höher, z. B. in einer Fußzeile des Eintrags
            hoeher = el.parent
            for _ in range(2):
                if hoeher is None:
                    break
                zeiten = {m.group(0) for m in UHRZEIT.finditer(hoeher.get_text(" "))}
                if len(zeiten) > 2:
                    break
                if zeiten:
                    zeit = uhrzeit_aus(hoeher.get_text(" "))
                    break
                hoeher = hoeher.parent
        eintrag = {"datum": datum, "zeit": zeit, "titel": titel[0][1], "ort": "", "url": link_in(el, basis)}
        if titel[0][0] <= 1:          # Titel aus Klasse oder Überschrift: sicher genug
            return eintrag
        notbehelf = notbehelf or eintrag
    return notbehelf


def events_tagesliste(soup, basis):
    """Monatsspielplan als Liste von Tagesblöcken (z. B. HAU).

    Aufbau: <div class="month"> mit Überschrift „Oktober 2026“, darin <li class="day"
    data-filterdate="8-10"> je Tag und <div|li class="item"> je Termin mit Uhrzeit
    (.date strong), Spielort (.event-venue) und Titel (.info-wrap h3 / h4).
    Einträge ohne Uhrzeit (Festival-Hinweise, Kombitickets) werden übersprungen."""
    gefunden = []
    for monat in soup.select("div.month"):
        kopf = monat.find("h3")
        jahr = re.search(r"\b(20\d\d)\b", kopf.get_text(" ") if kopf else "")
        if not jahr:
            continue
        for tag in monat.select("li.day[data-filterdate]"):
            try:
                t, mo = (int(x) for x in tag["data-filterdate"].split("-"))
                datum = dt.date(int(jahr.group(1)), mo, t)
            except ValueError:
                continue
            if datum != HEUTE:
                continue
            for item in tag.select(".item"):
                uhr = item.select_one(".date strong")
                zeit = uhrzeit_aus(text(uhr.get_text(" "))) if uhr else ""
                if not zeit:
                    continue
                teile = [text(x.get_text(" ")) for x in item.select(".info-wrap h3, .info-wrap h4")]
                titel = " – ".join(x for x in teile if x)
                ort = item.select_one(".event-venue")
                link = item.select_one("a.show-btn[href]")
                gefunden.append({"datum": datum, "zeit": zeit, "titel": titel,
                                 "ort": ort.get("data-venue", "") if ort else "",
                                 "url": urljoin(basis, link["href"]) if link else ""})
    return gefunden


def events_timetags(soup, basis):
    gefunden = []
    for t in soup.find_all("time", attrs={"datetime": True}):
        datum, uhr = zeitpunkt(t["datetime"])
        if datum == HEUTE:
            e = eintrag_um(t, basis, datum, uhr)
            if e:
                gefunden.append(e)
    return gefunden


def events_datumstext(soup, basis):
    """Einträge, bei denen das Datum als Text steht, z. B. „11.10.2026“ oder „So, 11.10.26“."""
    d = HEUTE
    monat = MONATSNAMEN[d.month - 1]
    # „11.10.2026“, „So, 11.10.26“, „So 11/10“, „11/10/26“, „Di | 13. Okt“, „So 11 Okt 2026“, „Di 13 Oktober — 2026“
    muster = re.compile(
        r"^\W*((Mo|Di|Mi|Do|Fr|Sa|So|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\.?\s*[,|]?\s*)?"
        r"0?%d\s*(\.\s*0?%d\.|/\s*0?%d\b|\.?\s*(%s))" % (d.day, d.month, d.month, monat), re.I)
    treffer = []
    for el in soup.find_all(True):
        if el.name in ("script", "style", "option", "label", "select", "button", "noscript"):
            continue
        t = text(el.get_text(" "))
        if len(t) <= 40 and muster.match(t):
            # Jahr prüfen, falls angegeben
            jahr = re.search(r"\b(20\d\d)\b|\d{1,2}[./]\s*\d{1,2}[./]\s*(\d{2})\b", t)
            if jahr and int(next(g for g in jahr.groups() if g)) % 100 != d.year % 100:
                continue
            treffer.append(el)
    # nur die innersten Treffer behalten
    innerste = [el for el in treffer if not any(x is not el and el in x.parents for x in treffer)]
    gefunden = []
    for el in innerste:
        if el.find_parent(class_=re.compile(r"(filter|calendar-nav|datepicker|month-list|sprung)", re.I)):
            continue
        e = eintrag_um(el, basis, d)
        if e:
            gefunden.append(e)
    return gefunden


def events_json_eingebettet(soup, basis):
    """JSON-Daten in data-Attributen oder <script type=application/json> (z. B. Kampnagel, Next.js)."""
    quellen = [s.string or "" for s in soup.find_all("script", type=re.compile("json"))
               if s.get("type") != "application/ld+json"]
    for el in soup.find_all(True):
        for k, v in el.attrs.items():
            if k.startswith("data-") and isinstance(v, str) and v[:1] in "{[" and len(v) > 200:
                quellen.append(v)
    gefunden = []

    def start_von(o):
        for k in ("eventStart", "startDate", "start_date", "date_start", "dateStart", "start", "begin", "datetime", "date"):
            v = o.get(k)
            if isinstance(v, dict):
                v = v.get("date")
            if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
                return v.replace(" ", "T")[:19]
        return None

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            st = start_von(o)
            titel = o.get("title") or o.get("name") or o.get("headline")
            if st and isinstance(titel, str) and not DATUM.fullmatch(titel.strip()):
                datum, uhr = zeitpunkt(st)
                if datum == HEUTE:
                    unter = o.get("subline") or o.get("subtitle")
                    t = text(titel) + (f": {text(unter)}" if isinstance(unter, str) and len(text(titel)) < 25 else "")
                    url = o.get("href") or o.get("url") or o.get("link") or ""
                    gefunden.append({"datum": datum, "zeit": o.get("time") or uhr, "titel": t,
                                     "ort": "", "url": urljoin(basis, url) if isinstance(url, str) else ""})
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)

    for q in quellen:
        try:
            walk(json.loads(q))
        except (json.JSONDecodeError, TypeError):
            continue
    return gefunden


# ---------- iCal ----------

def ics_links(soup, basis):
    links = []
    for a in soup.find_all(["a", "link"], href=True):
        h = a["href"]
        if (h.lower().endswith(".ics") or "ical" in h.lower()
                or a.get("type") == "text/calendar" or h.startswith("webcal:")):
            links.append(urljoin(basis, h.replace("webcal:", "https:", 1)))
    return list(dict.fromkeys(links))[:2]


def events_ics(inhalt):
    zeilen = re.sub(r"\r?\n[ \t]", "", inhalt).splitlines()  # Zeilenumbrüche auflösen
    gefunden, ev = [], None
    for z in zeilen:
        if z == "BEGIN:VEVENT":
            ev = {}
        elif z == "END:VEVENT" and ev is not None:
            roh = ev.get("DTSTART", "")
            datum, uhr = None, ""
            m = re.match(r"(\d{8})(T(\d{6})(Z?))?", roh)
            if m:
                if m.group(2):
                    d = dt.datetime.strptime(m.group(1) + m.group(3), "%Y%m%d%H%M%S")
                    d = d.replace(tzinfo=dt.timezone.utc if m.group(4) else TZ).astimezone(TZ)
                    datum, uhr = d.date(), d.strftime("%H:%M")
                else:
                    datum = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            if datum:
                gefunden.append({"datum": datum, "zeit": uhr,
                                 "titel": text(ev.get("SUMMARY", "").replace("\\,", ",")),
                                 "ort": text(ev.get("LOCATION", "").replace("\\,", ",")),
                                 "url": ev.get("URL", "")})
            ev = None
        elif ev is not None and ":" in z:
            schluessel, wert = z.split(":", 1)
            ev[schluessel.split(";")[0]] = wert
    return gefunden


# ---------- Ausstellungen (Museen) ----------

def ausstellungen_jsonld(soup, basis):
    """schema.org-Events (z. B. ExhibitionEvent) mit Beginn und Ende."""
    gefunden = []

    def durchgehen(obj):
        if isinstance(obj, list):
            for x in obj:
                durchgehen(x)
        elif isinstance(obj, dict):
            typ = obj.get("@type", [])
            typ = [typ] if isinstance(typ, str) else typ
            if any(isinstance(t, str) and t.endswith("Event") for t in typ):
                von, _ = zeitpunkt(obj.get("startDate"))
                bis, _ = zeitpunkt(obj.get("endDate"))
                if bis:
                    gefunden.append({"titel": text(obj.get("name")), "von": von, "bis": bis,
                                     "url": urljoin(basis, obj["url"]) if isinstance(obj.get("url"), str) else ""})
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    durchgehen(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            durchgehen(json.loads(tag.string or tag.get_text() or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return gefunden


MONAT_NR = [re.compile(r"^" + m + r"$", re.I) for m in MONATSNAMEN]
# 12.10.2026 · 12.10. · 12. Oktober 2026 · 12 Oct 2026 · 12. Okt.
EIN_DATUM = re.compile(r"(\d{1,2})\.?\s*(?:(\d{1,2})\.|([A-Za-zÄÖÜäöü]{3,10})\.?)\s*((?:19|20)\d\d(?!\d)|\d\d(?![\d:.]))?")
STRICH = re.compile(r"^\s*(?:[–—-]|bis|to|until)\s*$", re.I)


def monat_aus(wort):
    for i, m in enumerate(MONAT_NR):
        if m.match(wort):
            return i + 1
    return None


def daten_im_text(t):
    """Alle Datumsangaben im Text als (start, ende, tag, monat, jahr oder None)."""
    aus = []
    for m in EIN_DATUM.finditer(t):
        tag = int(m.group(1))
        monat = int(m.group(2)) if m.group(2) else monat_aus(m.group(3))
        if not monat or not 1 <= monat <= 12 or not 1 <= tag <= 31:
            continue
        jahr = m.group(4)
        jahr = (2000 + int(jahr) if len(jahr) == 2 else int(jahr)) if jahr else None
        aus.append((m.start(), m.end(), tag, monat, jahr))
    return aus


def datum_bauen(tag, monat, jahr):
    try:
        return dt.date(jahr, monat, tag)
    except ValueError:
        return None


def zeitraum_aus(t):
    """„10.10.2026 – 12.1.2027“, „10. Okt. – 12. Jan. 2027“, „bis 12.01.2027“ -> (von, bis)."""
    daten = daten_im_text(t)
    for i in range(len(daten) - 1):
        a, b = daten[i], daten[i + 1]
        if b[4] and STRICH.match(t[a[1]:b[0]]):
            bis = datum_bauen(b[2], b[3], b[4])
            von = datum_bauen(a[2], a[3], a[4] or b[4])
            if von and bis and von > bis and not a[4]:
                von = datum_bauen(a[2], a[3], b[4] - 1)
            if von and bis and von <= bis:
                return von, bis
    for d in daten:
        if d[4] and re.search(r"\b(bis|until|through|noch bis)\s*$", t[:d[0]], re.I):
            bis = datum_bauen(d[2], d[3], d[4])
            if bis:
                return None, bis
    return None, None


def ausstellungen_html(soup, basis):
    """Elemente mit Zeitraum suchen und den Titel der Ausstellung daneben finden."""
    treffer = []
    for el in soup.find_all(True):
        if el.name in ("script", "style", "option", "select", "noscript", "head", "title"):
            continue
        t = text(el.get_text(" "))
        if not t or len(t) > 80:
            continue
        von, bis = zeitraum_aus(t)
        if bis:
            treffer.append((el, von, bis))
    elemente = [x[0] for x in treffer]
    gefunden = []
    for el, von, bis in treffer:
        if any(x is not el and x in el.descendants for x in elemente):
            continue        # nur die innersten Treffer
        if el.find_parent(["footer", "nav"]) or el.find_parent(class_=re.compile(r"(footer|opening|oeffnung|hours)", re.I)):
            continue
        e = eintrag_um(el, basis, bis)
        if e:
            gefunden.append({"titel": e["titel"], "von": von, "bis": bis, "url": e["url"]})
    return gefunden


# Häuser, deren Übersichtsseite keine Zeiträume zeigt: Das Ende („bis 30.06.2027“)
# steht erst auf der Seite der einzelnen Ausstellung.
MIT_DETAILSEITEN = {"b-mfn"}
MAX_DETAILSEITEN = 15


def ausstellungen_detailseiten(soup, url):
    """Unterseiten der Übersicht einzeln lesen: Titel aus der h1, Zeitraum aus dem Haupttext.
    Ausstellungen ohne Datum (Dauerausstellungen) fallen dabei heraus."""
    host = urlparse(url).netloc
    pfad = urlparse(url).path.rstrip("/") + "/"
    links = []
    for a in soup.find_all("a", href=True):
        ziel = urljoin(url, a["href"]).split("#")[0].split("?")[0]
        p = urlparse(ziel)
        if p.netloc == host and p.path.startswith(pfad) and p.path.rstrip("/") + "/" != pfad \
                and ziel not in links:
            links.append(ziel)
    gefunden = []
    for ziel in links[:MAX_DETAILSEITEN]:
        try:
            r = laden(ziel)
        except (PermissionError, requests.RequestException):
            continue
        seite = BeautifulSoup(r.text, "html.parser")
        kopf = seite.find("h1")
        titel = text(kopf.get_text(" ")) if kopf else ""
        haupt = seite.find("main") or seite
        for x in haupt(["script", "style", "noscript"]):
            x.decompose()
        von, bis = zeitraum_aus(text(haupt.get_text(" ")))
        if titel and bis:
            gefunden.append({"titel": titel, "von": von, "bis": bis, "url": r.url})
    return gefunden


def ausstellungen_abrufen(hid, eintrag):
    web, gezielt = eintrag
    basis = "https://www." + web
    kandidaten = list(dict.fromkeys(gezielt + [basis.rstrip("/") + p for p in AUSSTELLUNGS_PFADE]))
    fehler, erste_seite = [], None
    for url in kandidaten:
        try:
            r = laden(url)
        except PermissionError:
            fehler.append(f"{url} -> robots.txt verbietet Abruf")
            continue
        except requests.HTTPError as e:
            fehler.append(f"{url} -> {e.response.status_code if e.response is not None else '?'}")
            continue
        except requests.RequestException as e:
            fehler.append(f"{url} -> {type(e).__name__}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        if erste_seite is None:
            erste_seite = (r.url, r.text)
            speichere_diagnose(hid, erste_seite)
        liste, quelle = ausstellungen_jsonld(soup, r.url), "json-ld"
        if not liste:
            liste, quelle = ausstellungen_html(soup, r.url), "html-zeitraum"
        if not liste and hid in MIT_DETAILSEITEN:
            liste, quelle = ausstellungen_detailseiten(soup, r.url), "detailseiten"
        if not liste:
            continue
        aktuell, gesehen = [], set()
        for a in sorted(liste, key=lambda a: a["bis"]):
            if not a["titel"] or a["titel"] in gesehen or a["bis"] < HEUTE or (a["von"] and a["von"] > HEUTE):
                continue
            gesehen.add(a["titel"])
            aktuell.append({k: (v.isoformat() if isinstance(v, dt.date) else v)
                            for k, v in a.items() if v})
        return {"status": "ok" if aktuell else "keine", "ausstellungen": aktuell[:8],
                "quelle": r.url, "format": quelle}
    grund = "geladen, aber keine Ausstellungsdaten erkannt" if erste_seite else "keine Seite erreichbar"
    return {"status": "fehler", "grund": grund, "versuche": fehler[:6]}


# ---------- pro Haus ----------

def speichere_diagnose(hid, seite):
    os.makedirs("diagnose", exist_ok=True)
    with open(f"diagnose/{hid}.html", "w", encoding="utf-8") as f:
        f.write(f"<!-- {seite[0]} -->\n" + seite[1][:600_000])


def abrufen(hid, eintrag):
    web, gezielt = eintrag
    basis = "https://www." + web
    kandidaten = list(dict.fromkeys(gezielt + [basis.rstrip("/") + p for p in PFADE]))
    fehler = []
    erste_seite = None
    for url in kandidaten:
        try:
            r = laden(url)
        except PermissionError:
            fehler.append(f"{url} -> robots.txt verbietet Abruf")
            continue
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            fehler.append(f"{url} -> {code}")
            continue
        except requests.RequestException as e:
            fehler.append(f"{url} -> {type(e).__name__}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        if erste_seite is None:
            erste_seite = (r.url, r.text)
            speichere_diagnose(hid, erste_seite)
        termine = events_jsonld(soup, r.url)
        quelle = "json-ld"
        if not termine:
            termine = events_microdata(soup, r.url)
            quelle = "microdata"
        if not termine:
            for link in ics_links(soup, r.url):
                try:
                    termine += events_ics(laden(link).text)
                    quelle = "ical"
                except (requests.RequestException, PermissionError):
                    continue
        for name, erkenner in (("html-tagesliste", events_tagesliste),
                               ("json-eingebettet", events_json_eingebettet),
                               ("html-time", events_timetags),
                               ("html-datum", events_datumstext)):
            if termine:
                break
            termine = erkenner(soup, r.url)
            quelle = name
        if termine:
            heute = [t for t in termine if t["datum"] == HEUTE and t["titel"]]
            eindeutig = {}
            for t in sorted(heute, key=lambda t: t["zeit"] or "99"):
                eindeutig.setdefault((t["titel"], t["zeit"]), t)
            for t in eindeutig.values():
                t["titel"] = re.sub(r"^[^:]{3,30}(München|Berlin|Hamburg|Wien):\s*", "", t["titel"])
                t["titel"] = re.sub(r"\s+[-–]\s+\d{1,2}\.? ?(" + MONATE + r")\w*\.?$", "", t["titel"])
            vorstellungen = [{k: v for k, v in t.items() if k != "datum" and v}
                             for t in eindeutig.values()]
            return {"status": "ok" if vorstellungen else "keine",
                    "vorstellungen": vorstellungen,
                    "quelle": r.url, "format": quelle}
    grund = "geladen, aber keine Termindaten erkannt" if erste_seite else "keine Seite erreichbar"
    return {"status": "fehler", "grund": grund, "versuche": fehler[:6]}


def main():
    ergebnis = {"datum": HEUTE.isoformat(),
                "stand": dt.datetime.now(TZ).isoformat(timespec="minutes"),
                "haeuser": {}}
    for hid, eintrag in HAEUSER.items():
        if eintrag is None:
            continue
        try:
            res = abrufen(hid, eintrag)
        except Exception as e:  # ein Haus darf nie den ganzen Lauf stoppen
            res = {"status": "fehler", "grund": f"{type(e).__name__}: {e}"}
        ergebnis["haeuser"][hid] = res
        info = (", ".join(v["titel"] for v in res.get("vorstellungen", []))
                or res.get("grund", "heute nichts"))
        if res["status"] == "fehler":
            info += "  |  " + "; ".join(res.get("versuche", [])[:3])
        print(f"{hid:8} {res['status']:7} {info[:90]}", flush=True)

    for hid, eintrag in MUSEEN.items():
        if eintrag is None:
            continue
        try:
            res = ausstellungen_abrufen(hid, eintrag)
        except Exception as e:  # ein Museum darf nie den ganzen Lauf stoppen
            res = {"status": "fehler", "grund": f"{type(e).__name__}: {e}"}
        ergebnis["haeuser"][hid] = res
        info = (", ".join(a["titel"] + " (bis " + a["bis"] + ")" for a in res.get("ausstellungen", []))
                or res.get("grund", "keine laufende Ausstellung"))
        if res["status"] == "fehler":
            info += "  |  " + "; ".join(res.get("versuche", [])[:3])
        print(f"{hid:8} {res['status']:7} {info[:110]}", flush=True)

    with open("heute.json", "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in ergebnis["haeuser"].values() if r["status"] != "fehler")
    print(f"\n{ok} von {len(ergebnis['haeuser'])} Häusern mit Termindaten.")


if __name__ == "__main__":
    sys.exit(main())
