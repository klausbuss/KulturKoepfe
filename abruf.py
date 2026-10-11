#!/usr/bin/env python3
"""
Kulturköpfe – täglicher Abruf „Heute läuft“

Liest die Spielplanseiten der Häuser und sucht dort nach strukturierten
Termindaten: schema.org-Events (JSON-LD) oder iCal-Dateien (.ics).
Das Ergebnis landet in heute.json, die die App beim Öffnen lädt.

Häuser, bei denen nichts gefunden wird, bekommen den Status "fehler".
Die App zeigt dann einfach nur den Spielplan-Link an.
"""
import datetime as dt
import html
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
    "hau": ("hebbel-am-ufer.de", ["https://www.hebbel-am-ufer.de/programm/spielplan/"]),
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
    "w-sb":   ("wiener-staatsballett.at", []),
    "w-vo":   ("volksoper.at", []),
    "w-tadw": ("theater-wien.at", []),
    "w-kh":   ("konzerthaus.at", []),
    "w-mv":   ("musikverein.at", []),
    "w-sym":  ("wienersymphoniker.at", []),
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


# ---------- pro Haus ----------

def abrufen(hid, eintrag):
    web, gezielt = eintrag
    basis = "https://www." + web
    kandidaten = list(dict.fromkeys(gezielt + [basis.rstrip("/") + p for p in PFADE]))
    letzter_fehler = "keine strukturierten Termindaten gefunden"
    for url in kandidaten:
        try:
            r = laden(url)
        except PermissionError as e:
            letzter_fehler = str(e)
            continue
        except requests.RequestException as e:
            letzter_fehler = f"{url}: {type(e).__name__}"
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        termine = events_jsonld(soup, r.url)
        quelle = "json-ld"
        if not termine:
            for link in ics_links(soup, r.url):
                try:
                    termine += events_ics(laden(link).text)
                    quelle = "ical"
                except (requests.RequestException, PermissionError):
                    continue
        if termine:
            heute = [t for t in termine if t["datum"] == HEUTE and t["titel"]]
            eindeutig = {}
            for t in sorted(heute, key=lambda t: t["zeit"] or "99"):
                eindeutig.setdefault((t["titel"], t["zeit"]), t)
            vorstellungen = [{k: v for k, v in t.items() if k != "datum" and v}
                             for t in eindeutig.values()]
            return {"status": "ok" if vorstellungen else "keine",
                    "vorstellungen": vorstellungen,
                    "quelle": r.url, "format": quelle}
    return {"status": "fehler", "grund": letzter_fehler}


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
        print(f"{hid:8} {res['status']:7} {info[:90]}", flush=True)

    with open("heute.json", "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in ergebnis["haeuser"].values() if r["status"] != "fehler")
    print(f"\n{ok} von {len(ergebnis['haeuser'])} Häusern mit Termindaten.")


if __name__ == "__main__":
    sys.exit(main())
