"""Libreria media di un reel: immagini e video caricati a mano, trovati su NASA
Image and Video Library o Wikimedia Commons (nessuna chiave richiesta), o
scaricati da YouTube. Ogni asset conserva autore, licenza e origine: servono per
i crediti automatici nella caption.
"""

from __future__ import annotations

import html
import io
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Config
from .ffmpeg_utils import dimensioni_video, durata_media, estrai_fotogramma
from .models import Asset, ProduzioneReel
from .reel_produzione import cartella_media, elimina_file
from .ytdlp_manager import scarica_video

# Wikimedia chiede un User-Agent descrittivo; niente dati personali dell'utente.
_HEADERS = {"User-Agent": "GeneratoreCaroselliSEDS/1.0 (uso personale; progetto divulgativo SEDS UPO)"}
_LATO_MASSIMO_IMMAGINE = 4000  # oltre non serve per un video 1080x1920, anche con zoom
_LARGHEZZA_ANTEPRIMA = 480
LATO_MINIMO_CONSIGLIATO = 1920  # sotto questo lo zoom lento inizia a sgranare


class ErroreMedia(RuntimeError):
    pass


@dataclass
class RisultatoRicerca:
    fonte: str  # "nasa" | "wikimedia"
    tipo: str  # "immagine" | "video"
    titolo: str
    anteprima_url: str
    riferimento: str  # nasa_id per NASA, URL del file originale per Wikimedia
    autore: str | None
    licenza: str | None
    url_origine: str | None
    larghezza: int | None
    altezza: int | None


def _nuovo_id() -> str:
    return uuid.uuid4().hex[:12]


def _testo_pulito(valore: str | None) -> str | None:
    """I campi di Wikimedia contengono HTML (link all'autore): tieni solo il testo."""
    if not valore:
        return None
    testo = html.unescape(re.sub(r"<[^>]+>", "", valore)).strip()
    return re.sub(r"\s+", " ", testo) or None


# ---------------------------------------------------------------------------
# Import nella libreria
# ---------------------------------------------------------------------------


def importa_immagine(nome_reel: str, dati: bytes, titolo: str, fonte: str, autore: str | None = None,
                     licenza: str | None = None, url_origine: str | None = None) -> Asset:
    try:
        immagine = Image.open(io.BytesIO(dati))
        immagine.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ErroreMedia("Il file non è un'immagine valida.") from e

    immagine = ImageOps.exif_transpose(immagine).convert("RGB")  # rispetta la rotazione delle foto da telefono
    immagine.thumbnail((_LATO_MASSIMO_IMMAGINE, _LATO_MASSIMO_IMMAGINE), Image.LANCZOS)

    asset_id = _nuovo_id()
    cartella = cartella_media(nome_reel)
    cartella.mkdir(parents=True, exist_ok=True)
    immagine.save(cartella / f"{asset_id}.jpg", quality=92)

    anteprima = immagine.copy()
    anteprima.thumbnail((_LARGHEZZA_ANTEPRIMA, _LARGHEZZA_ANTEPRIMA * 2), Image.LANCZOS)
    anteprima.save(cartella / f"{asset_id}_anteprima.jpg", quality=80)

    return Asset(
        id=asset_id, tipo="immagine", fonte=fonte, file=f"{asset_id}.jpg", anteprima=f"{asset_id}_anteprima.jpg",
        titolo=titolo, autore=autore, licenza=licenza, url_origine=url_origine,
        larghezza=immagine.width, altezza=immagine.height,
    )


