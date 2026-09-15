"""Montaggio del reel in MP4 verticale 1080x1920 pronto per Instagram.

Fasi:
1. trascrizione delle registrazioni con Whisper (solo quelle non ancora trascritte)
2. traccia voce: le registrazioni scelte, una dopo l'altra
3. un clip per ogni blocco visivo, lungo esattamente quanto il parlato che copre:
   immagini con zoom lento costante, video tagliati sullo spezzone scelto
4. unione dei clip
5. passaggio finale: sottotitoli e titolo dell'hook (ASS), logo, audio (voce +
   eventuale musica che si abbassa da sola quando si parla, volume a -14 LUFS)

Durate calcolate a fotogrammi interi e cumulate: così la somma dei clip coincide
col parlato e l'audio non va fuori sincrono man mano che il video procede.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .config import FONTS_DIR, LOGO_PATH, Config
from .ffmpeg_utils import esegui
from .models import ProduzioneReel, Reel
from .reel_produzione import (
    Blocco,
    calcola_blocchi,
    carica_produzione,
    cartella_audio,
    cartella_media,
    cartella_output,
    mancanze,
    modifica_produzione,
    segmenti_parlati,
)
from .reel_sottotitoli import ALTEZZA as ALTEZZA_RIFERIMENTO
from .reel_sottotitoli import FILE_FONT, LINEA_BASE_SOTTOTITOLI, genera_ass
from .reel_trascrizione import trascrivi_parole

FPS = 30
ZOOM_AL_SECONDO = 0.015  # zoom lento: +1.5% di ingrandimento al secondo
ZOOM_TOTALE_MASSIMO = 0.30  # anche su blocchi lunghi non oltre +30%, per non sgranare
RAPPORTO_OLTRE_CUI_SFONDO_SFOCATO = 0.75  # più larghe di 3:4 → sfondo sfocato ai lati invece di tagliare


@dataclass(frozen=True)
class Qualita:
    larghezza: int
    altezza: int
    preset: str
    crf: int
    nome_file: str


QUALITA_FINALE = Qualita(1080, 1920, "medium", 20, "reel.mp4")
QUALITA_ANTEPRIMA = Qualita(540, 960, "ultrafast", 30, "anteprima.mp4")

SuAvanzamento = Callable[[str, float], None]


class ErroreMontaggio(RuntimeError):
    pass


def _percorso_ffmpeg(percorso: Path) -> str:
    """Per le liste del concat demuxer: barre in avanti e apici escapati."""
    return str(percorso.resolve()).replace("\\", "/").replace("'", "'\\''")


# ---------------------------------------------------------------------------
# 1. Trascrizione
# ---------------------------------------------------------------------------


def _segmenti_da_trascrivere(reel: Reel, produzione: ProduzioneReel) -> list[str]:
    if not produzione.sottotitoli_attivi:
        return []
    chiavi = []
    for segmento in segmenti_parlati(reel):
        if segmento.chiave == "hook" and produzione.titolo_hook_attivo:
            continue  # durante l'hook si mostra il titolo, non i sottotitoli
        stato = produzione.segmenti[segmento.chiave]
        if stato.take_scelto and (stato.parole is None or stato.parole_take != stato.take_scelto):
            chiavi.append(segmento.chiave)
    return chiavi


def trascrivi_mancanti(config: Config, reel: Reel, su_avanzamento: SuAvanzamento) -> None:
    produzione = carica_produzione(reel)
    testi = {s.chiave: s.testo for s in segmenti_parlati(reel)}
    da_fare = _segmenti_da_trascrivere(reel, produzione)

    for indice, chiave in enumerate(da_fare):
        su_avanzamento(f"Trascrizione sottotitoli ({indice + 1}/{len(da_fare)})", indice / len(da_fare))
        take = produzione.segmenti[chiave].take_attivo()
        parole = trascrivi_parole(cartella_audio(reel.nome_reel) / take.file, config.whisper_modello, testi[chiave])

        # Si ricarica lo stato prima di salvare: nel frattempo dall'interfaccia può
        # essere cambiato qualcos'altro, e non va sovrascritto.
        with modifica_produzione(reel) as aggiornata:
            stato = aggiornata.segmenti.get(chiave)
            if stato and stato.take_scelto == take.id:
                stato.parole = parole
                stato.parole_take = take.id


# ---------------------------------------------------------------------------
# 3. Clip dei blocchi visivi
# ---------------------------------------------------------------------------


def _base_immagine(percorso: Path, larghezza: int, altezza: int, riempi: bool = False, inquadratura_x: float = 0.5) -> Image.Image:
    immagine = Image.open(percorso).convert("RGB")
    if riempi or immagine.width / immagine.height <= RAPPORTO_OLTRE_CUI_SFONDO_SFOCATO:
        return ImageOps.fit(immagine, (larghezza, altezza), Image.LANCZOS, centering=(inquadratura_x, 0.5))

    # Sfondo: la stessa immagine a tutto schermo, sfocata e scurita. Sfocata a bassa
    # risoluzione e poi ingrandita: stesso risultato, molto più veloce.
    piccola = ImageOps.fit(immagine, (larghezza // 8, altezza // 8), Image.BILINEAR)
    sfondo = piccola.filter(ImageFilter.GaussianBlur(radius=6)).resize((larghezza, altezza), Image.BICUBIC)
    sfondo = ImageEnhance.Brightness(sfondo).enhance(0.55)
    primo_piano = ImageOps.contain(immagine, (larghezza, altezza), Image.LANCZOS)
    sfondo.paste(primo_piano, ((larghezza - primo_piano.width) // 2, (altezza - primo_piano.height) // 2))
    return sfondo


# Le JPEG portano colori "full range": senza conversione esplicita il video esce yuvj420p,
# che su molti telefoni (e su Instagram) si vede con neri grigi e colori slavati.
_COLORE_VIDEO = "scale=iw:ih:out_range=tv:out_color_matrix=bt709,format=yuv420p"
_TAG_COLORE = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _codifica_intermedia() -> list[str]:
    # Qualità alta e codifica veloce: questi clip vengono ricodificati nel passaggio finale.
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-video_track_timescale", "15360", "-an", *_TAG_COLORE]


def _clip_immagine(blocco: Blocco, sorgente: Path, fotogrammi: int, q: Qualita, lavoro: Path, indice: int) -> Path:
    # Base al doppio della risoluzione d'uscita: zoompan arrotonda le coordinate ai pixel
    # interi dell'ingresso, e con più pixel lo zoom scorre fluido invece di tremolare.
    base = lavoro / f"base_{indice:02d}.jpg"
    _base_immagine(sorgente, q.larghezza * 2, q.altezza * 2, blocco.riempi_schermo, blocco.inquadratura_x).save(base, quality=95)

    durata = fotogrammi / FPS
    zoom_totale = min(ZOOM_AL_SECONDO * durata, ZOOM_TOTALE_MASSIMO)
    passo = zoom_totale / max(fotogrammi, 1)
    uscita = lavoro / f"blocco_{indice:02d}.mp4"
    esegui([
        "-loop", "1", "-framerate", str(FPS), "-i", str(base),
        "-vf", (
            f"zoompan=z='1+{passo:.8f}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={q.larghezza}x{q.altezza}:fps={FPS},setsar=1,{_COLORE_VIDEO}"
        ),
        "-frames:v", str(fotogrammi),
        *_codifica_intermedia(), str(uscita),
    ])
    return uscita


def _clip_video(blocco: Blocco, sorgente: Path, fotogrammi: int, q: Qualita, lavoro: Path, indice: int) -> Path:
    asset = blocco.asset
    durata = fotogrammi / FPS
    w, h = q.larghezza, q.altezza

    if blocco.riempi_schermo:
        # Ingrandito a tutto schermo: si taglia la larghezza in eccesso, spostando la
        # finestra secondo l'inquadratura scelta.
        grafo = (
            f"[0:v]fps={FPS},scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x='(iw-ow)*{blocco.inquadratura_x:.3f}':y='(ih-oh)/2',"
        )
    elif asset.larghezza / asset.altezza > RAPPORTO_OLTRE_CUI_SFONDO_SFOCATO:
        grafo = (
            f"[0:v]fps={FPS},split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"scale={w // 8}:{h // 8},gblur=sigma=6,scale={w}:{h},eq=brightness=-0.12[sfondo];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[primo];"
            f"[sfondo][primo]overlay=(W-w)/2:(H-h)/2,"
        )
    else:
        grafo = f"[0:v]fps={FPS},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
    # tpad: se lo spezzone rimasto è più corto del parlato, si tiene fermo l'ultimo fotogramma.
    grafo += f"tpad=stop_mode=clone:stop_duration={durata:.3f},setsar=1,{_COLORE_VIDEO}[v]"

    uscita = lavoro / f"blocco_{indice:02d}.mp4"
    esegui([
        "-ss", f"{max(blocco.inizio_video, 0):.3f}", "-i", str(sorgente),
        "-filter_complex", grafo, "-map", "[v]",
        "-frames:v", str(fotogrammi),
        *_codifica_intermedia(), str(uscita),
    ])
    return uscita


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------


def monta(config: Config, reel: Reel, anteprima: bool, su_avanzamento: SuAvanzamento) -> Path:
    q = QUALITA_ANTEPRIMA if anteprima else QUALITA_FINALE
    nome = reel.nome_reel

    produzione = carica_produzione(reel)
    problemi = mancanze(reel, produzione)
    if problemi:
        raise ErroreMontaggio("Impossibile montare, mancano ancora:\n- " + "\n- ".join(problemi))

    # 1. Trascrizione (pesa ~15% dell'avanzamento se c'è da fare)
    peso_trascrizione = 0.15 if _segmenti_da_trascrivere(reel, produzione) else 0.0
    trascrivi_mancanti(config, reel, lambda fase, f: su_avanzamento(fase, f * peso_trascrizione))
    produzione = carica_produzione(reel)

    uscita_finale = cartella_output(nome) / q.nome_file
    lavoro = cartella_output(nome) / f"_lavoro_{Path(q.nome_file).stem}"
    shutil.rmtree(lavoro, ignore_errors=True)
    lavoro.mkdir(parents=True)

    try:
        # 2. Voce
        su_avanzamento("Preparazione traccia voce", peso_trascrizione)
        lista_audio = lavoro / "voce.txt"
        lista_audio.write_text(
            "".join(
                f"file '{_percorso_ffmpeg(cartella_audio(nome) / produzione.segmenti[s.chiave].take_attivo().file)}'\n"
                for s in segmenti_parlati(reel)
            ),
            encoding="utf-8",
        )
        voce = lavoro / "voce.wav"
        esegui(["-f", "concat", "-safe", "0", "-i", str(lista_audio), "-c", "copy", str(voce)])

        # 3. Clip dei blocchi
        blocchi = calcola_blocchi(reel, produzione)
        durata_totale = sum(b.durata for b in blocchi)
        inizio_blocchi, peso_blocchi = peso_trascrizione + 0.03, 0.52
        clip: list[Path] = []
        fotogramma_corrente = 0
        secondi_fatti = 0.0
        for indice, blocco in enumerate(blocchi):
            su_avanzamento(
                f"Clip visivo {indice + 1}/{len(blocchi)}",
                inizio_blocchi + peso_blocchi * (secondi_fatti / durata_totale),
            )
            fotogramma_fine = round((blocco.inizio + blocco.durata) * FPS)
            fotogrammi = max(fotogramma_fine - fotogramma_corrente, 1)
            fotogramma_corrente = fotogramma_fine
            sorgente = cartella_media(nome) / blocco.asset.file
            if blocco.asset.tipo == "immagine":
                clip.append(_clip_immagine(blocco, sorgente, fotogrammi, q, lavoro, indice))
            else:
                clip.append(_clip_video(blocco, sorgente, fotogrammi, q, lavoro, indice))
            secondi_fatti += blocco.durata

        # 4. Unione
        su_avanzamento("Unione dei clip", inizio_blocchi + peso_blocchi)
        lista_clip = lavoro / "clip.txt"
        lista_clip.write_text("".join(f"file '{_percorso_ffmpeg(c)}'\n" for c in clip), encoding="utf-8")
        visivi = lavoro / "visivi.mp4"
        esegui(["-f", "concat", "-safe", "0", "-i", str(lista_clip), "-c", "copy", str(visivi)])

        # 5. Passaggio finale
        _passaggio_finale(reel, produzione, q, lavoro, visivi, voce, durata_totale, uscita_finale,
                          lambda f: su_avanzamento("Composizione finale", inizio_blocchi + peso_blocchi + 0.02 + 0.28 * f))
    finally:
        shutil.rmtree(lavoro, ignore_errors=True)

    su_avanzamento("Completato", 1.0)
    return uscita_finale


def _crea_velo_testo(q: Qualita, produzione: ProduzioneReel, destinazione: Path) -> None:
    """PNG trasparente con due sfumature scure morbide: in alto (titolo dell'hook e
    logo) e in basso attorno ai sottotitoli. Nessun bordo netto: si scurisce solo
    quanto basta perché il bianco non si perda su un cielo o una parete chiara."""
    scala = q.altezza / ALTEZZA_RIFERIMENTO
    colonna = Image.new("L", (1, q.altezza), 0)
    valori = [0.0] * q.altezza

    # In alto: sempre un velo leggero per il logo, più deciso se c'è il titolo dell'hook.
    intensita_alto, fine_alto = (0.65, round(760 * scala)) if produzione.titolo_hook_attivo else (0.4, round(420 * scala))
    for y in range(fine_alto):
        valori[y] = intensita_alto * (1 - y / fine_alto) ** 1.3

    # In basso: sfumatura progressiva su tutto il terzo inferiore (non una fascia, che su
    # uno sfondo uniforme si vedrebbe come una striscia): piena dalla riga dei sottotitoli in giù.
    if produzione.sottotitoli_attivi:
        inizio = round(900 * scala)
        pieno = round((LINEA_BASE_SOTTOTITOLI - 40) * scala)
        for y in range(inizio, q.altezza):
            t = min((y - inizio) / (pieno - inizio), 1.0)
            valori[y] = max(valori[y], 0.5 * t * t * (3 - 2 * t))
    for y, valore in enumerate(valori):
        colonna.putpixel((0, y), round(255 * valore))
    velo = Image.new("RGBA", (q.larghezza, q.altezza), (8, 10, 20, 0))
    velo.putalpha(colonna.resize((q.larghezza, q.altezza)))
    velo.save(destinazione)


def _posizione_logo(q: Qualita, larghezza_logo: int) -> tuple[int, int]:
    """Logo nell'angolo in alto a sinistra, alla stessa distanza dal bordo sinistro e
    da quello superiore. Il PNG ha margini trasparenti diversi ai lati e in alto: si
    allinea la parte visibile, non il riquadro del file, altrimenti a occhio le due
    distanze non tornano."""
    x = round(q.larghezza * 0.065)
    logo = Image.open(LOGO_PATH)
    scala = larghezza_logo / logo.width
    sinistra, alto, _, _ = logo.getbbox()
    distanza_visibile = x + sinistra * scala  # dal bordo sinistro alla parte disegnata del logo
    return x, round(distanza_visibile - alto * scala)


def _passaggio_finale(reel: Reel, produzione: ProduzioneReel, q: Qualita, lavoro: Path, visivi: Path, voce: Path,
                      durata: float, uscita: Path, su_avanzamento: Callable[[float], None]) -> None:
    (lavoro / "fonts").mkdir(exist_ok=True)
    shutil.copy(FONTS_DIR / FILE_FONT, lavoro / "fonts" / FILE_FONT)
    shutil.copy(LOGO_PATH, lavoro / "logo.png")

    usa_ass = produzione.sottotitoli_attivi or produzione.titolo_hook_attivo
    if usa_ass:
        genera_ass(reel, produzione, lavoro / "sottotitoli.ass")

    ingressi = ["-i", str(visivi), "-i", str(voce), "-i", "logo.png"]
    musica = cartella_media(reel.nome_reel) / produzione.musica_file if produzione.musica_file else None
    if musica and musica.is_file():
        ingressi += ["-stream_loop", "-1", "-i", str(musica)]
    else:
        musica = None

    larghezza_logo = round(q.larghezza * 0.14)
    x_logo, y_logo = _posizione_logo(q, larghezza_logo)

    catena_video = "[0:v]"
    if usa_ass:
        # Il testo è bianco senza contorno: una sfumatura scura sotto le zone del testo
        # lo tiene leggibile anche su riprese chiare. Ultimo ingresso, così gli indici
        # di voce, logo e musica non cambiano.
        _crea_velo_testo(q, produzione, lavoro / "velo.png")
        indice_velo = 4 if musica else 3
        ingressi += ["-i", "velo.png"]
        catena_video = f"[0:v][{indice_velo}:v]overlay=0:0,ass=sottotitoli.ass:fontsdir=fonts[testo];[testo]"
    grafo = [
        f"[2:v]scale={larghezza_logo}:-1[logo]",
        f"{catena_video}[logo]overlay={x_logo}:{y_logo},{_COLORE_VIDEO}[v]",
    ]
    normalizza = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if musica:
        grafo += [
            "[1:a]asplit=2[voce][chiave]",
            f"[3:a]aresample=48000,volume={produzione.musica_volume:.3f},atrim=0:{durata:.3f}[mus]",
            # La voce comanda il compressore sulla musica: quando si parla, la musica si abbassa.
            "[mus][chiave]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=400[sotto]",
            f"[voce][sotto]amix=inputs=2:duration=first:normalize=0,{normalizza},"
            f"afade=t=out:st={max(durata - 1.5, 0):.3f}:d=1.5[a]",
        ]
    else:
        grafo.append(f"[1:a]{normalizza}[a]")

    esegui(
        [
            *ingressi,
            "-filter_complex", ";".join(grafo),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", q.preset, "-crf", str(q.crf), "-pix_fmt", "yuv420p", "-r", str(FPS),
            *_TAG_COLORE,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            "-t", f"{durata:.3f}",
            str(uscita.resolve()),
        ],
        durata_attesa=durata,
        su_avanzamento=su_avanzamento,
        cwd=lavoro,
    )
