"""Trascrizione delle registrazioni con Whisper in locale (faster-whisper), con i
tempi di ogni parola: servono per i sottotitoli animati sincronizzati alla voce.

Si trascrive quello che è stato detto davvero, non il copione: se chi registra
cambia una parola, i sottotitoli seguono la voce. Il testo del copione viene
passato come `initial_prompt` solo per aiutare Whisper con i termini tecnici.

Il modello si scarica una volta sola in modelli/whisper/ (centinaia di MB per
"small") e resta caricato in memoria nel processo per le chiamate successive.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .config import ROOT_DIR
from .models import ParolaTrascritta

CARTELLA_MODELLI = ROOT_DIR / "modelli" / "whisper"

_modelli: dict[str, object] = {}
_lock = threading.Lock()


def carica_modello(nome_modello: str):
    """Carica (e alla prima volta scarica) il modello. Thread-safe: due richieste
    contemporanee non scaricano lo stesso modello due volte."""
    with _lock:
        if nome_modello not in _modelli:
            from faster_whisper import WhisperModel  # import pesante: solo quando serve

            CARTELLA_MODELLI.mkdir(parents=True, exist_ok=True)
            _modelli[nome_modello] = WhisperModel(
                nome_modello, device="cpu", compute_type="int8", download_root=str(CARTELLA_MODELLI)
            )
        return _modelli[nome_modello]


def trascrivi_parole(percorso_audio: Path, nome_modello: str, testo_atteso: str = "") -> list[ParolaTrascritta]:
    modello = carica_modello(nome_modello)
    segmenti, _ = modello.transcribe(
        str(percorso_audio),
        language="it",
        word_timestamps=True,
        initial_prompt=testo_atteso or None,
        vad_filter=False,  # l'audio è già ripulito dai silenzi: il VAD rischierebbe di tagliare parole
        beam_size=5,
    )
    parole: list[ParolaTrascritta] = []
    for segmento in segmenti:
        for parola in segmento.words or []:
            testo = parola.word.strip()
            if testo:
                parole.append(ParolaTrascritta(testo=testo, inizio=parola.start, fine=parola.end))
    return parole


MODELLI_DISPONIBILI = {
    "base": "veloce, meno preciso (~150 MB)",
    "small": "equilibrato, consigliato (~500 MB)",
    "medium": "più preciso, lento su CPU (~1,5 GB)",
    "large-v3-turbo": "il più preciso, molto lento su CPU (~1,6 GB)",
}


def modello_scaricato(nome_modello: str) -> bool:
    """Il download di Hugging Face finisce in models--<autore>--faster-whisper-<nome>."""
    from faster_whisper.utils import _MODELS  # noqa: PLC0415 - import pesante solo quando serve

    repo = _MODELS.get(nome_modello)
    if repo is None:
        return False
    cartella = CARTELLA_MODELLI / f"models--{repo.replace('/', '--')}" / "snapshots"
    return cartella.is_dir() and any(cartella.glob("*/model.bin"))
