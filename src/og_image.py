"""Suggerisce una foto per una slide del carosello notizie estraendo il tag
og:image (o twitter:image come fallback) dalla pagina della notizia, con una
richiesta HTTP diretta — non tramite Gemini, più affidabile per un dato
strutturato come un URL di immagine.

Tutto qui dentro è "best effort": qualunque errore (pagina irraggiungibile,
nessun tag immagine, immagine non scaricabile o non valida) ritorna semplicemente
None. L'utente può sempre caricare la foto a mano se il suggerimento manca o
non è quello giusto — non deve mai bloccare la creazione della slide.
"""

from __future__ import annotations

import html
import io
from html.parser import HTMLParser

import httpx
from PIL import Image, UnidentifiedImageError

_TIMEOUT_SECONDI = 15
_USER_AGENT = "Mozilla/5.0 (compatible; GeneratoreCaroselliSEDS/1.0)"


class _ParserMetaImmagine(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.og_image: str | None = None
        self.twitter_image: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        attrs_dict = dict(attrs)
        proprieta = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
        contenuto = attrs_dict.get("content")
        if not contenuto:
            return
        if proprieta == "og:image" and self.og_image is None:
            self.og_image = contenuto
        elif proprieta == "twitter:image" and self.twitter_image is None:
            self.twitter_image = contenuto


def _trova_url_immagine(url_pagina: str) -> str | None:
    try:
        risposta = httpx.get(
            url_pagina, follow_redirects=True, timeout=_TIMEOUT_SECONDI, headers={"User-Agent": _USER_AGENT}
        )
        risposta.raise_for_status()
    except httpx.HTTPError:
        return None

    parser = _ParserMetaImmagine()
    try:
        parser.feed(risposta.text)
    except Exception:  # noqa: BLE001 - HTML di terzi, qualunque errore di parsing è "nessun suggerimento"
        return None

    immagine = parser.og_image or parser.twitter_image
    return html.unescape(immagine) if immagine else None


def _scarica(url_immagine: str) -> Image.Image | None:
    try:
        risposta = httpx.get(
            url_immagine, follow_redirects=True, timeout=_TIMEOUT_SECONDI, headers={"User-Agent": _USER_AGENT}
        )
        risposta.raise_for_status()
        immagine = Image.open(io.BytesIO(risposta.content))
        immagine.load()
        return immagine.convert("RGB")
    except (httpx.HTTPError, UnidentifiedImageError, OSError):
        return None


def scarica_immagine_suggerita(url_pagina: str, url_alternativo: str | None = None) -> Image.Image | None:
    """Trova ed effettivamente scarica l'immagine principale della pagina; se non
    c'è, quella indicata dal feed RSS (`url_alternativo`). Ritorna None (mai
    un'eccezione) se qualunque passaggio fallisce.
    """
    for url_immagine in (_trova_url_immagine(url_pagina), url_alternativo):
        if url_immagine:
            immagine = _scarica(url_immagine)
            if immagine is not None:
                return immagine
    return None
