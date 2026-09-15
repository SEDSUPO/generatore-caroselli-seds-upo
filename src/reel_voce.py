"""Voce generata per i reel, in alternativa alla registrazione.

Due motori:
- Kokoro (kokoro-onnx): gratuito, gira sul computer, nessuna quota. Il modello
  (~120 MB) si scarica una volta sola in modelli/kokoro/.
- Gemini TTS: più espressivo (segue istruzioni sul tono), ma usa la quota della
  API key, che per i modelli vocali in anteprima è stretta.

Il risultato è un wav "grezzo": la pulizia (taglio silenzi, formato) e il
salvataggio come ripresa li fa reel_produzione.aggiungi_take, come per le
registrazioni.
"""

from __future__ import annotations

import hashlib
import re
import threading
import wave
from pathlib import Path
from typing import Callable

import httpx
from google.genai import types

from .config import ROOT_DIR, Config
from .gemini_client import chiama_con_retry, crea_client

CARTELLA_KOKORO = ROOT_DIR / "modelli" / "kokoro"
CARTELLA_ANTEPRIME = ROOT_DIR / ".stato" / "anteprime_voce"
_URL_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_FILE_KOKORO = ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]

MOTORI = {
    "kokoro": "Kokoro (gratuita, locale)",
    "gemini": "Gemini (più espressiva, usa quota)",
}
VOCI = {
    "kokoro": {"if_sara": "Sara", "im_nicola": "Nicola"},
    # Descrizioni tradotte da quelle ufficiali di Google: il timbro si sente meglio
    # con l'anteprima.
    "gemini": {
        "Kore": "Kore — decisa",
        "Charon": "Charon — informativa",
        "Puck": "Puck — vivace",
        "Fenrir": "Fenrir — entusiasta",
        "Aoede": "Aoede — ariosa",
        "Leda": "Leda — giovanile",
        "Orus": "Orus — decisa",
        "Zephyr": "Zephyr — luminosa",
        "Sadaltager": "Sadaltager — competente",
        "Achird": "Achird — amichevole",
        "Sulafat": "Sulafat — calda",
        "Iapetus": "Iapetus — chiara",
    },
}
VELOCITA_MINIMA, VELOCITA_MASSIMA = 0.8, 1.3
TESTO_ANTEPRIMA = "Ciao! Questa è la voce che userò per i reel di divulgazione spaziale di SEDS UPO."

SuAvanzamento = Callable[[str, float], None]


class ErroreVoce(RuntimeError):
    pass


def etichetta_voce(motore: str, voce: str) -> str:
    nome = VOCI.get(motore, {}).get(voce, voce).split(" — ")[0]
    return f"{'Kokoro' if motore == 'kokoro' else 'Gemini'} · {nome}"


def valida(motore: str, voce: str, velocita: float) -> None:
    if motore not in VOCI:
        raise ErroreVoce("Motore vocale non valido.")
    if voce not in VOCI[motore]:
        raise ErroreVoce("Voce non valida per il motore scelto.")
    if not VELOCITA_MINIMA <= velocita <= VELOCITA_MASSIMA:
        raise ErroreVoce(f"La velocità deve essere tra {VELOCITA_MINIMA} e {VELOCITA_MASSIMA}.")


# ---------------------------------------------------------------------------
# Kokoro (locale)
# ---------------------------------------------------------------------------

_kokoro = None
_lock_kokoro = threading.Lock()


def kokoro_scaricato() -> bool:
    return all((CARTELLA_KOKORO / nome).is_file() for nome in _FILE_KOKORO)


def _scarica_kokoro(su_avanzamento: SuAvanzamento | None) -> None:
    CARTELLA_KOKORO.mkdir(parents=True, exist_ok=True)
    for indice, nome in enumerate(_FILE_KOKORO):
        destinazione = CARTELLA_KOKORO / nome
        if destinazione.is_file():
            continue
        parziale = destinazione.with_suffix(destinazione.suffix + ".part")
        try:
            with httpx.stream("GET", _URL_KOKORO + nome, follow_redirects=True, timeout=60) as risposta:
                risposta.raise_for_status()
                totale = int(risposta.headers.get("content-length") or 0)
                scaricati = 0
                with open(parziale, "wb") as f:
                    for blocco in risposta.iter_bytes(1 << 20):
                        f.write(blocco)
                        scaricati += len(blocco)
                        if su_avanzamento and totale:
                            quota = (indice + scaricati / totale) / len(_FILE_KOKORO)
                            su_avanzamento(f"Download della voce gratuita ({scaricati >> 20}/{totale >> 20} MB)", quota)
        except httpx.HTTPError as e:
            parziale.unlink(missing_ok=True)
            raise ErroreVoce(f"Download del modello Kokoro non riuscito: {e}") from e
        parziale.replace(destinazione)


