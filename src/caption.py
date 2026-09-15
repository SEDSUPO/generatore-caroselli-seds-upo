"""Genera la caption Instagram (testo esteso + hashtag) a partire dal testo
grezzo della ricerca. Passo indipendente dalle slide: usa lo stesso testo di
partenza dell'analisi (Step 1), ma un prompt diverso, pensato per un testo
lungo e discorsivo da mettere in descrizione al post, non per il copy breve
delle singole slide.
"""

from __future__ import annotations

from google.genai import types

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .models import CarosalloNotizie, CaptionGenerata

_ISTRUZIONI = """\
Sei un editor scientifico che scrive la caption di un post Instagram di divulgazione \
scientifica spaziale/aerospaziale (pagina SEDS UPO), a partire dal testo grezzo di una ricerca.

La caption è un testo esteso e discorsivo, ben diverso dal copy breve delle slide di un \
carosello: alcuni paragrafi in italiano, tono divulgativo ma rigoroso, che racconta la ricerca \
in modo coinvolgente, riprendendo dettagli concreti (numeri, nomi, confronti, cifre) presenti \
nel testo fornito. Mai clickbait, mai affermazioni non supportate dal testo. Può concludersi con \
una riflessione o un collegamento più ampio, come farebbe un buon articolo divulgativo.

Il corpo (`testo`) non deve superare {budget_corpo} caratteri: alla fine verranno aggiunte una \
firma e degli hashtag, quindi il corpo da solo deve restare entro questo limite per non far \
sforare la caption completa oltre i {max_caratteri} caratteri concessi da Instagram/dall'autore. \
Preferisci meno paragrafi ma completi piuttosto che tagliare una frase a metà.

Non includere hashtag nel testo del corpo, non includere la firma dell'autore: vengono \
aggiunti separatamente dopo.

Genera anche circa 5 hashtag pertinenti in italiano: parole singole minuscole (o parole_unite \
se proprio necessario), senza il simbolo #, un mix tra specifici (legati al soggetto preciso \
della ricerca) e generali (es. spazio, scienza, divulgazione).
"""

# Spazio riservato per firma ("~ Articolo di ...") + hashtag + gli "a capo" tra i blocchi,
# sottratto dal budget richiesto a Gemini per il solo corpo del testo.
_RISERVA_FIRMA_HASHTAG = 150


def genera_caption(config: Config, testo_grezzo: str) -> CaptionGenerata:
    client = crea_client(config)
    istruzioni = _ISTRUZIONI.format(
        budget_corpo=max(config.max_caratteri_caption - _RISERVA_FIRMA_HASHTAG, 200),
        max_caratteri=config.max_caratteri_caption,
    )
    prompt = f'{istruzioni}\n\nTesto grezzo della ricerca:\n"""\n{testo_grezzo.strip()}\n"""\n'

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CaptionGenerata,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_testo)
    return response.parsed


_ISTRUZIONI_NOTIZIE = """\
Sei l'editor della pagina Instagram di divulgazione scientifica spaziale/aerospaziale SEDS UPO. \
Scrivi la caption per un carosello che raccoglie più notizie brevi (una rassegna), non un \
singolo articolo.

La caption è breve e diretta, non un lungo articolo: un'introduzione di una frase che inquadra il \
tema del post, poi un elenco puntato con una riga per ciascuna notizia inclusa (titolo in breve + \
un dettaglio interessante dal riassunto), tono divulgativo, mai clickbait, nessuna affermazione \
non supportata dai riassunti forniti.

Non includere hashtag nel testo del corpo, non includere la firma: vengono aggiunti separatamente \
dopo.

Genera anche circa 5 hashtag pertinenti in italiano: parole singole minuscole, senza il simbolo #.

Notizie incluse in questo carosello:
{notizie}
"""


def genera_caption_notizie(config: Config, carosello: CarosalloNotizie) -> CaptionGenerata:
    client = crea_client(config)
    notizie_testo = "\n".join(
        f"- {s.titolo_piatto()} ({s.fonte}): {s.riassunto}" for s in carosello.slides
    )
    prompt = _ISTRUZIONI_NOTIZIE.format(notizie=notizie_testo)

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CaptionGenerata,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_testo)
    return response.parsed


def formatta_caption(caption: CaptionGenerata, nome_autore: str) -> str:
    """Assembla il file finale: corpo + firma opzionale + hashtag, in Python
    (non chiesto a Gemini) così l'autore si può cambiare senza rigenerare il testo.
    """
    blocchi = [caption.testo.strip()]
    if nome_autore.strip():
        blocchi.append(f"~ Articolo di {nome_autore.strip()}")
    hashtag = " ".join(f"#{t.lstrip('#')}" for t in caption.tag if t.strip())
    if hashtag:
        blocchi.append(hashtag)
    return "\n\n".join(blocchi) + "\n"
