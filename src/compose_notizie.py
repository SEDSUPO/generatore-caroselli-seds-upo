"""Composizione di una slide del carosello notizie: foto reale a tutto campo +
pannello nero inferiore con titolo, fonte e freccia di swipe. Nessun rilevamento
di zone (a differenza di compose.py): qui il layout è un template fisso, non
serve capire cosa c'è nella foto — il pannello sta sempre sotto, punto.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .config import LOGO_PATH
from .image_utils import cover_crop
from .models import Segmento, SlideNotizia
from .text_metrics import (
    ALTEZZA_SLIDE,
    INTERLINEA,
    LARGHEZZA_SLIDE,
    altezza_blocco,
    carica_font,
    dimensione_iniziale,
    parole_da_segmenti,
    spezza_in_righe,
)

MARGINE_CONTENUTO = 80
MARGINE_LOGO = 40
ZONA_LOGO_LATO = 150

# Scala dedicata al titolo del carosello notizie: più contenuta di quella "hook"
# dell'altro modulo, pensata per titoli fino a ~90 caratteri su 2-3 righe, non 1.
CANDIDATI_DIMENSIONE_TITOLO = [72, 62, 54, 46, 40]
MAX_RIGHE_TITOLO = 3

NERO_PANNELLO = (8, 8, 10)
BIANCO = (255, 255, 255)
GRIGIO_FONTE = (176, 180, 194)
ACCENTO_ROSSO = (0xAA, 0x29, 0x22)

SFUMATURA_PANNELLO = 90  # px di dissolvenza tra foto e pannello nero

_font_fonte_cache = None


def _font_fonte():
    global _font_fonte_cache
    if _font_fonte_cache is None:
        _font_fonte_cache = carica_font(26, grassetto=False)
    return _font_fonte_cache


def _adatta_titolo(segmenti: list[Segmento], larghezza_max: float):
    parole = parole_da_segmenti(segmenti)
    num_caratteri = sum(len(p) for p, _ in parole)
    indice_iniziale = CANDIDATI_DIMENSIONE_TITOLO.index(
        dimensione_iniziale(num_caratteri, CANDIDATI_DIMENSIONE_TITOLO)
    )

    font, righe = carica_font(CANDIDATI_DIMENSIONE_TITOLO[-1]), []
    for indice in range(indice_iniziale, len(CANDIDATI_DIMENSIONE_TITOLO)):
        dimensione = CANDIDATI_DIMENSIONE_TITOLO[indice]
        font = carica_font(dimensione)
        righe = spezza_in_righe(parole, font, larghezza_max)
        if len(righe) <= MAX_RIGHE_TITOLO or indice == len(CANDIDATI_DIMENSIONE_TITOLO) - 1:
            break
    return font, righe


def _disegna_pannello_sfondo(canvas: Image.Image, y_inizio: int) -> None:
    """Riempie di nero da y_inizio in giù, con una dissolvenza morbida sopra
    per la transizione dalla foto al pannello."""
    y_sfumatura_inizio = max(y_inizio - SFUMATURA_PANNELLO, 0)

    colonna = Image.new("L", (1, ALTEZZA_SLIDE - y_sfumatura_inizio))
    for y in range(colonna.height):
        y_assoluto = y_sfumatura_inizio + y
        if y_assoluto >= y_inizio:
            alpha = 255
        else:
            alpha = int(255 * (y_assoluto - y_sfumatura_inizio) / SFUMATURA_PANNELLO)
        colonna.putpixel((0, y), alpha)

    maschera = colonna.resize((LARGHEZZA_SLIDE, colonna.height))
    velo = Image.new("RGBA", (LARGHEZZA_SLIDE, colonna.height), (*NERO_PANNELLO, 0))
    velo.putalpha(maschera)
    canvas.paste(velo, (0, y_sfumatura_inizio), velo)


def _disegna_titolo_centrato(
    draw: ImageDraw.ImageDraw, righe: list, font, y_inizio: int
) -> int:
    """Disegna righe centrate orizzontalmente, ritorna la y dopo l'ultima riga."""
    spazio = font.getlength(" ")
    altezza_riga = font.size * INTERLINEA
    y = y_inizio
    for riga in righe:
        larghezza_riga = sum(font.getlength(p) for p, _ in riga) + spazio * (len(riga) - 1)
        x = (LARGHEZZA_SLIDE - larghezza_riga) / 2
        for parola, evidenziato in riga:
            colore = ACCENTO_ROSSO if evidenziato else BIANCO
            draw.text((x, y), parola, font=font, fill=colore)
            x += font.getlength(parola) + spazio
        y += altezza_riga
    return int(y)