def importa_video(nome_reel: str, sorgente: Path, titolo: str, fonte: str, autore: str | None = None,
                  licenza: str | None = None, url_origine: str | None = None) -> Asset:
    """Sposta il file video nella libreria. Nessuna ricodifica qui: la fa il montaggio."""
    durata = durata_media(sorgente)
    dimensioni = dimensioni_video(sorgente)
    if not durata or not dimensioni:
        raise ErroreMedia("Il file non è un video leggibile.")

    asset_id = _nuovo_id()
    cartella = cartella_media(nome_reel)
    cartella.mkdir(parents=True, exist_ok=True)
    estensione = sorgente.suffix.lower() or ".mp4"
    destinazione = cartella / f"{asset_id}{estensione}"
    shutil.move(str(sorgente), destinazione)

    # Al 30% e non all'inizio: i filmati NASA (e molti altri) si aprono con una
    # schermata titolo che non rappresenta il contenuto del video.
    estrai_fotogramma(destinazione, durata * 0.3, cartella / f"{asset_id}_anteprima.jpg", _LARGHEZZA_ANTEPRIMA)

    return Asset(
        id=asset_id, tipo="video", fonte=fonte, file=destinazione.name, anteprima=f"{asset_id}_anteprima.jpg",
        titolo=titolo, autore=autore, licenza=licenza, url_origine=url_origine, durata=durata,
        larghezza=dimensioni[0], altezza=dimensioni[1],
    )


def importa_youtube(nome_reel: str, url: str, config: Config) -> Asset:
    temporanea = cartella_media(nome_reel) / "_download"
    scaricato = scarica_video(
        url, temporanea, _nuovo_id(),
        durata_massima_minuti=config.youtube_durata_massima_minuti,
        aggiornamento_automatico=config.ytdlp_aggiornamento_automatico,
    )
    try:
        return importa_video(
            nome_reel, scaricato.percorso, scaricato.titolo, "youtube",
            autore=scaricato.autore, licenza=scaricato.licenza, url_origine=scaricato.url,
        )
    finally:
        shutil.rmtree(temporanea, ignore_errors=True)


def rimuovi_asset(nome_reel: str, produzione: ProduzioneReel, asset_id: str) -> None:
    asset = produzione.asset_per_id(asset_id)
    if asset is None:
        return
    for nome_file in (asset.file, asset.anteprima):
        elimina_file(cartella_media(nome_reel) / nome_file)
    produzione.asset = [a for a in produzione.asset if a.id != asset_id]
    for stato in produzione.segmenti.values():
        if stato.asset_id == asset_id:
            stato.asset_id = None
            stato.inizio_video = 0.0


def _scarica_su_file(url: str, destinazione: Path) -> None:
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, headers=_HEADERS, timeout=120, follow_redirects=True) as risposta:
        risposta.raise_for_status()
        with open(destinazione, "wb") as f:
            for blocco in risposta.iter_bytes(1024 * 256):
                f.write(blocco)


# ---------------------------------------------------------------------------
# NASA Image and Video Library
# ---------------------------------------------------------------------------


def cerca_nasa(query: str, tipo: str, limite: int = 18) -> list[RisultatoRicerca]:
    media_type = "video" if tipo == "video" else "image"
    risposta = httpx.get(
        "https://images-api.nasa.gov/search",
        params={"q": query, "media_type": media_type, "page_size": limite},
        headers=_HEADERS, timeout=30,
    )
    risposta.raise_for_status()

    risultati = []
    for elemento in risposta.json()["collection"]["items"][:limite]:
        dati = elemento["data"][0]
        anteprima = next((l["href"] for l in elemento.get("links", []) if l.get("render") == "image"), None)
        if not anteprima:
            continue
        crediti = dati.get("photographer") or dati.get("secondary_creator")
        risultati.append(
            RisultatoRicerca(
                fonte="nasa", tipo="video" if media_type == "video" else "immagine",
                titolo=dati.get("title", dati["nasa_id"]), anteprima_url=anteprima, riferimento=dati["nasa_id"],
                autore=f"NASA/{crediti}" if crediti and "nasa" not in crediti.lower() else (crediti or "NASA"),
                licenza="NASA Media Usage Guidelines",
                url_origine=f"https://images.nasa.gov/details/{dati['nasa_id']}",
                larghezza=None, altezza=None,  # la ricerca NASA non restituisce le dimensioni
            )
        )
    return risultati


