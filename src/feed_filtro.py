"""Filtro locale (senza Gemini) per le notizie dei feed generalisti.

Tra le testate ci sono quotidiani e portali il cui feed mescola lo spazio con
politica, sport e cronaca: pubblicano molto più spesso delle testate spaziali e,
ordinando per data, riempirebbero l'elenco. Per ogni feed si guarda quante
notizie contengono parole legate allo spazio: se sono poche il feed è
generalista, e se ne tengono solo quelle che le contengono. I feed specializzati
passano interi (non tutti i loro titoli nominano lo spazio esplicitamente).

Il filtro è volutamente largo: la verifica fine la fa Gemini insieme alla
traduzione dei titoli (src/feed_traduzione.py).
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import VoceFeed

# Radici di parole, confrontate come sottostringhe senza distinguere maiuscole:
# "astronom" copre astronomy, astronomia, astronome, Astronomie. Evitate le radici
# troppo corte che pescano altro: "univers" (university), "launch" (lanci di
# prodotti), "cosm" (cosmetici), "meteor" (meteorologia).
_RADICI = [
    # inglese, italiano, francese, tedesco, spagnolo
    "space", "spazio", "spaziale", "spaziali", "espace", "spatial", "weltraum", "raumfahrt", "espacial",
    "cosmo", "cosmic", "cosmolog", "kosmos", "kosmonaut",
    "nasa", "esa ", "esa's", "jaxa", "isro", "cnsa", "roscosmos", "spacex", "blue origin", "rocket lab",
    "rocket", "razzo", "razzi", "fusée", "rakete", "cohete", "lanciatore", "lanciatori",
    "satellit", "orbit", "astronaut", "cosmonaut", "taikonaut", "starship", "falcon 9", "falcon heavy",
    "artemis", "ariane", "vega-c", "soyuz", "iss ", "stazione spaziale", "space station", "tiangong", "starlink",
    "astronom", "astrofisic", "astrophys", "telescop", "hubble", "james webb", "jwst",
    "galax", "galass", "nebul", "supernov", "black hole", "buco nero", "buchi neri", "trou noir",
    "exoplanet", "esopianet", "pianet", "planet", "asteroid", "comet", "meteorit", "meteor shower",
    "mars", "marte", "martian", "lunar", "moon", "venus", "venere", "jupiter", "giove",
    "saturn", "neptun", "nettuno", "uranus", "urano", "pluto", "solar system", "sistema solare",
    "solar wind", "vento solare", "solar flare", "brillament", "stellar", "stelle", "étoile",
    "universe", "universo", "l'univers", "big bang", "dark matter", "materia oscura",
    "gravitational wave", "onde gravitazional", "aurora", "eclipse", "eclissi",
    # russo
    "космос", "космич", "ракет", "спутник", "орбит", "астроном", "луна", "марс", "роскосмос", "мкс",
    # cinese e giapponese
    "宇宙", "航天", "火箭", "卫星", "探测", "天文", "月球", "火星", "太空", "衛星", "ロケット", "探査", "天体",
    # coreano
    "우주", "로켓", "위성", "발사체", "천문", "달 탐사", "누리호",
]
_ESPRESSIONE = re.compile("|".join(re.escape(r) for r in _RADICI), re.IGNORECASE)

QUOTA_MINIMA_SPECIALIZZATO = 0.4  # sotto questa quota di notizie "spaziali" il feed è generalista
NOTIZIE_MINIME_PER_GIUDICARE = 2


def parla_di_spazio(voce: VoceFeed) -> bool:
    return bool(_ESPRESSIONE.search(f" {voce.titolo} {voce.riassunto_grezzo} "))


def filtra_generalisti(voci: list[VoceFeed]) -> list[VoceFeed]:
    """Mantiene l'ordine; toglie dai feed generalisti le notizie non spaziali."""
    per_feed: dict[str, list[VoceFeed]] = defaultdict(list)
    for voce in voci:
        per_feed[voce.feed].append(voce)

    generalisti = set()
    for feed, notizie in per_feed.items():
        if len(notizie) < NOTIZIE_MINIME_PER_GIUDICARE:
            continue
        quota = sum(parla_di_spazio(v) for v in notizie) / len(notizie)
        if quota < QUOTA_MINIMA_SPECIALIZZATO:
            generalisti.add(feed)

    return [v for v in voci if v.feed not in generalisti or parla_di_spazio(v)]
