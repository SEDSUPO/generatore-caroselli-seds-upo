"""Sottotitoli del reel in formato ASS, bruciati nel video da ffmpeg (libass).

- Durante l'hook: titolo grande nella parte alta (i primi secondi decidono se chi
  guarda resta), invece dei sottotitoli normali che lo duplicherebbero.
- Poi: sottotitoli a gruppi di poche parole, testo bianco senza contorno, con un
  rettangolino rosso del brand dietro la parola pronunciata in quel momento.

ASS non sa disegnare uno sfondo dietro una sola parola: il rettangolo è una forma
vettoriale (\\p1) su un livello sotto il testo, posizionata calcolando dove cade
ogni parola con le metriche del font. Per farlo coincidere con il testo si replica
come libass dimensiona il font: la "dimensione" ASS corrisponde all'altezza
winAscent + winDescent, non all'em (verificato renderizzando con ffmpeg).

Coordinate in spazio 1080x1920 (PlayRes): libass le scala da solo se il video
è più piccolo (anteprima). Il font è l'istanza statica Bold di Space Grotesk:
libass non gestisce bene il file a peso variabile e ripiegherebbe su un altro font.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import ImageFont

from .config import FONTS_DIR
from .models import ParolaTrascritta, ProduzioneReel, Reel
from .reel_produzione import segmenti_parlati

FAMIGLIA_FONT = "SEDS Grotesk Bold"
FILE_FONT = "SEDSGrotesk-Bold.ttf"

_BIANCO = "&H00FFFFFF"
_ROSSO = "&H002229AA"  # #aa2922 in ordine BGR, come vuole ASS

_PAROLE_MASSIME_PER_GRUPPO = 3
_CARATTERI_MASSIMI_PER_GRUPPO = 20
_PAUSA_MASSIMA_A_SCHERMO = 0.4  # dopo l'ultima parola di un gruppo, il testo resta un attimo

LARGHEZZA, ALTEZZA = 1080, 1920
DIMENSIONE_SOTTOTITOLI = 92
LINEA_BASE_SOTTOTITOLI = 1310  # in basso, ma sopra l'area coperta da nome utente e didascalia di Instagram
DIMENSIONE_TITOLO = 98


@dataclass(frozen=True)
class _Metriche:
    em: float  # dimensione in pixel dell'em per il font ASS di questa dimensione
    ascesa: float  # dal bordo alto della riga (\an7) alla linea di base
    maiuscole: float  # altezza delle maiuscole
    font: ImageFont.FreeTypeFont


@lru_cache(maxsize=4)
def _metriche(dimensione_ass: int) -> _Metriche:
    percorso = FONTS_DIR / FILE_FONT
    tabelle = TTFont(str(percorso))
    os2, unita = tabelle["OS/2"], tabelle["head"].unitsPerEm
    altezza_win = os2.usWinAscent + os2.usWinDescent
    em = dimensione_ass * unita / altezza_win
    return _Metriche(
        em=em,
        ascesa=dimensione_ass * os2.usWinAscent / altezza_win,
        maiuscole=em * os2.sCapHeight / unita,
        font=ImageFont.truetype(str(percorso), em),
    )


def _rettangolo_arrotondato(larghezza: float, altezza: float, raggio: float) -> str:
    """Tracciato ASS (\\p1) di un rettangolo con angoli arrotondati, origine in alto a sinistra."""
    w, h, r = round(larghezza), round(altezza), round(min(raggio, larghezza / 2, altezza / 2))
    k = round(r * 0.45)  # punti di controllo per approssimare il quarto di cerchio
    return (
        f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
        f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0"
    )


@dataclass
class _ParolaTimeline:
    testo: str
    inizio: float
    fine: float
    fine_segmento: float


def _tempo_ass(secondi: float) -> str:
    secondi = max(secondi, 0.0)
    centesimi = int(round(secondi * 100))
    ore, resto = divmod(centesimi, 360000)
    minuti, resto = divmod(resto, 6000)
    sec, cent = divmod(resto, 100)
    return f"{ore}:{minuti:02d}:{sec:02d}.{cent:02d}"


def _pulisci(testo: str) -> str:
    return testo.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _unisci_apostrofi(parole: list[ParolaTrascritta]) -> list[ParolaTrascritta]:
    """Whisper spezza "l'alto" in "l" + "'alto": si riuniscono per i sottotitoli."""
    risultato: list[ParolaTrascritta] = []
    for parola in parole:
        if risultato and (parola.testo.startswith("'") or risultato[-1].testo.endswith("'")):
            precedente = risultato[-1]
            risultato[-1] = ParolaTrascritta(
                testo=precedente.testo + parola.testo, inizio=precedente.inizio, fine=parola.fine
            )
        else:
            risultato.append(parola)
    return risultato


def _raggruppa(parole: list[_ParolaTimeline]) -> list[list[_ParolaTimeline]]:
    gruppi: list[list[_ParolaTimeline]] = []
    corrente: list[_ParolaTimeline] = []
    for parola in parole:
        lunghezza = sum(len(p.testo) + 1 for p in corrente) + len(parola.testo)
        cambio_segmento = corrente and corrente[-1].fine_segmento != parola.fine_segmento
        if corrente and (len(corrente) >= _PAROLE_MASSIME_PER_GRUPPO or lunghezza > _CARATTERI_MASSIMI_PER_GRUPPO or cambio_segmento):
            gruppi.append(corrente)
            corrente = []
        corrente.append(parola)
        if parola.testo[-1:] in ".,;:!?":
            gruppi.append(corrente)
            corrente = []
    if corrente:
        gruppi.append(corrente)
    return gruppi


def genera_ass(reel: Reel, produzione: ProduzioneReel, destinazione: Path) -> None:
    righe = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        # Testo bianco pieno, senza contorno né ombra (Outline 0, Shadow 0). Posizione
        # data evento per evento con \pos, per allineare il rettangolo della parola.
        f"Style: Sub,{FAMIGLIA_FONT},{DIMENSIONE_SOTTOTITOLI},{_BIANCO},{_BIANCO},{_BIANCO},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Evidenza,{FAMIGLIA_FONT},{DIMENSIONE_SOTTOTITOLI},{_ROSSO},{_ROSSO},{_ROSSO},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        # Titolo dell'hook: grande, nella parte alta, sotto l'intestazione dell'app.
        f"Style: Titolo,{FAMIGLIA_FONT},{DIMENSIONE_TITOLO},{_BIANCO},{_BIANCO},{_BIANCO},&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,90,90,330,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    parole_timeline: list[_ParolaTimeline] = []
    tempo = 0.0
    for segmento in segmenti_parlati(reel):
        stato = produzione.segmenti[segmento.chiave]
        take = stato.take_attivo()
        if take is None:
            continue
        inizio_segmento, fine_segmento = tempo, tempo + take.durata
        tempo = fine_segmento

        if segmento.chiave == "hook" and produzione.titolo_hook_attivo:
            testo = _pulisci(segmento.testo)
            righe.append(
                f"Dialogue: 1,{_tempo_ass(inizio_segmento)},{_tempo_ass(fine_segmento)},Titolo,,0,0,0,,{{\\fad(250,200)}}{testo}"
            )
            continue

        if not produzione.sottotitoli_attivi or not stato.parole:
            continue
        for parola in _unisci_apostrofi(stato.parole):
            parole_timeline.append(
                _ParolaTimeline(
                    testo=_pulisci(parola.testo),
                    inizio=inizio_segmento + parola.inizio,
                    fine=min(inizio_segmento + parola.fine, fine_segmento),
                    fine_segmento=fine_segmento,
                )
            )

    metriche = _metriche(DIMENSIONE_SOTTOTITOLI)
    spazio = metriche.font.getlength(" ")
    alto_riga = LINEA_BASE_SOTTOTITOLI - metriche.ascesa
    # Rettangolo: dalla cima delle maiuscole alla linea di base, con un po' di aria
    # (stesse proporzioni del carosello scientifico).
    alto_rettangolo = LINEA_BASE_SOTTOTITOLI - metriche.maiuscole - metriche.em * 0.2
    basso_rettangolo = LINEA_BASE_SOTTOTITOLI + metriche.em * 0.24
    margine_rettangolo = metriche.em * 0.14

    gruppi = _raggruppa(parole_timeline)
    for indice_gruppo, gruppo in enumerate(gruppi):
        successivo = gruppi[indice_gruppo + 1][0].inizio if indice_gruppo + 1 < len(gruppi) else None
        fine_gruppo = min(gruppo[-1].fine + _PAUSA_MASSIMA_A_SCHERMO, gruppo[-1].fine_segmento)
        if successivo is not None:
            fine_gruppo = min(fine_gruppo, successivo)

        larghezze = [metriche.font.getlength(p.testo) for p in gruppo]
        x_riga = (LARGHEZZA - (sum(larghezze) + spazio * (len(gruppo) - 1))) / 2
        testo_gruppo = " ".join(p.testo for p in gruppo)

        # Un evento per ogni parola del gruppo: il testo resta fermo, si sposta solo il
        # rettangolo rosso (livello 0) sotto la parola pronunciata (testo al livello 1).
        x_parola = x_riga
        for indice, parola in enumerate(gruppo):
            inizio_evento = gruppo[0].inizio if indice == 0 else parola.inizio
            fine_evento = gruppo[indice + 1].inizio if indice + 1 < len(gruppo) else fine_gruppo
            if fine_evento > inizio_evento:
                inizio, fine = _tempo_ass(inizio_evento), _tempo_ass(fine_evento)
                larghezza_rettangolo = larghezze[indice] + 2 * margine_rettangolo
                altezza_rettangolo = basso_rettangolo - alto_rettangolo
                forma = _rettangolo_arrotondato(larghezza_rettangolo, altezza_rettangolo, metriche.em * 0.14)
                righe.append(
                    f"Dialogue: 0,{inizio},{fine},Evidenza,,0,0,0,,"
                    f"{{\\pos({x_parola - margine_rettangolo:.1f},{alto_rettangolo:.1f})\\p1}}{forma}"
                )
                righe.append(f"Dialogue: 1,{inizio},{fine},Sub,,0,0,0,,{{\\pos({x_riga:.1f},{alto_riga:.1f})}}{testo_gruppo}")
            x_parola += larghezze[indice] + spazio

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text("\n".join(righe) + "\n", encoding="utf-8")
