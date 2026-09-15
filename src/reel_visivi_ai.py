"""Aiuti AI per i visivi del reel, in una sola chiamata Gemini per risparmiare
quota: una query di ricerca in inglese per ogni segmento (NASA e Wikimedia
rendono molto meglio in inglese) e il soggetto dell'illustrazione di copertina.
"""

from __future__ import annotations

from google.genai import types

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .image_prompts import BLOCCO_STILE_FISSO
from .models import AnalisiVisivaReel, Reel
from .reel_produzione import segmenti_parlati

_ISTRUZIONI = """\
Per un reel Instagram di divulgazione spaziale/aerospaziale devi trovare immagini e video su
archivi come NASA Image Library e Wikimedia Commons.

Per OGNI segmento qui sotto, nello stesso ordine, scrivi una query di ricerca in inglese: la
parola che sintetizza di più il soggetto da mostrare, al massimo 3 parole (meglio 1-2), concreta e
fotografabile, del tipo che funziona in un archivio fotografico: es. "Saturn", "solar flare",
"spacewalk", "Falcon 9". Niente articoli, aggettivi superflui o concetti astratti che non si
fotografano. Se il segmento non ha un soggetto fotografabile preciso (es. un invito a seguire la
pagina), usa il soggetto principale dell'argomento del reel.

Scrivi `musica_query`: 1-3 parole in inglese per cercare una musica di sottofondo adatta al tono
del reel in un archivio di musica libera (genere o atmosfera, es. "ambient space", "cinematic",
"electronic upbeat"). Niente titoli di brani o nomi di artisti.

Scrivi anche `soggetto_copertina`: un singolo soggetto semplice e diretto in inglese (un oggetto
con un'azione o un contesto chiaro, non una scena con più elementi) per un'illustrazione di
copertina che rappresenti l'argomento del reel.

Argomento del reel: {argomento}

Segmenti:
{segmenti}
"""


def analizza_visivi(config: Config, reel: Reel) -> AnalisiVisivaReel:
    segmenti = segmenti_parlati(reel)
    elenco = "\n".join(
        f"{i + 1}. [{s.etichetta}] Testo: {s.testo} | Visivo suggerito: {s.visivo_suggerito}"
        for i, s in enumerate(segmenti)
    )
    prompt = _ISTRUZIONI.format(argomento=reel.argomento, segmenti=elenco)
    client = crea_client(config)

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=AnalisiVisivaReel
            ),
        )

    analisi: AnalisiVisivaReel = chiama_con_retry(config, _chiamata, config.modelli_testo).parsed
    # Se il modello restituisce un numero di query diverso dai segmenti, si completa
    # con l'argomento invece di sfasare le query rispetto ai segmenti.
    analisi.query = (analisi.query + [reel.argomento] * len(segmenti))[: len(segmenti)]
    return analisi


def prompt_copertina(soggetto: str) -> str:
    """Prompt Nano Banana per la copertina del reel, nello stile fisso del brand.
    Formato 9:16, ma con il soggetto nella fascia centrale: la griglia del profilo
    Instagram mostra la copertina ritagliata ai lati e in alto/basso."""
    return (
        f'Subject: a simple technical line-art illustration of "{soggetto}", '
        "clean and minimal, a single clear subject, no diagram labels or callouts.\n"
        "Composition: vertical format 9:16 (1080x1920), subject horizontally centered and composed "
        "in the lower-middle part of the frame, all important elements inside the central area "
        "(the profile grid crops the edges), completely empty solid dark navy background (no "
        "illustration elements, no linework, no texture) covering the top 35% of the frame for a "
        "title, subtle grid lines only in the area with the subject.\n"
        f"{BLOCCO_STILE_FISSO}\n"
        "Constraints: no text, no letters, no numbers, no labels, no watermark, no realistic "
        "textures, no photographic elements, no clutter."
    )
