"""Lettura dei feed RSS/Atom delle testate spaziali (portato da anime-bites-auto).

Parser scritto sopra xml.etree (libreria standard) invece di feedparser: i
feed che servono sono pochi e regolari, e cosi' l'app non aggiunge dipendenze.
Gestisce sia RSS 2.0 (<item>) sia Atom (<entry>), ed estrae l'URL della foto
da media:thumbnail, media:content, <enclosure> o dal primo <img> nel
riassunto - quale dei quattro dipende dalla testata.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from .config import INTESTAZIONI_HTTP, TIMEOUT_HTTP_SECONDI, Feed
from .models import VoceFeed

_NS_MEDIA = "{http://search.yahoo.com/mrss/}"
_NS_ATOM = "{http://www.w3.org/2005/Atom}"

_IMG_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

LUNGHEZZA_RIASSUNTO_GREZZO = 600


def pulisci_html(grezzo: str) -> str:
    """Toglie i tag e normalizza gli spazi: i campi dei feed arrivano spesso in HTML."""
    if not grezzo:
        return ""
    testo = html.unescape(re.sub(r"<[^>]+>", " ", grezzo))
    return re.sub(r"\s+", " ", testo).strip()


def _nome_locale(tag: str) -> str:
    """Il nome del tag senza il namespace: "{...}item" -> "item"."""
    return tag.rsplit("}", 1)[-1]


def _figlio(elemento, *nomi_locali: str):
    """Primo figlio con uno di questi nomi, qualunque sia il suo namespace.

    Serve perche' gli stessi campi arrivano marcati in modo diverso secondo il
    formato: RSS 2.0 li tiene senza namespace, RSS 1.0/RDF li mette nel proprio
    (e la data in quello Dublin Core). Cercarli per nome locale copre tutti i
    casi senza elencare i namespace uno per uno.
    """
    for figlio in elemento:
        if _nome_locale(figlio.tag) in nomi_locali:
            return figlio
    return None


def _testo(elemento) -> str:
    return (elemento.text or "").strip() if elemento is not None else ""


def _fonte_da_url(url: str) -> str:
    dominio = urlparse(url).netloc.lower()
    return dominio[4:] if dominio.startswith("www.") else dominio


def _data_iso(grezza: str) -> str | None:
    """Normalizza le date dei feed (RFC 822 per RSS, ISO 8601 per Atom) in ISO
    8601, per poter ordinare notizie provenienti da testate diverse."""
    grezza = grezza.strip()
    if not grezza:
        return None
    try:
        return parsedate_to_datetime(grezza).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(grezza.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _url_immagine(elemento, descrizione_grezza: str) -> str | None:
    # I tag media:* si confrontano per nome locale: le testate dichiarano il
    # namespace Media RSS in varianti diverse (con o senza slash finale), e
    # l'URL a volte sta nell'attributo url, a volte nel testo del tag (MAL).
    for figlio in elemento:
        nome_locale = figlio.tag.rsplit("}", 1)[-1]
        if nome_locale not in ("thumbnail", "content"):
            continue
        url = figlio.get("url") or (figlio.text or "").strip()
        if url.startswith("http"):
            return url

    allegato = elemento.find("enclosure")
    if allegato is not None and (allegato.get("type") or "").startswith("image"):
        return allegato.get("url")

    incorporata = _IMG_SRC.search(descrizione_grezza)
    return incorporata.group(1) if incorporata else None


def _voce_da_item(elemento, feed: Feed) -> VoceFeed | None:
    titolo = _testo(_figlio(elemento, "title"))
    link = _testo(_figlio(elemento, "link"))
    if not titolo or not link:
        return None

    descrizione = _testo(_figlio(elemento, "description", "encoded", "summary"))
    # pubDate per RSS 2.0, dc:date per RSS 1.0/RDF.
    data = _testo(_figlio(elemento, "pubDate", "date"))
    return VoceFeed(
        titolo=pulisci_html(titolo),
        url=link,
        fonte=_fonte_da_url(link),
        feed=feed.nome,
        riassunto_grezzo=pulisci_html(descrizione)[:LUNGHEZZA_RIASSUNTO_GREZZO],
        url_immagine=_url_immagine(elemento, descrizione),
        data=_data_iso(data),
    )


def _voce_da_entry(elemento, feed: Feed) -> VoceFeed | None:
    titolo = _testo(elemento.find(f"{_NS_ATOM}title"))
    link_elemento = elemento.find(f"{_NS_ATOM}link")
    link = link_elemento.get("href") if link_elemento is not None else ""
    if not titolo or not link:
        return None

    descrizione = _testo(elemento.find(f"{_NS_ATOM}summary")) or _testo(
        elemento.find(f"{_NS_ATOM}content")
    )
    data = _testo(elemento.find(f"{_NS_ATOM}updated")) or _testo(
        elemento.find(f"{_NS_ATOM}published")
    )
    return VoceFeed(
        titolo=pulisci_html(titolo),
        url=link,
        fonte=_fonte_da_url(link),
        feed=feed.nome,
        riassunto_grezzo=pulisci_html(descrizione)[:LUNGHEZZA_RIASSUNTO_GREZZO],
        url_immagine=_url_immagine(elemento, descrizione),
        data=_data_iso(data),
    )


def _titolo_del_feed(radice) -> str:
    """Il nome che il feed dichiara di avere: serve per intitolare un feed
    scoperto in automatico. Sta in channel/title (RSS 2.0), in un channel
    namespaced (RSS 1.0) o direttamente sotto la radice (Atom)."""
    canale = _figlio(radice, "channel", "feed")
    for elemento in (canale, radice):
        if elemento is None:
            continue
        titolo = _testo(_figlio(elemento, "title"))
        if titolo:
            return pulisci_html(titolo)
    return ""


def leggi_feed_da_url(url: str, massimo: int, nome_feed: str = "") -> tuple[str, list[VoceFeed]]:
    """Scarica e interpreta un feed dato il suo URL, ritornando anche il titolo
    che il feed dichiara. Solleva un'eccezione su errori di rete o di XML: chi
    chiama decide se segnalarli o ignorarli."""
    risposta = httpx.get(
        url, follow_redirects=True, timeout=TIMEOUT_HTTP_SECONDI, headers=INTESTAZIONI_HTTP
    )
    risposta.raise_for_status()

    # risposta.content (byte) e non .text: l'encoding dichiarato nel prologo XML
    # e' quello giusto, mentre httpx potrebbe averne indovinato un altro.
    radice = ElementTree.fromstring(risposta.content)
    titolo = _titolo_del_feed(radice)
    riferimento = Feed(nome=nome_feed or titolo or url, url=url)

    voci: list[VoceFeed] = []
    for elemento in radice.iter():
        # Il confronto e' sul nome locale: "item" copre sia RSS 2.0 sia
        # RSS 1.0/RDF, dove lo stesso tag vive in un namespace.
        nome = _nome_locale(elemento.tag)
        if nome == "item":
            voce = _voce_da_item(elemento, riferimento)
        elif nome == "entry":
            voce = _voce_da_entry(elemento, riferimento)
        else:
            continue
        if voce is not None:
            voci.append(voce)
        if len(voci) >= massimo:
            break

    return titolo, voci


def leggi_feed(feed: Feed, massimo: int) -> list[VoceFeed]:
    """Scarica e interpreta un feed della lista configurata."""
    _, voci = leggi_feed_da_url(feed.url, massimo, nome_feed=feed.nome)
    return voci


def leggi_tutti_i_feed(feeds: list[Feed], massimo_per_feed: int) -> tuple[list[VoceFeed], list[str]]:
    """Legge tutti i feed in parallelo (sono richieste di rete indipendenti:
    in sequenza il caricamento della pagina durerebbe la somma dei tempi).

    Ritorna le notizie ordinate dalla piu' recente e l'elenco degli errori, un
    messaggio per ogni feed che non ha risposto: un feed rotto non deve
    impedire di vedere quelli buoni.
    """
    if not feeds:
        return [], []

    voci: list[VoceFeed] = []
    errori: list[str] = []

    with ThreadPoolExecutor(max_workers=min(len(feeds), 16)) as pool:
        risultati = pool.map(lambda f: (f, _leggi_senza_errori(f, massimo_per_feed)), feeds)
        for feed, (lette, errore) in risultati:
            if errore:
                errori.append(f"{feed.nome}: {errore}")
            voci.extend(lette)

    # Le notizie senza data vanno in fondo, non in cima: meglio in coda che
    # spacciate per le piu' fresche.
    voci.sort(key=lambda v: v.data or "", reverse=True)

    # Feed della stessa testata si sovrappongono (es. "News" e "Tutto" di ANN
    # riportano gli stessi articoli): si tiene la prima occorrenza, cioe' la
    # piu' recente dopo l'ordinamento.
    viste: set[str] = set()
    uniche = []
    for voce in voci:
        if voce.url in viste:
            continue
        viste.add(voce.url)
        uniche.append(voce)

    return uniche, errori


def _leggi_senza_errori(feed: Feed, massimo: int) -> tuple[list[VoceFeed], str | None]:
    try:
        return leggi_feed(feed, massimo), None
    except httpx.HTTPError as e:
        return [], f"feed non raggiungibile ({type(e).__name__})"
    except ElementTree.ParseError:
        return [], "risposta non in formato RSS/Atom valido"
