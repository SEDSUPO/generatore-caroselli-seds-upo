"""Gestione di yt-dlp: download da YouTube, versione installata/ultima disponibile,
aggiornamento (manuale o automatico).

yt-dlp si usa sempre come sottoprocesso (`python -m yt_dlp`), mai importato:
così un aggiornamento fatto dall'app ha effetto subito, senza riavviare il server
(un modulo già importato resterebbe alla vecchia versione in memoria). E YouTube
cambia spesso: una versione vecchia di qualche settimana è la causa più comune
di download che falliscono, per questo in automatico si aggiorna e si riprova.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import imageio_ffmpeg

from .config import ROOT_DIR

_FLAG_NASCONDI_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FILE_STATO = ROOT_DIR / ".stato" / "ytdlp.json"
_INTERVALLO_CONTROLLO_SECONDI = 24 * 3600


class ErroreYtDlp(RuntimeError):
    pass


@dataclass
class VideoScaricato:
    percorso: Path
    titolo: str
    autore: str | None
    licenza: str | None
    url: str
    durata: float | None


def _esegui(argomenti: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *argomenti],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_FLAG_NASCONDI_CONSOLE,
    )


def versione_installata() -> str | None:
    try:
        risultato = _esegui(["-m", "yt_dlp", "--version"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if risultato.returncode != 0:
        return None
    return risultato.stdout.strip() or None


def stessa_versione(a: str | None, b: str | None) -> bool:
    """yt-dlp si presenta come '2026.08.19', PyPI la normalizza in '2026.8.19':
    si confrontano i numeri, non le stringhe."""
    if not a or not b:
        return False
    try:
        return [int(p) for p in a.split(".")] == [int(p) for p in b.split(".")]
    except ValueError:
        return a == b


def ultima_versione() -> str | None:
    try:
        risposta = httpx.get("https://pypi.org/pypi/yt-dlp/json", timeout=15)
        risposta.raise_for_status()
        return risposta.json()["info"]["version"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def aggiorna() -> tuple[bool, str]:
    """Aggiorna yt-dlp con pip. Ritorna (riuscito, messaggio)."""
    prima = versione_installata()
    try:
        risultato = _esegui(["-m", "pip", "install", "--upgrade", "yt-dlp"], timeout=300)
    except subprocess.TimeoutExpired:
        return False, "Aggiornamento interrotto: pip non ha risposto entro 5 minuti."
    _salva_stato({"ultimo_controllo": time.time()})
    if risultato.returncode != 0:
        return False, f"Aggiornamento fallito: {risultato.stderr.strip()[-500:]}"
    dopo = versione_installata()
    if prima == dopo:
        return True, f"yt-dlp è già all'ultima versione ({dopo})."
    return True, f"yt-dlp aggiornato: {prima} → {dopo}."


def _leggi_stato() -> dict:
    try:
        return json.loads(_FILE_STATO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _salva_stato(stato: dict) -> None:
    _FILE_STATO.parent.mkdir(parents=True, exist_ok=True)
    _FILE_STATO.write_text(json.dumps(stato), encoding="utf-8")


def aggiorna_se_necessario() -> str | None:
    """Controlla al massimo una volta al giorno se c'è una versione nuova e in quel
    caso aggiorna. Ritorna un messaggio solo se ha aggiornato davvero."""
    stato = _leggi_stato()
    if time.time() - stato.get("ultimo_controllo", 0) < _INTERVALLO_CONTROLLO_SECONDI:
        return None
    _salva_stato({"ultimo_controllo": time.time()})
    installata, ultima = versione_installata(), ultima_versione()
    if not ultima or stessa_versione(installata, ultima):
        return None
    riuscito, messaggio = aggiorna()
    return messaggio if riuscito else None


def scarica_video(url: str, cartella: Path, nome_base: str, durata_massima_minuti: int, aggiornamento_automatico: bool) -> VideoScaricato:
    """Scarica solo la traccia video (fino a 1080p): l'audio originale non serve,
    nel reel c'è la voce registrata. Se fallisce e l'aggiornamento automatico è
    attivo, aggiorna yt-dlp e riprova una volta."""
    if aggiornamento_automatico:
        aggiorna_se_necessario()

    try:
        return _scarica(url, cartella, nome_base, durata_massima_minuti)
    except ErroreYtDlp as primo_errore:
        if not aggiornamento_automatico or "durata massima" in str(primo_errore):
            raise
        riuscito, _ = aggiorna()
        if not riuscito:
            raise
        return _scarica(url, cartella, nome_base, durata_massima_minuti)


def _scarica(url: str, cartella: Path, nome_base: str, durata_massima_minuti: int) -> VideoScaricato:
    cartella.mkdir(parents=True, exist_ok=True)
    separatore = "\t"
    risultato = _esegui(
        [
            "-m", "yt_dlp",
            "--no-playlist",
            "--no-progress",
            "--match-filter", f"duration <= {durata_massima_minuti * 60}",
            "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]",
            "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
            "-o", str(cartella / f"{nome_base}.%(ext)s"),
            "--print", separatore.join(
                ["after_move:%(filepath)s", "%(title)s", "%(uploader)s", "%(license)s", "%(webpage_url)s", "%(duration)s"]
            ),
            url,
        ],
        timeout=1800,
    )

    righe = [r for r in risultato.stdout.splitlines() if r.strip()]
    if risultato.returncode != 0 or not righe:
        dettaglio = risultato.stderr.strip()[-600:]
        if "does not pass filter" in risultato.stdout + risultato.stderr or (risultato.returncode == 0 and not righe):
            raise ErroreYtDlp(f"Video scartato: supera la durata massima di {durata_massima_minuti} minuti.")
        raise ErroreYtDlp(f"Download non riuscito: {dettaglio or 'errore sconosciuto di yt-dlp'}")

    percorso, titolo, autore, licenza, url_pagina, durata = (righe[-1].split(separatore) + [""] * 6)[:6]
    vuoto = {"", "NA", "None"}
    return VideoScaricato(
        percorso=Path(percorso),
        titolo=titolo or "Video YouTube",
        autore=None if autore in vuoto else autore,
        licenza=None if licenza in vuoto else licenza,
        url=url_pagina or url,
        durata=float(durata) if durata not in vuoto else None,
    )