def _disegna_freccia_swipe(draw: ImageDraw.ImageDraw, centro_x: int, centro_y: int) -> None:
    """Una piccola freccia curva "swipe" (indica che il carosello continua)."""
    raggio = 17
    bbox = [centro_x - raggio, centro_y - raggio, centro_x + raggio, centro_y + raggio]
    angolo_inizio, angolo_fine = 200, 340
    draw.arc(bbox, start=angolo_inizio, end=angolo_fine, fill=BIANCO, width=4)

    angolo_rad = math.radians(angolo_fine)
    punta_x = centro_x + raggio * math.cos(angolo_rad)
    punta_y = centro_y + raggio * math.sin(angolo_rad)
    tangente = math.radians(angolo_fine + 90)
    lato = 11
    p1 = (punta_x + lato * math.cos(tangente - 2.6), punta_y + lato * math.sin(tangente - 2.6))
    p2 = (punta_x + lato * math.cos(tangente + 2.6), punta_y + lato * math.sin(tangente + 2.6))
    draw.polygon([(punta_x, punta_y), p1, p2], fill=BIANCO)


def _incolla_logo(canvas: Image.Image) -> None:
    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_adattato = ImageOps.contain(logo, (ZONA_LOGO_LATO, ZONA_LOGO_LATO))
    offset_x = MARGINE_LOGO + (ZONA_LOGO_LATO - logo_adattato.width) // 2
    offset_y = MARGINE_LOGO + (ZONA_LOGO_LATO - logo_adattato.height) // 2
    canvas.paste(logo_adattato, (offset_x, offset_y), logo_adattato)


def componi_slide_notizia(percorso_foto: Path, slide: SlideNotizia) -> Image.Image:
    """Compone una slide del carosello notizie: foto (cover-crop a 1080x1350) +
    pannello nero con titolo centrato, divisori, fonte e freccia di swipe + logo.
    """
    foto = Image.open(percorso_foto).convert("RGB")
    canvas = cover_crop(foto, LARGHEZZA_SLIDE, ALTEZZA_SLIDE)

    larghezza_max = LARGHEZZA_SLIDE - 2 * MARGINE_CONTENUTO
    font_titolo, righe = _adatta_titolo(slide.segmenti, larghezza_max)
    altezza_titolo = altezza_blocco(font_titolo, righe)

    # Struttura verticale del pannello, dall'alto in basso.
    padding_sopra_linea = 36
    linea_bianca_altezza = 4
    spazio_dopo_linea_bianca = 26
    spazio_dopo_titolo = 22
    linea_rossa_altezza = 3
    spazio_dopo_linea_rossa = 22
    altezza_riga_fonte = int(_font_fonte().size * 1.3)
    padding_sotto = 34

    altezza_pannello = (
        padding_sopra_linea
        + linea_bianca_altezza
        + spazio_dopo_linea_bianca
        + altezza_titolo
        + spazio_dopo_titolo
        + linea_rossa_altezza
        + spazio_dopo_linea_rossa
        + altezza_riga_fonte
        + padding_sotto
    )
    y_pannello = ALTEZZA_SLIDE - int(altezza_pannello)

    _disegna_pannello_sfondo(canvas, y_pannello)
    draw = ImageDraw.Draw(canvas)

    y = y_pannello + padding_sopra_linea
    larghezza_linea_bianca = 90
    x_linea = (LARGHEZZA_SLIDE - larghezza_linea_bianca) / 2
    draw.line([(x_linea, y), (x_linea + larghezza_linea_bianca, y)], fill=BIANCO, width=linea_bianca_altezza)
    y += linea_bianca_altezza + spazio_dopo_linea_bianca

    y = _disegna_titolo_centrato(draw, righe, font_titolo, y)
    y += spazio_dopo_titolo

    draw.line(
        [(MARGINE_CONTENUTO, y), (LARGHEZZA_SLIDE - MARGINE_CONTENUTO, y)],
        fill=ACCENTO_ROSSO,
        width=linea_rossa_altezza,
    )
    y += linea_rossa_altezza + spazio_dopo_linea_rossa

    draw.text((MARGINE_CONTENUTO, y), f"Fonte: {slide.fonte}", font=_font_fonte(), fill=GRIGIO_FONTE)
    _disegna_freccia_swipe(
        draw, LARGHEZZA_SLIDE - MARGINE_CONTENUTO - 15, y + altezza_riga_fonte // 2
    )

    _incolla_logo(canvas)

    return canvas
