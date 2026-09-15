"""Genera il copione di un reel Instagram a partire da un argomento (inserito
a mano o scelto tra i suggerimenti). Prompt diverso da quello delle slide:
qui serve un tono parlato/da video, non un copy scritto, e una struttura a
scene con indicazioni visive testuali (non prompt di generazione immagine —
scelta esplicita: l'utente si procura il materiale da solo)."""

from __future__ import annotations

from google.genai import types

from .config import Config
from .durata_parlato import formatta_durata, stima_secondi
from .gemini_client import chiama_con_retry, crea_client
from .models import CopioneGenerato, Reel

_PAROLE_MINIME = 150  # ~60s a 150 parole/minuto
_PAROLE_MASSIME = 400  # ~160s: oltre diventa lungo per un reel

_ISTRUZIONI = """\
Sei l'autore dei reel della pagina Instagram di divulgazione scientifica spaziale/aerospaziale \
SEDS UPO. Scrivi il copione di un reel sull'argomento indicato.

Il tono è PARLATO e diretto, come si parla in un video, non come si scrive un post: frasi brevi, \
colloquiali ma rigorose, mai clickbait, mai affermazioni non verificabili — se non sei sicuro di \
un dato/numero specifico, resta generale invece di inventarlo.

Struttura:
- `hook_varianti`: 2-3 alternative per il gancio iniziale (i primi 2-3 secondi): la frase che deve \
far fermare lo scroll. Diretta, curiosa o sorprendente, mai clickbait.
- `scene`: il corpo del reel diviso in scene brevi, ognuna con `testo_parlato` (cosa dire) e \
`visivo_suggerito` (una descrizione concreta in italiano di cosa riprendere o mostrare in quella \
scena — non un prompt di generazione immagine, è materiale che l'utente si procura da solo: può \
essere una ripresa da fare, un filmato di repertorio, una grafica, un'inquadratura di un oggetto).
- `chiusura_cta`: chiusura con invito ad azione (segui/salva/commenta), naturale, non forzata.

Il parlato complessivo (hook + tutte le scene + chiusura, sommato) deve essere tra le \
{parole_minime} e le {parole_massime} parole: sotto {parole_minime} il reel dura meno di un \
minuto (troppo corto), sopra {parole_massime} diventa lungo per il formato. Preferisci più scene \
brevi piuttosto che poche scene lunghe: aiuta il montaggio.

Argomento: {argomento}
"""


def genera_copione(config: Config, argomento: str) -> CopioneGenerato:
    client = crea_client(config)
    prompt = _ISTRUZIONI.format(
        parole_minime=_PAROLE_MINIME, parole_massime=_PAROLE_MASSIME, argomento=argomento
    )

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CopioneGenerato,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_testo)
    copione: CopioneGenerato = response.parsed

    if not copione.hook_varianti or not copione.scene:
        raise ValueError("Risposta Gemini incompleta: mancano hook o scene.")

    return copione


def testo_per_stima(copione: CopioneGenerato, hook_scelto: str | None = None) -> str:
    """Ricostruisce il parlato completo per stimare la durata, usando l'hook
    scelto se indicato, altrimenti il primo tra le varianti."""
    hook = hook_scelto or copione.hook_varianti[0]
    parti = [hook] + [s.testo_parlato for s in copione.scene] + [copione.chiusura_cta]
    return " ".join(parti)


def durata_stimata_ok(copione: CopioneGenerato) -> bool:
    secondi = stima_secondi(testo_per_stima(copione))
    return secondi >= 55  # piccolo margine sotto i 60s "nominali"


def formatta_teleprompter(reel: Reel) -> str:
    """Esportazione in formato leggibile ad alta voce durante la registrazione:
    righe corte e spaziate, non un testo compatto."""
    righe = [f"=== {reel.nome_reel} ===", f"Argomento: {reel.argomento}", "", "--- HOOK ---", reel.hook_scelto, ""]

    for scena in reel.scene:
        durata = formatta_durata(stima_secondi(scena.testo_parlato))
        righe.append(f"--- SCENA {scena.numero} (~{durata}) ---")
        righe.append(f"[DICI]: {scena.testo_parlato}")
        righe.append(f"[MOSTRA]: {scena.visivo_suggerito}")
        righe.append("")

    righe.append("--- CHIUSURA ---")
    righe.append(reel.chiusura_cta)
    righe.append("")

    durata_totale = formatta_durata(stima_secondi(reel.testo_completo()))
    righe.append(f"Durata stimata totale: ~{durata_totale}")

    return "\n".join(righe)
