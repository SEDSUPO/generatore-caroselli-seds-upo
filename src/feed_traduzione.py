"""Titoli delle notizie dei feed tradotti in italiano, e marcati come pertinenti o
no allo spazio, con Gemini (portato da anime-bites-auto).

Serve solo per l'elenco da cui si scelgono le notizie: le testate sono in molte
lingue, e un titolo in giapponese o russo non dice nulla a chi deve decidere se
la notizia gli interessa. Il titolo della slide lo riscrive
src/notizie_analisi.py leggendo l'articolo.

La pertinenza serve per i siti generalisti della lista (quotidiani, portali), i
cui feed mescolano lo spazio con politica e sport: la si decide insieme alla
traduzione, senza richieste in più.

I titoli si mandano a blocchi da 50, in parallelo: con 150 titoli in una sola
richiesta il modello supera il timeout di 45 secondi (osservato), mentre tre blocchi
rispondono in pochi secondi l'uno. Se un blocco fallisce o torna disallineato (righe
perse o accorpate), per quel blocco si tengono i titoli originali, tutti
pertinenti: la traduzione è un aiuto alla lettura, non deve mai impedire di vedere
le notizie.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from google.genai import types
from pydantic import BaseModel, Field

from .config import Config
from .gemini_client import chiama_con_retry, crea_client

TITOLI_PER_BLOCCO = 50
BLOCCHI_IN_PARALLELO = 4

_ISTRUZIONI = """\
Questi sono titoli di notizie presi dai feed di diverse testate, in varie lingue. Per ognuno:

1. traducilo in italiano. Lascia invariati i nomi propri (missioni, agenzie, veicoli, persone: \
Artemis II, ESA, Starship) e i termini che in italiano si usano così come sono. Se è già in \
italiano riportalo identico. Traduci senza riscrivere né commentare.
2. indica se è pertinente: true se parla di spazio, astronomia, astrofisica, esplorazione \
spaziale, lanci, satelliti, industria o politica spaziale, scienze planetarie; false per tutto il \
resto (politica, sport, economia generica, cronaca...).

Stesso numero di elementi e stesso ordine dei titoli dati.

Titoli:
{titoli}
"""


class _Voce(BaseModel):
    titolo: str = Field(description="Il titolo tradotto in italiano")
    pertinente: bool = Field(description="True se la notizia riguarda lo spazio o l'astronomia")


class _Risposta(BaseModel):
    voci: list[_Voce]


class _ChiamataFallita(Exception):
    pass


class _RispostaDisallineata(Exception):
    pass


def _traduci(config: Config, titoli: list[str]) -> list[tuple[str, bool]]:
    client = crea_client(config)
    elenco = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titoli))

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=_ISTRUZIONI.format(titoli=elenco),
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=_Risposta),
        )

    try:
        voci = chiama_con_retry(config, _chiamata, config.modelli_testo).parsed.voci
    except Exception as e:  # noqa: BLE001 - qualunque errore Gemini: si tengono gli originali
        raise _ChiamataFallita(str(e)) from e
    if len(voci) != len(titoli):
        raise _RispostaDisallineata(f"{len(voci)} traduzioni per {len(titoli)} titoli")
    return [(v.titolo.strip() or originale, v.pertinente) for v, originale in zip(voci, titoli)]


def _originali(titoli: list[str]) -> list[tuple[str, bool]]:
    return [(t, True) for t in titoli]


def _traduci_senza_errori(config: Config, titoli: list[str]) -> list[tuple[str, bool]]:
    try:
        return _traduci(config, titoli)
    except (_ChiamataFallita, _RispostaDisallineata) as e:
        print(f"  [traduzione] blocco non tradotto: {e}", file=sys.stderr)
        return _originali(titoli)


def traduci_titoli(config: Config, titoli: list[str]) -> list[tuple[str, bool]]:
    """(titolo in italiano, pertinente) per ogni titolo, stesso ordine."""
    if not titoli:
        return []
    blocchi = [titoli[i : i + TITOLI_PER_BLOCCO] for i in range(0, len(titoli), TITOLI_PER_BLOCCO)]
    with ThreadPoolExecutor(max_workers=min(len(blocchi), BLOCCHI_IN_PARALLELO)) as pool:
        risultati = pool.map(lambda blocco: _traduci_senza_errori(config, blocco), blocchi)
    return [voce for blocco in risultati for voce in blocco]
