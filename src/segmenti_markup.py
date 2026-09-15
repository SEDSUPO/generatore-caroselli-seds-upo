"""Conversione tra la lista di Segmento (usata da compose.py) e una rappresentazione
testuale editabile a mano, con `**parola**` per marcare l'evidenziazione in rosso —
usata dalla UI per permettere di modificare il copy di una slide già generata.
"""

from __future__ import annotations

import re

from .models import Segmento

_TAG_EVIDENZIATO = re.compile(r"\*\*(.+?)\*\*")


def markup_da_segmenti(segmenti: list[Segmento]) -> str:
    return "".join(f"**{s.testo}**" if s.evidenziato else s.testo for s in segmenti)


def segmenti_da_markup(testo: str) -> list[Segmento]:
    segmenti: list[Segmento] = []
    ultimo = 0
    for m in _TAG_EVIDENZIATO.finditer(testo):
        if m.start() > ultimo:
            segmenti.append(Segmento(testo=testo[ultimo : m.start()], evidenziato=False))
        if m.group(1):
            segmenti.append(Segmento(testo=m.group(1), evidenziato=True))
        ultimo = m.end()
    if ultimo < len(testo):
        segmenti.append(Segmento(testo=testo[ultimo:], evidenziato=False))
    return segmenti
