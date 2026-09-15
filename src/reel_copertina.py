"""Copertina del reel (1080x1920): immagine di sfondo + titolo con parole evidenziate
(sintassi **parola**, come nel resto dell'app: bianco su rettangolino rosso) + logo.

La griglia del profilo Instagram mostra la copertina ritagliata in 3:4: si perdono
circa 240 px in alto e in basso. Titolo e logo stanno quindi dentro la fascia
centrale, così restano leggibili sia nel reel sia nella griglia.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .config import LOGO_PATH
from .evidenziazione import disegna_evidenziazioni
from .image_utils import cover_crop
from .segmenti_markup import segmenti_da_markup
from .text_metrics import INTERLINEA, altezza_blocco, carica_font, parole_da_segmenti, spezza_in_righe

LARGHEZZA, ALTEZZA = 1080, 1920
TAGLIO_GRIGLIA = 240  # px nascosti in alto e in basso nella griglia del profilo
MARGINE = 90
CANDIDATI_FONT = [112, 98, 86, 76, 66]
RIGHE_MASSIME = 4
NAVY = (0x28, 0x2D, 0x43)


def _velo_superiore(canvas: Image.Image, fine: int) -> None:
    """Sfumatura navy dall'alto fino a `fine`: contrasto per il titolo su qualunque sfondo."""
    maschera = Image.new("L", (1, fine))
    for y in range(fine):
        maschera.putpixel((0, y), int(215 * (1 - (y / fine) ** 2)))
    velo = Image.new("RGBA", (LARGHEZZA, fine), (*NAVY, 0))
    velo.putalpha(maschera.resize((LARGHEZZA, fine)))
    canvas.paste(velo, (0, 0), velo)


def componi_copertina(sfondo: Path, titolo_markup: str, destinazione: Path) -> Path:
    canvas = cover_crop(Image.open(sfondo).convert("RGB"), LARGHEZZA, ALTEZZA)

    segmenti = segmenti_da_markup(titolo_markup.strip())
    parole = parole_da_segmenti(segmenti)
    larghezza_testo = LARGHEZZA - 2 * MARGINE
    y_titolo = TAGLIO_GRIGLIA + 70

    if parole:
        font, righe = carica_font(CANDIDATI_FONT[-1]), []
        for dimensione in CANDIDATI_FONT:
            font = carica_font(dimensione)
            righe = spezza_in_righe(parole, font, larghezza_testo)
            if len(righe) <= RIGHE_MASSIME:
                break

        _velo_superiore(canvas, int(y_titolo + altezza_blocco(font, righe) + 160))
        disegno = ImageDraw.Draw(canvas)
        spazio = font.getlength(" ")
        y = y_titolo
        for riga in righe:
            larghezza_riga = sum(font.getlength(p) for p, _ in riga) + spazio * (len(riga) - 1)
            x = (LARGHEZZA - larghezza_riga) / 2
            disegna_evidenziazioni(disegno, riga, font, x, y)
            for parola, _ in riga:
                disegno.text((x, y), parola, font=font, fill=(255, 255, 255))
                x += font.getlength(parola) + spazio
            y += font.size * INTERLINEA

    logo = ImageOps.contain(Image.open(LOGO_PATH).convert("RGBA"), (170, 170))
    canvas.paste(logo, ((LARGHEZZA - logo.width) // 2, ALTEZZA - TAGLIO_GRIGLIA - 60 - logo.height), logo)

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destinazione, quality=93)
    return destinazione
