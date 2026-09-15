"""Step 4: verifica che tutte le immagini di sfondo richieste siano presenti."""

from __future__ import annotations

from pathlib import Path

from .config import INPUT_IMMAGINI_DIR
from .models import CarosalloCompleto


class ImmaginiMancantiError(Exception):
    def __init__(self, cartella: Path, mancanti: list[str]):
        self.cartella = cartella
        self.mancanti = mancanti
        elenco = "\n".join(f"  - {nome}" for nome in mancanti)
        super().__init__(
            f"Mancano {len(mancanti)} immagini in {cartella}:\n{elenco}\n\n"
            "Genera le immagini con Nano Banana usando i prompt in "
            f"prompts/{cartella.name}/prompts_immagini.txt e salvale con questi nomi "
            "prima di rilanciare 'componi'."
        )


def valida_immagini_presenti(carosello: CarosalloCompleto) -> Path:
    cartella = INPUT_IMMAGINI_DIR / carosello.nome_carosello
    mancanti = [
        f"{slide.numero:02d}.png"
        for slide in carosello.slides
        if not (cartella / f"{slide.numero:02d}.png").is_file()
    ]
    if mancanti:
        raise ImmaginiMancantiError(cartella, mancanti)
    return cartella
