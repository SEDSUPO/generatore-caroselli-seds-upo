"""Utility condivise tra i due blueprint Flask (caroselli scientifici e carosello
notizie): niente di specifico al dominio, solo helper sui nomi cartella/URL."""

from __future__ import annotations

import re
import unicodedata

NOME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,60}$")


def slugify(titolo: str) -> str:
    """Trasforma un titolo libero (es. 'Vita extraterrestre: ipotesi e scoperte')
    in uno slug valido per nomi di cartella/URL (es. 'vita-extraterrestre-ipotesi-e-scoperte').
    """
    normalizzato = unicodedata.normalize("NFKD", titolo)
    ascii_puro = normalizzato.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_puro).strip("-")
    return slug[:60]
