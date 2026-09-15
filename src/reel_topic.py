"""Suggerisce argomenti per un reel.

Prima scelta: le ultime notizie dei feed RSS delle testate spaziali (le stesse del
carosello notizie), passate a Gemini in una normale chiamata di testo che sceglie
le più adatte a un reel. Non consuma la quota della ricerca web, che sul piano
gratuito è molto stretta.

Ripiego, se i feed non sono configurati o non rispondono: la ricerca web di Gemini.
"""

from __future__ import annotations

import sys

from google.genai import types

from .config import Config, carica_feeds
from .feed_filtro import filtra_generalisti
from .feed_rss import leggi_tutti_i_feed
from .gemini_client import chiama_con_retry, crea_client
from .models import TopicSuggeriti, TopicSuggerito
from .notizie_feed import notizie_in_cache

NOTIZIE_PER_SUGGERIMENTO = 80

# Nome pubblico (non _ISTRUZIONI): la webapp lo mostra in UI così l'utente può copiarlo e
# incollarlo a mano in un'altra chat con ricerca web se in quel momento la quota è esaurita.
ISTRUZIONI = """\
Cerca sul web quali sono le notizie spaziali/aerospaziali più rilevanti delle ultime due
settimane, in italiano o internazionali.

Proponi 5 argomenti totali per un reel Instagram di divulgazione scientifica (pagina SEDS UPO):
un mix tra 2-3 notizie recenti (tipo "notizia_recente") trovate nella ricerca, e 2-3 argomenti di
approfondimento evergreen (tipo "approfondimento": es. come funziona un lancio, cosa sono i venti
solari, come si forma un buco nero) che non dipendono dall'attualità.

Per ciascuno scrivi un titolo breve e una frase su perché è un buon argomento per un reel adesso.
"""

_ISTRUZIONI_DA_FEED = """\
Queste sono le ultime notizie pubblicate dalle testate spaziali/aerospaziali (titoli in varie
lingue, con fonte e data). Proponi 5 argomenti totali per un reel Instagram di divulgazione
scientifica (pagina SEDS UPO), scritti in italiano:
- 2-3 notizie recenti (tipo "notizia_recente") scelte tra quelle qui sotto: le più interessanti e
  spiegabili in un minuto a un pubblico giovane, non le più tecniche o di nicchia. Ignora quelle che
  non riguardano davvero lo spazio. Il titolo, tradotto in italiano, deve riprendere fedelmente la
  notizia, senza aggiungere fatti che il titolo originale non contiene;
- 2-3 argomenti di approfondimento evergreen (tipo "approfondimento": es. come funziona un lancio,
  cosa sono i venti solari, come si forma un buco nero), meglio se collegati alle notizie del momento.

Per ciascuno scrivi un titolo breve e una frase su perché è un buon argomento per un reel adesso,
sempre in italiano anche quando la notizia originale è in un'altra lingua.

Notizie:
{notizie}
"""


def _titoli_recenti() -> list[str]:
    """Titoli dalla cache del carosello notizie se c'è (già tradotti e filtrati),
    altrimenti letti al volo dai feed (pochi secondi, senza traduzione)."""
    elenco = notizie_in_cache()
    if elenco is not None:
        voci = [v for v in elenco.voci if v.pertinente]
    else:
        feeds = carica_feeds()
        if not feeds:
            return []
        letti, _ = leggi_tutti_i_feed(feeds, 8)
        voci = filtra_generalisti(letti)
    return [
        f"- [{v.fonte}, {(v.data or '')[:10]}] {v.titolo_tradotto or v.titolo}"
        for v in voci[:NOTIZIE_PER_SUGGERIMENTO]
    ]


def _chiedi(config: Config, prompt: str, strumenti: list[types.Tool] | None) -> list[TopicSuggerito]:
    client = crea_client(config)

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=strumenti,
                response_mime_type="application/json",
                response_schema=TopicSuggeriti,
            ),
        )

    return chiama_con_retry(config, _chiamata, config.modelli_testo).parsed.topic


def suggerisci_topic(config: Config) -> list[TopicSuggerito]:
    try:
        titoli = _titoli_recenti()
    except Exception as e:  # noqa: BLE001 - feed irraggiungibili: si passa alla ricerca web
        print(f"  [topic] feed non leggibili ({e}), uso la ricerca web", file=sys.stderr)
        titoli = []

    if titoli:
        return _chiedi(config, _ISTRUZIONI_DA_FEED.format(notizie="\n".join(titoli)), None)
    return _chiedi(config, ISTRUZIONI, [types.Tool(google_search=types.GoogleSearch())])
