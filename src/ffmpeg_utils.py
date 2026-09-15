"""Utility ffmpeg condivise dalla produzione reel.

Il binario arriva dal pacchetto pip `imageio-ffmpeg` (niente installazioni
manuali). Non include ffprobe: le durate si leggono dall'output di `ffmpeg -i`
o, per i wav, direttamente con il modulo `wave`.
"""

from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path
from typing import Callable

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# Su Windows evita che ogni chiamata a ffmpeg apra una finestra di console.
_FLAG_NASCONDI_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErroreFFmpeg(RuntimeError):
    pass


def esegui(argomenti: list[str], durata_attesa: float | None = None, su_avanzamento: Callable[[float], None] | None = None,
           cwd: Path | None = None) -> None:
    """Esegue ffmpeg. Se `durata_attesa` e `su_avanzamento` sono indicati, chiama
    `su_avanzamento(frazione 0-1)` man mano che ffmpeg procede (da `-progress`).
    `cwd` serve per usare percorsi relativi nei filtri (es. `ass=sottotitoli.ass`):
    su Windows i percorsi assoluti con "C:" dentro un filtro richiedono un escape fragile.
    """
    comando = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-nostdin"]
    if su_avanzamento and durata_attesa:
        comando += ["-progress", "pipe:1", "-nostats"]
    comando += argomenti

    processo = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_FLAG_NASCONDI_CONSOLE,
        cwd=str(cwd) if cwd else None,
    )

    if su_avanzamento and durata_attesa:
        for riga in processo.stdout:
            if riga.startswith("out_time_us="):
                try:
                    secondi = int(riga.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                su_avanzamento(max(0.0, min(secondi / durata_attesa, 1.0)))

    _, errori = processo.communicate()
    if processo.returncode != 0:
        raise ErroreFFmpeg(errori.strip()[-2000:] or f"ffmpeg terminato con codice {processo.returncode}")


def durata_media(percorso: Path) -> float | None:
    """Durata in secondi di un file audio/video, letta dall'intestazione."""
    risultato = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(percorso)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_FLAG_NASCONDI_CONSOLE,
    )
    corrispondenza = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", risultato.stderr)
    if not corrispondenza:
        return None
    ore, minuti, secondi = corrispondenza.groups()
    return int(ore) * 3600 + int(minuti) * 60 + float(secondi)


def dimensioni_video(percorso: Path) -> tuple[int, int] | None:
    risultato = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(percorso)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_FLAG_NASCONDI_CONSOLE,
    )
    corrispondenza = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", risultato.stderr)
    if not corrispondenza:
        return None
    return int(corrispondenza.group(1)), int(corrispondenza.group(2))


def durata_wav(percorso: Path) -> float:
    with wave.open(str(percorso), "rb") as f:
        return f.getnframes() / f.getframerate()


# Taglio dei silenzi a inizio e fine: silenceremove agisce solo sull'inizio, quindi
# si applica, si rovescia l'audio, si riapplica e si rovescia di nuovo.
_TAGLIO_SILENZI = (
    "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
    "areverse"
)


def pulisci_voce(sorgente: Path, destinazione: Path, riduci_rumore: bool = True) -> float:
    """Converte una registrazione (qualunque formato) in wav 48 kHz mono ripulito:
    filtro passa-alto contro i rumori bassi, riduzione rumore di fondo, taglio dei
    silenzi iniziali/finali e un piccolo respiro prima e dopo la frase. Ritorna la durata.
    Per una voce sintetica (già pulita) la riduzione rumore si salta: non avrebbe
    niente da togliere e rovinerebbe solo un po' il timbro.
    """
    filtri = ",".join(
        (["highpass=f=80", "afftdn=nf=-25"] if riduci_rumore else [])
        + [
            _TAGLIO_SILENZI,
            "adelay=80:all=1",
            "apad=pad_dur=0.25",
        ]
    )
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    esegui(["-i", str(sorgente), "-af", filtri, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destinazione)])
    return durata_wav(destinazione)


def estrai_fotogramma(video: Path, secondo: float, destinazione: Path, larghezza: int = 480) -> None:
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    esegui(
        [
            "-ss", f"{max(secondo, 0):.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale={larghezza}:-2",
            "-q:v", "4",
            str(destinazione),
        ]
    )
