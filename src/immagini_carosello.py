"""Immagini pubbliche (NASA Image Library, Wikimedia Commons) come sfondo delle
slide del carosello scientifico, in alternativa all'illustrazione Nano Banana.

Stessa ricerca del creatore di reel (src/reel_media.py); qui in più: le parole
chiave suggerite da Gemini per tutte le slide in una sola chiamata, e il salvataggio
dell'immagine come sfondo della slide con i dati per i crediti.
"""

from __future__ import annotations

import io

from google.genai import types
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import INPUT_IMMAGINI_DIR, Config
from .gemini_client import chiama_con_retry, crea_client
from .models import CarosalloCompleto, CreditoImmagine, QueryImmaginiSlide
from .reel_media import ErroreMedia, scarica_immagine_pubblica

_LATO_MASSIMO = 3000  # la slide è 1080x1350: oltre non serve

_ISTRUZIONI = """\
Per ogni slide di questo carosello Instagram di divulgazione spaziale, nello stesso ordine, scrivi
la parola chiave in inglese per cercare una foto reale nella NASA Image Library o su Wikimedia
Commons: la parola che sintetizza di più il soggetto, al massimo 3 parole (meglio 1-2), concreta e
fotografabile (es. "Saturn", "cleanroom", "Mars rover", "solar flare"). Niente concetti astratti,
né loghi o nomi di agenzie e uffici: se la slide parla di un ente, cerca l'oggetto fisico di cui si
occupa (es. per la protezione planetaria "spacecraft cleanroom", non "NASA logo").

Argomento: {argomento}

Slide:
{slide}
"""


def suggerisci_query(config: Config, carosello: CarosalloCompleto) -> list[str]:
    elenco = "\n".join(
        f"{s.numero}. Testo: {s.testo_piatto()} | Soggetto illustrazione: {s.soggetto_immagine}"
        for s in carosello.slides
    )
    client = crea_client(config)

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=_ISTRUZIONI.format(argomento=carosello.argomento, slide=elenco),
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=QueryImmaginiSlide),
        )

    query = chiama_con_retry(config, _chiamata, config.modelli_testo).parsed.query
    # Numero sbagliato di risposte: si completa con l'argomento invece di sfasare le slide.
    return (query + [carosello.argomento] * len(carosello.slides))[: len(carosello.slides)]


def salva_immagine_pubblica(nome_carosello: str, numero: int, fonte: str, riferimento: str) -> None:
    """Scarica l'immagine e la salva come sfondo della slide (input_immagini/<nome>/NN.png)."""
    # Per NASA basta la versione "large" (circa 1900 px): l'originale può pesare decine di MB.
    dati = scarica_immagine_pubblica(fonte, riferimento, ["~large.jpg", "~orig.jpg", "~medium.jpg", "~orig.png"])
    try:
        immagine = Image.open(io.BytesIO(dati))
        immagine.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ErroreMedia("Il file scaricato non è un'immagine valida.") from e
    immagine = ImageOps.exif_transpose(immagine).convert("RGB")
    immagine.thumbnail((_LATO_MASSIMO, _LATO_MASSIMO), Image.LANCZOS)
    cartella = INPUT_IMMAGINI_DIR / nome_carosello
    cartella.mkdir(parents=True, exist_ok=True)
    immagine.save(cartella / f"{numero:02d}.png")


def testo_crediti(carosello: CarosalloCompleto) -> str:
    """Riga per la caption con autori e licenze delle immagini pubbliche usate."""
    voci: list[str] = []
    for slide in carosello.slides:
        credito: CreditoImmagine | None = slide.credito_immagine
        if credito is None:
            continue
        voce = credito.autore or ("NASA" if credito.fonte == "nasa" else "Wikimedia Commons")
        if credito.licenza:
            voce += f" ({credito.licenza})"
        if credito.fonte == "wikimedia" and "Wikimedia" not in voce:
            voce = f"Wikimedia Commons: {voce}"
        if voce not in voci:
            voci.append(voce)
    return "Crediti immagini: " + "; ".join(voci) if voci else ""
