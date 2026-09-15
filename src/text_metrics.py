"""Metriche di testo condivise tra Step 3 (stima quanto spazio chiedere a Nano
Banana nel prompt) e Step 6 (composizione Pillow reale, che adatta il font
alla zona effettivamente disponibile). Tenerle in un solo posto evita che le
due stime divergano tra loro.
"""

from __future__ import annotations

from PIL import ImageFont

from .config import FONT_VARIABLE_PATH
from .models import Segmento

LARGHEZZA_SLIDE = 1080
ALTEZZA_SLIDE = 1350
MARGINE_SICUREZZA = 80
INTERLINEA = 1.3

# Scala font per il corpo (fasi diverse da hook).
CANDIDATI_DIMENSIONE = [84, 72, 62, 54, 46, 40]
# Scala font per la slide Hook (copertina): copy istruito a essere corto
# (vedi text_analysis.py), quindi può permettersi caratteri molto più grandi.
CANDIDATI_DIMENSIONE_HOOK = [104, 90, 78, 68, 58, 50]

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def carica_font(dimensione: int, grassetto: bool = True) -> ImageFont.FreeTypeFont:
    chiave = (dimensione, grassetto)
    if chiave not in _font_cache:
        font = ImageFont.truetype(str(FONT_VARIABLE_PATH), dimensione)
        font.set_variation_by_axes([700 if grassetto else 400])
        _font_cache[chiave] = font
    return _font_cache[chiave]


def dimensione_iniziale(num_caratteri: int, candidati: list[int]) -> int:
    soglie = [50, 90, 140, 200, 260]
    for soglia, dimensione in zip(soglie, candidati):
        if num_caratteri <= soglia:
            return dimensione
    return candidati[-1]


PUNTEGGIATURA = ".,;:!?…»)”’"


def parole_da_segmenti(segmenti: list[Segmento]) -> list[tuple[str, bool]]:
    """Parole da disegnare, ognuna con il suo stato di evidenziazione.

    La punteggiatura che apre un segmento senza spazio davanti (es. "**Terra**?")
    si attacca alla parola precedente: altrimenti diventerebbe una parola a sé e
    verrebbe disegnata staccata ("Terra ?"). Mantiene lo stato della parola a cui si
    attacca; il rettangolo dell'evidenziazione la esclude comunque.
    """
    parole: list[tuple[str, bool]] = []
    for segmento in segmenti:
        for indice, pezzo in enumerate(segmento.testo.split(" ")):
            if not pezzo:
                continue
            if indice == 0 and parole and pezzo.strip(PUNTEGGIATURA) == "":
                precedente, evidenziata = parole[-1]
                parole[-1] = (precedente + pezzo, evidenziata)
            else:
                parole.append((pezzo, segmento.evidenziato))
    return parole


def spezza_in_righe(
    parole: list[tuple[str, bool]], font: ImageFont.FreeTypeFont, larghezza_max: float
) -> list[list[tuple[str, bool]]]:
    righe: list[list[tuple[str, bool]]] = []
    riga_corrente: list[tuple[str, bool]] = []
    larghezza_corrente = 0.0
    spazio = font.getlength(" ")

    for parola, evidenziato in parole:
        larghezza_parola = font.getlength(parola)
        aggiunta = larghezza_parola if not riga_corrente else larghezza_parola + spazio
        if riga_corrente and larghezza_corrente + aggiunta > larghezza_max:
            righe.append(riga_corrente)
            riga_corrente = [(parola, evidenziato)]
            larghezza_corrente = larghezza_parola
        else:
            riga_corrente.append((parola, evidenziato))
            larghezza_corrente += aggiunta

    if riga_corrente:
        righe.append(riga_corrente)
    return righe


def stima_blocco_testo(
    segmenti: list[Segmento], candidati: list[int], larghezza_max: float
) -> tuple[ImageFont.FreeTypeFont, list[list[tuple[str, bool]]]]:
    """Stima font e righe alla dimensione "ideale" di partenza della scala
    (quella che si userebbe se lo spazio non fosse un problema): usata sia per
    stimare lo spazio da chiedere a Nano Banana, sia come primo tentativo in
    fase di composizione reale.
    """
    parole = parole_da_segmenti(segmenti)
    num_caratteri = sum(len(p) for p, _ in parole)
    dimensione = dimensione_iniziale(num_caratteri, candidati)
    font = carica_font(dimensione)
    righe = spezza_in_righe(parole, font, larghezza_max)
    return font, righe


def altezza_blocco(font: ImageFont.FreeTypeFont, righe: list) -> float:
    return len(righe) * font.size * INTERLINEA
