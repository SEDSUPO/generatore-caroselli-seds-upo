"""Riconoscimento del feed RSS/Atom a partire dall'indirizzo di un sito.

Le testate si citano per homepage o per pagina di categoria
("https://sito.it/category/spazio/"), non per URL del feed: questo modulo fa il
passaggio, cosi' nell'app si puo' incollare l'indirizzo che si ha sottomano.

L'ordine in cui si provano i candidati non e' casuale, va dal piu' affidabile
al piu' speculativo:

1. l'URL dato, se ha l'aspetto di un feed (finisce per .xml, o contiene
   rss/feed/atom);
2. i <link rel="alternate" type="application/rss+xml"> dichiarati nella pagina:
   e' il modo in cui un sito indica il proprio feed;
3. i suffissi consueti sul percorso dato (/feed, /rss.xml, ...): su WordPress
   una pagina di categoria + /feed da' il feed della sola categoria, che e'
   quello che si vuole per una sezione "spazio" di un sito generalista;
4. gli stessi suffissi sulla radice del dominio, come ultima spiaggia.

Il candidato vince solo se scaricandolo si ottiene un feed con almeno una
notizia: un 404 servito come pagina HTML, o un feed vuoto, non passa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .config import INTESTAZIONI_HTTP, TIMEOUT_HTTP_SECONDI
from .feed_rss import leggi_feed_da_url

# Suffissi consueti, dal piu' diffuso al piu' raro: WordPress (feed/),
# installazioni generiche (rss.xml, index.xml), siti giapponesi che pubblicano
# ancora in RSS 1.0 (index.rdf), Blogger (feeds/posts/default).
_SUFFISSI = (
    "feed/",
    "feed",
    "rss/",
    "rss",
    "rss.xml",
    "feed.xml",
    "index.xml",
    "atom.xml",
    "index.rdf",
    "rss/index.rdf",
    "rss/news.xml",
    "rss/index.xml",
    "feed/rss",
    "atom",
    "feeds/posts/default",
    "?feed=rss2",
)
_ASPETTO_DI_FEED = re.compile(r"(\.xml$|/(rss|feed|atom)\b)", re.IGNORECASE)


@dataclass(frozen=True)
class FeedScoperto:
    url: str
    titolo: str
    # Link delle prime notizie: due URL diversi che servono lo stesso feed
    # (tipicamente "/feed" e "/feed/", o lo stesso feed con un parametro in
    # coda) hanno la stessa impronta, e cosi' si riconoscono come doppioni.
    impronta: tuple[str, ...]


class _ParserLinkFeed(HTMLParser):
    """Raccoglie gli href dei <link rel="alternate"> di tipo RSS o Atom."""

    def __init__(self) -> None:
        super().__init__()
        self.href: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attributi = {chiave: (valore or "") for chiave, valore in attrs}
        tipo = attributi.get("type", "").lower()
        rel = attributi.get("rel", "").lower()
        href = attributi.get("href", "")
        if href and "alternate" in rel and ("rss+xml" in tipo or "atom+xml" in tipo):
            self.href.append(href)


class _SitoIrraggiungibile(Exception):
    """Il sito non risponde proprio (DNS, connessione, timeout)."""


def _link_dichiarati(url_pagina: str) -> list[str]:
    try:
        risposta = httpx.get(
            url_pagina,
            follow_redirects=True,
            timeout=TIMEOUT_HTTP_SECONDI,
            headers=INTESTAZIONI_HTTP,
        )
        risposta.raise_for_status()
    except httpx.HTTPStatusError:
        return []  # risponde ma rifiuta la pagina: i percorsi dei feed possono comunque funzionare
    except httpx.HTTPError as e:
        # Senza questo controllo un sito spento costerebbe una trentina di tentativi
        # da 15 secondi l'uno, uno per ogni percorso consueto dei feed.
        raise _SitoIrraggiungibile(str(e)) from e
    if "html" not in risposta.headers.get("content-type", "").lower():
        return []

    parser = _ParserLinkFeed()
    try:
        parser.feed(risposta.text)
    except Exception:  # noqa: BLE001 - HTML di terzi: qualunque errore = nessun link trovato
        return []

    # Gli href possono essere relativi: si risolvono sull'URL finale, quello
    # dopo eventuali redirect, non su quello di partenza.
    base = str(risposta.url)
    return [urljoin(base, href) for href in parser.href]


def _con_suffissi(url_base: str) -> list[str]:
    percorso = url_base if url_base.endswith("/") else url_base + "/"
    return [percorso + suffisso for suffisso in _SUFFISSI]


def candidati(url_pagina: str) -> list[str]:
    """Gli URL da provare, in ordine di affidabilita', senza duplicati."""
    pezzi = urlparse(url_pagina)
    radice = f"{pezzi.scheme}://{pezzi.netloc}"
    senza_slash = url_pagina.rstrip("/")

    lista: list[str] = []
    if _ASPETTO_DI_FEED.search(senza_slash):
        lista.append(url_pagina)
    try:
        lista += _link_dichiarati(url_pagina)
    except _SitoIrraggiungibile:
        return lista
    lista += _con_suffissi(senza_slash)
    if senza_slash != radice:
        lista += _con_suffissi(radice)

    visti: set[str] = set()
    return [u for u in lista if not (u in visti or visti.add(u))]


def verifica_feed(url: str) -> FeedScoperto | None:
    """Ritorna il feed se l'URL ne serve uno leggibile e non vuoto, altrimenti None."""
    try:
        titolo, voci = leggi_feed_da_url(url, 3)
    except Exception:  # noqa: BLE001 - candidato scartato: rete, HTML al posto di XML, feed rotto
        return None
    if not voci:
        return None
    return FeedScoperto(url=url, titolo=titolo, impronta=tuple(v.url for v in voci))


def scopri_feed(url_pagina: str) -> FeedScoperto | None:
    """Primo feed valido trovato partendo dall'indirizzo di un sito o di una
    sua sezione. None se il sito non ne espone nessuno."""
    for candidato in candidati(url_pagina):
        trovato = verifica_feed(candidato)
        if trovato is not None:
            return trovato
    return None
