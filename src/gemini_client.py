"""Client Gemini condiviso con cascata tra modelli + retry/backoff su
rate-limit ed errori transitori."""

from __future__ import annotations

import sys
import time
from typing import Callable

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import Config

# Codici HTTP per cui ha senso ritentare (sullo stesso modello o passando al successivo).
# 404 incluso: un modello non trovato/non più disponibile non deve bloccare l'intera
# cascata, deve solo far passare al modello successivo nella lista. 499 incluso: è il
# codice che Google restituisce quando annulla una richiesta perché ha superato il
# timeout comunicato dal client (vedi sotto) — è un timeout, va trattato come tale.
_CODICI_RITENTABILI = {404, 429, 499, 500, 502, 503, 504}

# Timeout per singolo tentativo di richiesta HTTP: l'SDK, se non lo si imposta
# esplicitamente, in alcuni percorsi lo tratta come infinito (nessun timeout),
# quindi una richiesta bloccata in rete resterebbe appesa a tempo indeterminato
# senza che il retry qui sotto scatti mai (non c'è nessun errore da intercettare).
# Il valore viene inviato a Google come header X-Server-Timeout: se troppo basso,
# annulla lato server anche richieste lente ma che sarebbero riuscite (osservato con
# generazioni corpose come il copione reel, errore 499 CANCELLED) — 45s è un
# compromesso tra "non restare mai bloccati" e "non tagliare richieste legittime".
_TIMEOUT_MS_PER_TENTATIVO = 45_000


def crea_client(config: Config) -> genai.Client:
    return genai.Client(
        api_key=config.google_api_key,
        http_options=types.HttpOptions(timeout=_TIMEOUT_MS_PER_TENTATIVO),
    )


def _classifica_errore(e: Exception) -> tuple[bool, str]:
    """Ritorna (ritentabile, descrizione) per un errore incontrato in una chiamata Gemini."""
    if isinstance(e, genai_errors.APIError):
        codice = getattr(e, "code", None)
        return codice in _CODICI_RITENTABILI, f"errore {codice} ({e})"
    # Timeout/errore di connessione: nessun codice HTTP, ma è per definizione transitorio.
    return True, f"problema di rete ({e})"


def chiama_con_retry(config: Config, costruisci_chiamata: Callable[[str], object], modelli: list[str]):
    """Esegue `costruisci_chiamata(modello)` provando i modelli in `modelli` in
    cascata: se uno è sovraccarico/irraggiungibile, passa subito al successivo
    invece di aspettare in backoff su un modello che sta rispondendo male. Solo
    se TUTTI i modelli falliscono in un giro si aspetta prima di ripartire dal
    primo — fino a `config.retry_massimi` giri.
    """
    if not modelli:
        raise ValueError("Nessun modello configurato: aggiungine almeno uno in Impostazioni.")

    ultimo_errore: Exception | None = None
    for giro in range(config.retry_massimi + 1):
        for modello in modelli:
            try:
                return costruisci_chiamata(modello)
            except (genai_errors.APIError, httpx.TimeoutException, httpx.ConnectError) as e:
                ritentabile, descrizione = _classifica_errore(e)
                if not ritentabile:
                    raise
                ultimo_errore = e
                print(f"  [Gemini] {modello}: {descrizione}, provo il prossimo modello...", file=sys.stderr)

        if giro == config.retry_massimi:
            raise ultimo_errore

        attesa = config.attesa_iniziale_secondi * (2**giro)
        print(
            f"  [Gemini] tutti i modelli configurati hanno fallito, nuovo giro "
            f"{giro + 1}/{config.retry_massimi} tra {attesa:.0f}s...",
            file=sys.stderr,
        )
        time.sleep(attesa)
    raise ultimo_errore  # pragma: no cover - non raggiungibile
