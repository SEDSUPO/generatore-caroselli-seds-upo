"""Evidenziazione delle parole chiave nelle grafiche (carosello scientifico,
copertina del reel): rettangolo rosso arrotondato dietro la parola, testo bianco
sopra. Il testo rosso direttamente sul navy aveva poco contrasto.

Stesse proporzioni dei sottotitoli del reel (src/reel_sottotitoli.py), così
l'evidenziazione è riconoscibile ovunque.
"""

from __future__ import annotations

from PIL import ImageDraw, ImageFont

from .text_metrics import PUNTEGGIATURA

ROSSO = (0xAA, 0x29, 0x22)


def disegna_evidenziazioni(
    draw: ImageDraw.ImageDraw, riga: list[tuple[str, bool]], font: ImageFont.FreeTypeFont, x_inizio: float, y: float
) -> None:
    """Disegna i rettangoli di una riga di testo che parte da (x_inizio, y), da
    chiamare prima di scrivere il testo. Parole evidenziate consecutive sulla
    stessa riga condividono un solo rettangolo."""
    spazio = font.getlength(" ")
    dimensione = font.size
    linea_base = y + font.getmetrics()[0]
    altezza_maiuscole = -font.getbbox("H", anchor="ls")[1]
    alto = linea_base - altezza_maiuscole - dimensione * 0.2
    basso = linea_base + dimensione * 0.24
    margine_orizzontale = dimensione * 0.13

    x = x_inizio
    inizio_tratto: float | None = None
    fine_tratto = 0.0
    for parola, evidenziato in [*riga, ("", False)]:  # sentinella: chiude l'ultimo tratto
        if evidenziato:
            inizio_tratto = x if inizio_tratto is None else inizio_tratto
            # La punteggiatura finale ("Terra?") resta fuori dal rettangolo.
            fine_tratto = x + font.getlength(parola.rstrip(PUNTEGGIATURA) or parola)
        elif inizio_tratto is not None:
            draw.rounded_rectangle(
                [inizio_tratto - margine_orizzontale, alto, fine_tratto + margine_orizzontale, basso],
                radius=dimensione * 0.12,
                fill=ROSSO,
            )
            inizio_tratto = None
        x += font.getlength(parola) + spazio