def carica_kokoro(su_avanzamento: SuAvanzamento | None = None):
    """Scarica (la prima volta) e carica il modello. Thread-safe e in cache."""
    global _kokoro
    with _lock_kokoro:
        if _kokoro is None:
            _scarica_kokoro(su_avanzamento)
            from kokoro_onnx import Kokoro  # import pesante: solo quando serve

            _kokoro = Kokoro(str(CARTELLA_KOKORO / _FILE_KOKORO[0]), str(CARTELLA_KOKORO / _FILE_KOKORO[1]))
        return _kokoro


def _scrivi_wav(percorso: Path, pcm16: bytes, frequenza: int) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(percorso), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(frequenza)
        f.writeframes(pcm16)


def _genera_kokoro(testo: str, voce: str, velocita: float, destinazione: Path, su_avanzamento: SuAvanzamento | None) -> None:
    import numpy as np

    kokoro = carica_kokoro(su_avanzamento)
    campioni, frequenza = kokoro.create(testo, voice=voce, speed=velocita, lang="it")
    pcm16 = (np.clip(campioni, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    _scrivi_wav(destinazione, pcm16, frequenza)


# ---------------------------------------------------------------------------
# Gemini TTS (online)
# ---------------------------------------------------------------------------


def _genera_gemini(config: Config, testo: str, voce: str, velocita: float, istruzioni: str, destinazione: Path) -> None:
    client = crea_client(config)
    # Gemini TTS non ha un parametro di velocità: si chiede a parole, come il tono.
    ritmo = ""
    if velocita >= 1.1:
        ritmo = ", a ritmo veloce"
    elif velocita <= 0.9:
        ritmo = ", a ritmo lento e scandito"
    prompt = f"{(istruzioni.strip() or 'Leggi con tono naturale').rstrip(':.')}{ritmo}:\n{testo}"
    configurazione = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voce))
        ),
    )

    def _chiamata(modello: str):
        return client.models.generate_content(model=modello, contents=prompt, config=configurazione)

    risposta = chiama_con_retry(config, _chiamata, config.modelli_tts)
    parti = (risposta.candidates or [None])[0]
    parti = parti.content.parts if parti and parti.content else []
    audio = next((p.inline_data for p in parti if p.inline_data and p.inline_data.data), None)
    if audio is None:
        raise ErroreVoce("Gemini non ha restituito audio: riprova o usa la voce gratuita.")
    frequenza = re.search(r"rate=(\d+)", audio.mime_type or "")
    _scrivi_wav(destinazione, audio.data, int(frequenza.group(1)) if frequenza else 24000)


# ---------------------------------------------------------------------------
# API comune
# ---------------------------------------------------------------------------


def genera(config: Config | None, testo: str, motore: str, voce: str, velocita: float, istruzioni: str,
           destinazione: Path, su_avanzamento: SuAvanzamento | None = None) -> Path:
    testo = testo.strip()
    if not testo:
        raise ErroreVoce("Il testo da leggere è vuoto.")
    valida(motore, voce, velocita)
    if motore == "kokoro":
        _genera_kokoro(testo, voce, velocita, destinazione, su_avanzamento)
    else:
        if config is None:
            raise ErroreVoce("API key non configurata.")
        _genera_gemini(config, testo, voce, velocita, istruzioni, destinazione)
    return destinazione


def anteprima(config: Config | None, motore: str, voce: str, velocita: float, istruzioni: str,
              su_avanzamento: SuAvanzamento | None = None) -> Path:
    """Frase di esempio con la voce scelta, in cache: riascoltarla non rigenera
    (per Gemini non consuma quota una seconda volta)."""
    valida(motore, voce, velocita)
    impronta = hashlib.sha1(f"{motore}|{voce}|{velocita:.2f}|{istruzioni if motore == 'gemini' else ''}".encode()).hexdigest()[:12]
    destinazione = CARTELLA_ANTEPRIME / f"{impronta}.wav"
    if not destinazione.is_file():
        temporaneo = destinazione.with_suffix(".tmp.wav")
        genera(config, TESTO_ANTEPRIMA, motore, voce, velocita, istruzioni, temporaneo, su_avanzamento)
        temporaneo.replace(destinazione)
    return destinazione
