"""Analizza una singola notizia a partire dal suo URL, usando lo strumento
url_context di Gemini per leggere davvero il contenuto della pagina (non un
testo incollato a mano, a differenza del carosello scientifico).

Per le notizie scelte dai feed RSS c'è un ripiego: se la pagina non si lascia
leggere (paywall, blocchi anti-bot), si sintetizza dal titolo e dall'anteprima
che il feed ha già fornito, invece di perdere la notizia.
"""

from __future__ import annotations

from urllib.parse import urlparse

from google.genai import types

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .models import SlideNotizia, TitoloNotiziaGenerato

_REGOLE_TITOLO = """\
Scrivi un titolo breve e d'impatto in italiano per una slide di un carosello Instagram di \
divulgazione (pagina SEDS UPO): al massimo {max_caratteri} caratteri, tono divulgativo ma \
rigoroso, mai clickbait, nessuna affermazione non supportata dalla fonte.

Scomponi il titolo in una sequenza ordinata di segmenti di testo; imposta `evidenziato=true` \
SOLO per 1-2 parole o brevi espressioni chiave (mai frasi intere).

Scrivi anche un riassunto di 1-2 frasi della notizia in italiano: non verrà mostrato sulla slide, \
serve solo per comporre in seguito la caption del post che raccoglie più notizie insieme.
"""

_ISTRUZIONI_URL = """\
Leggi il contenuto di questa pagina web, che riporta una notizia scientifica \
spaziale/aerospaziale.

""" + _REGOLE_TITOLO + """
URL della notizia: {url}
"""

_ISTRUZIONI_TESTO = """\
Questa è una notizia spaziale/aerospaziale di cui hai solo il titolo e l'anteprima forniti dal \
feed RSS della testata ({fonte}), non l'articolo completo: basati solo su queste informazioni, \
senza aggiungere dettagli che non contengono.

""" + _REGOLE_TITOLO + """
Titolo originale: {titolo}
Anteprima: {anteprima}
"""


def _estrai_fonte(url: str) -> str:
    dominio = urlparse(url).netloc.lower()
    return dominio[4:] if dominio.startswith("www.") else dominio


def _costruisci_slide(config: Config, generato: TitoloNotiziaGenerato, url: str, numero: int) -> SlideNotizia:
    testo_piatto = "".join(s.testo for s in generato.segmenti)
    if not generato.segmenti or len(testo_piatto) > config.max_caratteri_notizia:
        raise ValueError(
            f"Titolo generato di {len(testo_piatto)} caratteri, oltre il limite di "
            f"{config.max_caratteri_notizia}."
        )
    return SlideNotizia(
        numero=numero,
        url=url,
        fonte=_estrai_fonte(url),
        segmenti=generato.segmenti,
        riassunto=generato.riassunto,
    )


def analizza_notizia(config: Config, url: str, numero: int) -> SlideNotizia:
    client = crea_client(config)
    prompt = _ISTRUZIONI_URL.format(max_caratteri=config.max_caratteri_notizia, url=url)

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(url_context=types.UrlContext())],
                response_mime_type="application/json",
                response_schema=TitoloNotiziaGenerato,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_testo)

    # Se il recupero dell'URL richiesto fallisce, Gemini a volte tenta un fallback (es. la
    # homepage del dominio) e, essendo comunque obbligato a rispettare lo schema JSON,
    # genera un titolo plausibile ma su una notizia completamente diversa. Per questo non
    # basta controllare che "almeno un URL" sia stato letto: se anche una sola voce è in
    # errore, l'URL specifico richiesto non è stato letto correttamente e va rifiutato tutto.
    metadati = response.candidates[0].url_context_metadata
    voci_stato = metadati.url_metadata if metadati else None
    tutte_riuscite = bool(voci_stato) and all(
        v.url_retrieval_status == types.UrlRetrievalStatus.URL_RETRIEVAL_STATUS_SUCCESS
        for v in voci_stato
    )
    if not tutte_riuscite:
        raise ValueError(
            f"Impossibile leggere il contenuto di questo URL (pagina irraggiungibile, bloccata "
            f"o che richiede un login): {url}"
        )

    return _costruisci_slide(config, response.parsed, url, numero)


def analizza_da_testo(config: Config, url: str, numero: int, titolo: str, anteprima: str) -> SlideNotizia:
    """Sintetizza la notizia dal titolo e dall'anteprima del feed RSS, senza leggere la pagina."""
    client = crea_client(config)
    prompt = _ISTRUZIONI_TESTO.format(
        max_caratteri=config.max_caratteri_notizia,
        fonte=_estrai_fonte(url),
        titolo=titolo,
        anteprima=anteprima or "(nessuna anteprima disponibile)",
    )

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=TitoloNotiziaGenerato
            ),
        )

    return _costruisci_slide(config, chiama_con_retry(config, _chiamata, config.modelli_testo).parsed, url, numero)
