"""Ultime notizie spaziali dai feed RSS configurati in feeds.yaml: lettura in
parallelo, ordinamento per data, traduzione dei titoli e filtro di pertinenza.

Il risultato resta in memoria per qualche minuto: lo usano sia il carosello notizie
sia i suggerimenti di argomento per i reel, e rileggere un centinaio di feed a ogni
clic farebbe aspettare e sprecherebbe una chiamata Gemini di traduzione.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .config import Config, carica_feeds
from .feed_filtro import filtra_generalisti
from .feed_rss import leggi_tutti_i_feed
from .feed_traduzione import traduci_titoli
from .models import VoceFeed

DURATA_CACHE_SECONDI = 15 * 60


@dataclass
class ElencoNotizie:
    voci: list[VoceFeed]
    errori: list[str]
    totale_lette: int
    numero_feed: int
    letto_il: float = field(default_factory=time.time)


_cache: ElencoNotizie | None = None
_lock = threading.Lock()


def notizie_in_cache() -> ElencoNotizie | None:
    if _cache and time.time() - _cache.letto_il < DURATA_CACHE_SECONDI:
        return _cache
    return None


def ultime_notizie(config: Config, forza: bool = False) -> ElencoNotizie:
    """Legge (o ripesca dalla cache) le notizie più recenti di tutti i feed."""
    global _cache
    with _lock:
        if not forza and notizie_in_cache():
            return _cache

        feeds = carica_feeds()
        if not feeds:
            raise ValueError("Nessun feed configurato: aggiungi i siti delle testate in Impostazioni.")
        voci, errori = leggi_tutti_i_feed(feeds, config.max_notizie_per_feed)
        mostrate = filtra_generalisti(voci)[: config.max_notizie_totali]

        if config.traduci_titoli_feed and mostrate:
            for voce, (tradotto, pertinente) in zip(mostrate, traduci_titoli(config, [v.titolo for v in mostrate])):
                if tradotto != voce.titolo:
                    voce.titolo_tradotto = tradotto
                voce.pertinente = pertinente

        _cache = ElencoNotizie(voci=mostrate, errori=errori, totale_lette=len(voci), numero_feed=len(feeds))
        return _cache


def svuota_cache() -> None:
    """Dopo aver cambiato l'elenco dei feed."""
    global _cache
    with _lock:
        _cache = None
