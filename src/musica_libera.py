"""Musica di sottofondo con licenza libera da Openverse (api.openverse.org), il
motore di ricerca di contenuti Creative Commons: raccoglie soprattutto brani di
Jamendo, oltre a ccMixter, Freesound e Wikimedia. Nessuna chiave richiesta.

La libreria audio di YouTube non ha un'API: si usa solo da YouTube Studio con il
proprio account Google, per questo qui non c'è.

Di default si escludono le licenze ND ("no derivative works"): nel reel il brano
viene tagliato, ripetuto e abbassato sotto la voce, cioè modificato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

API = "https://api.openverse.org/v1/audio/"
_HEADERS = {"User-Agent": "GeneratoreCaroselliSEDS/1.0 (uso personale; progetto divulgativo SEDS UPO)"}
_ID_VALIDO = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DIMENSIONE_MASSIMA = 40 * 1024 * 1024


class ErroreMusica(RuntimeError):
    pass


@dataclass
class Brano:
    id: str
    titolo: str
    autore: str | None
    licenza: str  # leggibile, es. "CC BY-SA 3.0"
    licenza_nd: bool
    licenza_nc: bool
    durata: float | None
    url_audio: str
    pagina: str | None
    fonte: str
    attribuzione: str | None


def _licenza_leggibile(dati: dict) -> str:
    codice = (dati.get("license") or "").lower()
    versione = dati.get("license_version") or ""
    if codice in ("cc0", "pdm"):
        return "CC0" if codice == "cc0" else "Pubblico dominio"
    return f"CC {codice.upper()} {versione}".strip()


def _brano(dati: dict) -> Brano:
    codice = (dati.get("license") or "").lower()
    durata = dati.get("duration")
    return Brano(
        id=dati["id"],
        titolo=(dati.get("title") or "Senza titolo").strip(),
        autore=dati.get("creator"),
        licenza=_licenza_leggibile(dati),
        licenza_nd="nd" in codice.split("-"),
        licenza_nc="nc" in codice.split("-"),
        durata=durata / 1000 if durata else None,
        url_audio=dati.get("url") or "",
        pagina=dati.get("foreign_landing_url"),
        fonte=dati.get("source") or dati.get("provider") or "openverse",
        attribuzione=dati.get("attribution"),
    )


def cerca_musica(query: str, includi_nd: bool = False, limite: int = 20) -> list[Brano]:
    parametri = {
        "q": query,
        "category": "music",
        "length": "short,medium",  # da 30 secondi a 10 minuti: basta per un reel senza ripetizioni
        "page_size": limite,
    }
    if not includi_nd:
        parametri["license_type"] = "modification"
    try:
        risposta = httpx.get(API, params=parametri, headers=_HEADERS, timeout=30, follow_redirects=True)
        risposta.raise_for_status()
    except httpx.HTTPError as e:
        raise ErroreMusica(f"Openverse non risponde: {e}") from e
    return [_brano(d) for d in risposta.json().get("results", []) if d.get("url")]


def scarica_brano(id_brano: str, cartella: Path, nome_base: str) -> tuple[Path, Brano]:
    """Scarica un brano scelto. I dati (URL compreso) si rileggono da Openverse a
    partire dall'id: il server non scarica mai un URL arrivato dal browser."""
    if not _ID_VALIDO.match(id_brano):
        raise ErroreMusica("Brano non valido.")
    try:
        dettaglio = httpx.get(f"{API}{id_brano}/", headers=_HEADERS, timeout=30, follow_redirects=True)
        dettaglio.raise_for_status()
        brano = _brano(dettaglio.json())
        if not brano.url_audio.startswith("https://"):
            raise ErroreMusica("Il brano non ha un file audio scaricabile.")

        cartella.mkdir(parents=True, exist_ok=True)
        destinazione = cartella / f"{nome_base}.mp3"
        scaricati = 0
        with httpx.stream("GET", brano.url_audio, headers=_HEADERS, timeout=60, follow_redirects=True) as risposta:
            risposta.raise_for_status()
            with open(destinazione, "wb") as f:
                for blocco in risposta.iter_bytes(1 << 16):
                    scaricati += len(blocco)
                    if scaricati > _DIMENSIONE_MASSIMA:
                        raise ErroreMusica("File audio troppo grande.")
                    f.write(blocco)
    except httpx.HTTPError as e:
        raise ErroreMusica(f"Download non riuscito: {e}") from e
    return destinazione, brano


def credito(titolo: str | None, autore: str | None, licenza: str | None) -> str:
    voce = f"\"{titolo}\"" if titolo else "Brano"
    if autore:
        voce += f" di {autore}"
    if licenza:
        voce += f" ({licenza})"
    return voce
