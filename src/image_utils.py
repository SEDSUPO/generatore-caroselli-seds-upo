"""Utility Pillow condivise tra i due compositori (illustrazioni generate e
foto reali del carosello notizie)."""

from __future__ import annotations

from PIL import Image, ImageOps


def cover_crop(immagine: Image.Image, larghezza: int, altezza: int) -> Image.Image:
    """Adatta un'immagine a (larghezza, altezza) con un ritaglio centrato che
    riempie tutto il riquadro senza deformare né lasciare bordi vuoti.
    """
    return ImageOps.fit(
        immagine, (larghezza, altezza), method=Image.LANCZOS, centering=(0.5, 0.5)
    )
