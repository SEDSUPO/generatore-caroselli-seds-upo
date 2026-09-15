"""Caricamento configurazione: .env (credenziali) + config.yaml (parametri)."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
LOGO_PATH = ASSETS_DIR / "logo.png"
INPUT_IMMAGINI_DIR = ROOT_DIR / "input_immagini"
OUTPUT_DIR = ROOT_DIR / "output"
PROMPTS_DIR = ROOT_DIR / "prompts"
ENV_PATH = ROOT_DIR / ".env"
CONFIG_YAML_PATH = ROOT_DIR / "config.yaml"
# Scritto da esporta_app.py nello ZIP (e aggiornato da aggiorna_app.py): nella copia
# di sviluppo non esiste.
VERSIONE_PATH = ROOT_DIR / "versione.txt"
VERSIONE_APP = VERSIONE_PATH.read_text(encoding="utf-8").strip() if VERSIONE_PATH.is_file() else "sviluppo"

# Carosello notizie: struttura a parte, parallela a quella sopra ma per un
# tipo di carosello diverso (foto reali da URL, non illustrazioni generate).
NOTIZIE_DIR = ROOT_DIR / "notizie"
INPUT_IMMAGINI_NOTIZIE_DIR = ROOT_DIR / "input_immagini_notizie"
OUTPUT_NOTIZIE_DIR = ROOT_DIR / "output_notizie"

# Creatore Reel: tutto ciò che riguarda un reel (copione, registrazioni, media,
# video montato) sta sotto reel/<nome>/, in sottocartelle audio/ media/ output/.
REEL_DIR = ROOT_DIR / "reel"
# Feed RSS delle testate spaziali: file separato perché lo riscrive l'app (un dump
# YAML completo di config.yaml ne perderebbe i commenti).
FEEDS_YAML_PATH = ROOT_DIR / "feeds.yaml"

# Richieste ai siti delle testate (feed, pagine): un User-Agent da browser, perché
# diversi siti rifiutano i client che si presentano come script.
INTESTAZIONI_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "it,en;q=0.9",
}
TIMEOUT_HTTP_SECONDI = 15

FONT_VARIABLE_PATH = FONTS_DIR / "SpaceGrotesk-Variable.ttf"


@dataclass(frozen=True)
class Config:
    google_api_key: str
    soglia_qualita: float
    max_caratteri_slide: int
    max_caratteri_caption: int
    max_caratteri_notizia: int
    min_slide: int
    max_slide: int
    modelli_testo: list[str]
    modelli_vision: list[str]
    modelli_tts: list[str]
    retry_massimi: int
    attesa_iniziale_secondi: float
    whisper_modello: str
    ytdlp_aggiornamento_automatico: bool
    youtube_durata_massima_minuti: int
    max_notizie_per_feed: int
    max_notizie_totali: int
    traduci_titoli_feed: bool


def _leggi_api_key() -> str:
    # override=True: se la chiave è stata appena salvata da UI, il prossimo
    # caricamento deve rileggerla dal file e non da una versione già in cache
    # nel processo (os.environ).
    load_dotenv(ENV_PATH, override=True)
    return os.environ.get("GOOGLE_API_KEY", "").strip()


def _normalizza_lista_modelli(valore, default: list[str]) -> list[str]:
    if isinstance(valore, list) and valore:
        return [str(m) for m in valore]
    if isinstance(valore, str) and valore.strip():
        return [valore.strip()]  # config.yaml pre-cascata: una stringa sola
    return list(default)


def _costruisci_config(api_key: str) -> Config:
    with open(CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    default_modelli = ["gemini-3.5-flash-lite"]
    return Config(
        google_api_key=api_key,
        soglia_qualita=float(raw.get("soglia_qualita", 7.0)),
        max_caratteri_slide=int(raw.get("max_caratteri_slide", 280)),
        max_caratteri_caption=int(raw.get("max_caratteri_caption", 1600)),
        max_caratteri_notizia=int(raw.get("max_caratteri_notizia", 90)),
        min_slide=int(raw.get("min_slide", 5)),
        max_slide=int(raw.get("max_slide", 10)),
        modelli_testo=_normalizza_lista_modelli(raw.get("modelli_testo"), default_modelli),
        modelli_vision=_normalizza_lista_modelli(raw.get("modelli_vision"), default_modelli),
        modelli_tts=_normalizza_lista_modelli(
            raw.get("modelli_tts"), ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
        ),
        retry_massimi=int(raw.get("retry_massimi", 2)),
        attesa_iniziale_secondi=float(raw.get("attesa_iniziale_secondi", 2)),
        whisper_modello=str(raw.get("whisper_modello", "small")),
        ytdlp_aggiornamento_automatico=bool(raw.get("ytdlp_aggiornamento_automatico", True)),
        youtube_durata_massima_minuti=int(raw.get("youtube_durata_massima_minuti", 30)),
        max_notizie_per_feed=int(raw.get("max_notizie_per_feed", 8)),
        max_notizie_totali=int(raw.get("max_notizie_totali", 150)),
        traduci_titoli_feed=bool(raw.get("traduci_titoli_feed", True)),
    )


def carica_config() -> Config:
    """Per uso da CLI: termina il processo con un messaggio chiaro se la chiave manca."""
    api_key = _leggi_api_key()
    if not api_key:
        print(
            "ERRORE: GOOGLE_API_KEY non impostata.\n"
            "Crea un file .env nella root del progetto (vedi .env.example) con:\n"
            "  GOOGLE_API_KEY=la-tua-chiave\n"
            "Ottienila gratuitamente su https://ai.google.dev",
            file=sys.stderr,
        )
        sys.exit(1)
    return _costruisci_config(api_key)


def carica_config_opzionale() -> Config | None:
    """Per uso dalla web app: ritorna None se la chiave manca, senza terminare il processo."""
    api_key = _leggi_api_key()
    if not api_key:
        return None
    return _costruisci_config(api_key)


def aggiorna_lista_modelli_yaml(chiave: str, modelli: list[str]) -> None:
    aggiorna_valore_yaml(chiave, "[" + ", ".join(modelli) + "]")


def aggiorna_valore_yaml(chiave: str, valore_yaml: str) -> None:
    """Riscrive SOLO la riga `chiave: valore` in config.yaml, lasciando intatto
    il resto del file (commenti inclusi) — non un dump YAML completo, che
    perderebbe la formattazione e i commenti esistenti. `valore_yaml` è già
    scritto in sintassi YAML (es. "true", "small", "[a, b]").
    """
    testo = CONFIG_YAML_PATH.read_text(encoding="utf-8")
    nuova_riga = f"{chiave}: {valore_yaml}"

    pattern = re.compile(rf"^{re.escape(chiave)}:.*$", re.MULTILINE)
    if pattern.search(testo):
        testo = pattern.sub(nuova_riga, testo, count=1)
    else:
        testo = testo.rstrip("\n") + f"\n{nuova_riga}\n"

    CONFIG_YAML_PATH.write_text(testo, encoding="utf-8")


# ---------------------------------------------------------------------------
# Feed RSS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Feed:
    nome: str
    url: str


def carica_feeds() -> list[Feed]:
    if not FEEDS_YAML_PATH.is_file():
        return []
    raw = yaml.safe_load(FEEDS_YAML_PATH.read_text(encoding="utf-8")) or {}
    feeds = []
    for voce in raw.get("feeds") or []:
        if isinstance(voce, dict) and voce.get("url"):
            feeds.append(Feed(nome=str(voce.get("nome") or voce["url"]), url=str(voce["url"])))
    return feeds


def salva_feeds(feeds: list[Feed]) -> None:
    dati = {"feeds": [{"nome": f.nome, "url": f.url} for f in feeds]}
    FEEDS_YAML_PATH.write_text(
        "# Feed RSS da cui l'app pesca le notizie spaziali.\n"
        "# Gestito dall'interfaccia (Impostazioni) o con importa_feed.py; modificabile anche a mano.\n"
        + yaml.safe_dump(dati, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