def _scegli_file_nasa(nasa_id: str, preferenze: list[str]) -> str:
    risposta = httpx.get(f"https://images-api.nasa.gov/asset/{nasa_id}", headers=_HEADERS, timeout=30)
    risposta.raise_for_status()
    file_disponibili = [e["href"] for e in risposta.json()["collection"]["items"]]
    for suffisso in preferenze:
        for href in file_disponibili:
            if href.lower().endswith(suffisso):
                return href.replace("http://", "https://")
    raise ErroreMedia("Nessun file scaricabile trovato per questo elemento NASA.")


# ---------------------------------------------------------------------------
# Wikimedia Commons
# ---------------------------------------------------------------------------


def cerca_wikimedia(query: str, limite: int = 18) -> list[RisultatoRicerca]:
    risposta = httpx.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limite * 2,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 500,
        },
        headers=_HEADERS, timeout=30,
    )
    risposta.raise_for_status()
    pagine = risposta.json().get("query", {}).get("pages", {})

    risultati = []
    for pagina in sorted(pagine.values(), key=lambda p: p.get("index", 0)):
        info = (pagina.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if max(info.get("width", 0), info.get("height", 0)) < 800:
            continue  # troppo piccole anche solo per uno sfondo
        metadati = info.get("extmetadata", {})
        risultati.append(
            RisultatoRicerca(
                fonte="wikimedia", tipo="immagine",
                titolo=pagina["title"].removeprefix("File:").rsplit(".", 1)[0],
                anteprima_url=info.get("thumburl") or info["url"], riferimento=info["url"],
                autore=_testo_pulito(metadati.get("Artist", {}).get("value")),
                licenza=_testo_pulito(metadati.get("LicenseShortName", {}).get("value")),
                url_origine=info.get("descriptionurl"),
                larghezza=info.get("width"), altezza=info.get("height"),
            )
        )
        if len(risultati) >= limite:
            break
    return risultati


# ---------------------------------------------------------------------------
# Import di un risultato di ricerca
# ---------------------------------------------------------------------------


def scarica_immagine_pubblica(fonte: str, riferimento: str, preferenze_nasa: list[str] | None = None) -> bytes:
    """I byte di un'immagine NASA (dato il nasa_id) o Wikimedia (dato l'URL del file,
    accettato solo se è davvero su upload.wikimedia.org)."""
    if fonte == "nasa":
        url = _scegli_file_nasa(riferimento, preferenze_nasa or ["~orig.jpg", "~large.jpg", "~medium.jpg", "~orig.png"])
    elif fonte == "wikimedia":
        if not riferimento.startswith("https://upload.wikimedia.org/"):
            raise ErroreMedia("URL del file non valido.")
        url = riferimento
    else:
        raise ErroreMedia(f"Fonte non supportata: {fonte}")
    try:
        risposta = httpx.get(url, headers=_HEADERS, timeout=120, follow_redirects=True)
        risposta.raise_for_status()
    except httpx.HTTPError as e:
        raise ErroreMedia(f"Download non riuscito: {e}") from e
    return risposta.content


def importa_risultato(nome_reel: str, risultato: RisultatoRicerca) -> Asset:
    try:
        if risultato.fonte == "nasa" and risultato.tipo == "video":
            url = _scegli_file_nasa(risultato.riferimento, ["~large.mp4", "~medium.mp4", "~mobile.mp4", "~orig.mp4"])
            temporaneo = cartella_media(nome_reel) / "_download" / f"{_nuovo_id()}.mp4"
            try:
                _scarica_su_file(url, temporaneo)
                return importa_video(
                    nome_reel, temporaneo, risultato.titolo, "nasa",
                    autore=risultato.autore, licenza=risultato.licenza, url_origine=risultato.url_origine,
                )
            finally:
                shutil.rmtree(temporaneo.parent, ignore_errors=True)

        dati = scarica_immagine_pubblica(risultato.fonte, risultato.riferimento)
        return importa_immagine(
            nome_reel, dati, risultato.titolo, risultato.fonte,
            autore=risultato.autore, licenza=risultato.licenza, url_origine=risultato.url_origine,
        )
    except httpx.HTTPError as e:
        raise ErroreMedia(f"Download non riuscito: {e}") from e
