"""Step 5-6: composizione di ogni slide con Pillow (sfondo + testo + logo).

Il testo viene posizionato dentro le "zone pulite" individuate da
layout_vision.rileva_zone_testo (analisi Gemini vision dello sfondo, prima di
disegnare qualunque cosa), così da non sovrapporsi mai all'illustrazione. Se
il copy non entra nella zona migliore, viene diviso tra le due zone
individuate. Se il rilevamento fallisce, si ricade su una fascia superiore di
default con un velo di sicurezza più marcato.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import Config, LOGO_PATH
from .evidenziazione import disegna_evidenziazioni
from .image_utils import cover_crop
from .layout_vision import rileva_zone_testo
from .models import Segmento, SlideCompleta, ZonaTesto
from .text_metrics import (
    ALTEZZA_SLIDE,
    CANDIDATI_DIMENSIONE,
    CANDIDATI_DIMENSIONE_HOOK,
    INTERLINEA,
    LARGHEZZA_SLIDE,
    MARGINE_SICUREZZA,
    altezza_blocco,
    carica_font,
    dimensione_iniziale,
    parole_da_segmenti,
    spezza_in_righe,
)

ZONA_LOGO_LATO = 150

BIANCO = (255, 255, 255)
ACCENTO_ROSSO = (0xAA, 0x29, 0x22)
NAVY = (0x28, 0x2D, 0x43)

# Dimensione minima di una zona rilevata perché venga considerata utilizzabile;
# sotto questa soglia la scartiamo e usiamo il fallback.
LARGHEZZA_MINIMA_ZONA = 300
ALTEZZA_MINIMA_ZONA = 90

SFUMATURA_VELO = 48
ALPHA_VELO_ZONA_RILEVATA = 130  # zona già giudicata pulita: velo leggero, solo di sicurezza
ALPHA_VELO_FALLBACK = 215  # nessuna zona rilevata: non sappiamo cosa c'è sotto, velo pieno


@dataclass
class BoxPixel:
    x: int
    y: int
    larghezza: int
    altezza: int


def _adatta_testo_a_zona(
    segmenti: list[Segmento], zona: BoxPixel, candidati: list[int]
) -> tuple[ImageFont.FreeTypeFont, list[list[tuple[str, bool]]]]:
    parole = parole_da_segmenti(segmenti)
    num_caratteri = sum(len(p) for p, _ in parole)
    indice_iniziale = candidati.index(dimensione_iniziale(num_caratteri, candidati))

    font, righe = carica_font(candidati[-1]), []
    for indice in range(indice_iniziale, len(candidati)):
        dimensione = candidati[indice]
        font = carica_font(dimensione)
        righe = spezza_in_righe(parole, font, zona.larghezza)
        if altezza_blocco(font, righe) <= zona.altezza or indice == len(candidati) - 1:
            break
    return font, righe


def _dividi_segmenti(
    segmenti: list[Segmento], area_zona0: float, area_zona1: float
) -> tuple[list[Segmento], list[Segmento]]:
    totale = sum(len(s.testo) for s in segmenti) or 1
    target_zona0 = totale * area_zona0 / (area_zona0 + area_zona1)

    cumulato = 0
    taglio = len(segmenti)
    for indice, segmento in enumerate(segmenti):
        cumulato += len(segmento.testo)
        if cumulato >= target_zona0:
            taglio = indice + 1
            break

    taglio = max(1, min(taglio, len(segmenti) - 1)) if len(segmenti) > 1 else len(segmenti)
    return segmenti[:taglio], segmenti[taglio:]


def _disegna_velo(canvas: Image.Image, y_inizio: int, y_fine: int, alpha_massimo: int) -> None:
    """Sovrappone un velo navy semi-trasparente e sfumato su una fascia orizzontale,
    come rete di sicurezza per il contrasto anche se la zona non è perfettamente pulita.
    """
    y_inizio = max(int(y_inizio), 0)
    y_fine = min(int(y_fine), ALTEZZA_SLIDE)
    altezza = y_fine - y_inizio
    if altezza <= 0:
        return

    colonna = Image.new("L", (1, altezza))
    for y in range(altezza):
        alpha = alpha_massimo
        if y < SFUMATURA_VELO:
            alpha = int(alpha * (y / SFUMATURA_VELO))
        if y > altezza - SFUMATURA_VELO:
            alpha = int(alpha * ((altezza - y) / SFUMATURA_VELO))
        colonna.putpixel((0, y), max(alpha, 0))

    maschera = colonna.resize((LARGHEZZA_SLIDE, altezza))
    velo = Image.new("RGBA", (LARGHEZZA_SLIDE, altezza), (*NAVY, 0))
    velo.putalpha(maschera)
    canvas.paste(velo, (0, y_inizio), velo)


def _disegna_righe(
    canvas: Image.Image, righe: list, font: ImageFont.FreeTypeFont, x_inizio: int, y_inizio: int
) -> None:
    draw = ImageDraw.Draw(canvas)
    spazio = font.getlength(" ")
    altezza_riga = font.size * INTERLINEA
    y = y_inizio
    for riga in righe:
        disegna_evidenziazioni(draw, riga, font, x_inizio, y)
        x = x_inizio
        for parola, _ in riga:
            draw.text((x, y), parola, font=font, fill=BIANCO)
            x += font.getlength(parola) + spazio
        y += altezza_riga


def _zona_default() -> tuple[BoxPixel, bool]:
    """Fascia superiore usata quando il rilevamento delle zone pulite fallisce
    o non produce risultati utilizzabili. Il bool indica "zona non verificata"
    (velo pieno) per distinguerla da una zona confermata da Gemini vision.
    """
    return (
        BoxPixel(
            x=MARGINE_SICUREZZA,
            y=MARGINE_SICUREZZA,
            larghezza=LARGHEZZA_SLIDE - 2 * MARGINE_SICUREZZA,
            altezza=int(ALTEZZA_SLIDE * 0.4),
        ),
        False,
    )


def _converti_zona(zona: ZonaTesto) -> BoxPixel | None:
    x = int(zona.x * LARGHEZZA_SLIDE)
    y = int(zona.y * ALTEZZA_SLIDE)
    x_fine = x + int(zona.larghezza * LARGHEZZA_SLIDE)
    y_fine = y + int(zona.altezza * ALTEZZA_SLIDE)

    x = max(x, MARGINE_SICUREZZA)
    y = max(y, MARGINE_SICUREZZA)
    x_fine = min(x_fine, LARGHEZZA_SLIDE - MARGINE_SICUREZZA)
    y_fine = min(y_fine, ALTEZZA_SLIDE - MARGINE_SICUREZZA)

    # non invadere l'angolo del logo (basso a destra): il testo non deve mai finirci
    # sopra, anche se va bene che ci finisca l'illustrazione.
    logo_x = LARGHEZZA_SLIDE - MARGINE_SICUREZZA - ZONA_LOGO_LATO
    logo_y = ALTEZZA_SLIDE - MARGINE_SICUREZZA - ZONA_LOGO_LATO
    if x_fine > logo_x and y_fine > logo_y:
        y_fine = min(y_fine, logo_y)

    larghezza, altezza = x_fine - x, y_fine - y
    if larghezza < LARGHEZZA_MINIMA_ZONA or altezza < ALTEZZA_MINIMA_ZONA:
        return None
    return BoxPixel(x=x, y=y, larghezza=larghezza, altezza=altezza)


def _rileva_zone_pixel(config: Config, canvas: Image.Image) -> tuple[list[BoxPixel], bool]:
    zone_rilevate = [_converti_zona(z) for z in rileva_zone_testo(config, canvas)[:2]]
    zone_valide = [z for z in zone_rilevate if z is not None]

    if not zone_valide:
        zona, verificata = _zona_default()
        return [zona], verificata

    zone_valide.sort(key=lambda z: z.y)  # ordine di lettura dall'alto verso il basso
    return zone_valide, True


def _disegna_testo(canvas: Image.Image, slide: SlideCompleta, zone: list[BoxPixel], verificata: bool) -> None:
    candidati = CANDIDATI_DIMENSIONE_HOOK if slide.fase_narrativa == "hook" else CANDIDATI_DIMENSIONE
    alpha_velo = ALPHA_VELO_ZONA_RILEVATA if verificata else ALPHA_VELO_FALLBACK

    if len(zone) == 1:
        gruppi: list[list[Segmento]] = [slide.segmenti]
    else:
        font_prova, righe_prova = _adatta_testo_a_zona(slide.segmenti, zone[0], candidati)
        if altezza_blocco(font_prova, righe_prova) <= zone[0].altezza:
            gruppi = [slide.segmenti]
            zone = [zone[0]]
        else:
            area0 = zone[0].larghezza * zone[0].altezza
            area1 = zone[1].larghezza * zone[1].altezza
            gruppi = list(_dividi_segmenti(slide.segmenti, area0, area1))

    for blocco_segmenti, box in zip(gruppi, zone):
        if not blocco_segmenti:
            continue
        font, righe = _adatta_testo_a_zona(blocco_segmenti, box, candidati)
        altezza = altezza_blocco(font, righe)
        _disegna_velo(canvas, box.y - 24, box.y + altezza + 24, alpha_velo)
        _disegna_righe(canvas, righe, font, box.x, box.y)


def _incolla_logo(canvas: Image.Image) -> None:
    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_adattato = ImageOps.contain(logo, (ZONA_LOGO_LATO, ZONA_LOGO_LATO))

    box_x = LARGHEZZA_SLIDE - MARGINE_SICUREZZA - ZONA_LOGO_LATO
    box_y = ALTEZZA_SLIDE - MARGINE_SICUREZZA - ZONA_LOGO_LATO
    offset_x = box_x + (ZONA_LOGO_LATO - logo_adattato.width) // 2
    offset_y = box_y + (ZONA_LOGO_LATO - logo_adattato.height) // 2

    canvas.paste(logo_adattato, (offset_x, offset_y), logo_adattato)


# Layout "foto": la foto è una stampa appoggiata su un foglio blueprint quadrettato
# (sfondo navy del design system), con il testo in alto sopra la griglia.
PADDING_FASCIA = 40
FRAZIONE_FASCIA_MINIMA = 0.22
FRAZIONE_FASCIA_MASSIMA = 0.55
PASSO_GRIGLIA = 36
LINEE_PER_QUADRO = 5  # una linea più marcata ogni 5 quadretti, come nella carta millimetrata
COLORE_GRIGLIA = (0x3A, 0x40, 0x5E)
COLORE_GRIGLIA_MARCATA = (0x4A, 0x50, 0x80)
BORDO_STAMPA = 12
LATO_SEGNO_ANGOLO = 34
DISTANZA_SEGNO_ANGOLO = 16


def _griglia_blueprint(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    for indice, x in enumerate(range(0, LARGHEZZA_SLIDE + 1, PASSO_GRIGLIA)):
        colore = COLORE_GRIGLIA_MARCATA if indice % LINEE_PER_QUADRO == 0 else COLORE_GRIGLIA
        draw.line([(x, 0), (x, ALTEZZA_SLIDE)], fill=colore, width=1)
    for indice, y in enumerate(range(0, ALTEZZA_SLIDE + 1, PASSO_GRIGLIA)):
        colore = COLORE_GRIGLIA_MARCATA if indice % LINEE_PER_QUADRO == 0 else COLORE_GRIGLIA
        draw.line([(0, y), (LARGHEZZA_SLIDE, y)], fill=colore, width=1)


def _segni_angolo(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int) -> None:
    """Marcature a L in rosso attorno agli angoli della stampa, come i segni di
    registro di un disegno tecnico."""
    d, l = DISTANZA_SEGNO_ANGOLO, LATO_SEGNO_ANGOLO
    for x, y, sx, sy in ((x0 - d, y0 - d, 1, 1), (x1 + d, y0 - d, -1, 1), (x0 - d, y1 + d, 1, -1), (x1 + d, y1 + d, -1, -1)):
        draw.line([(x, y), (x + sx * l, y)], fill=ACCENTO_ROSSO, width=4)
        draw.line([(x, y), (x, y + sy * l)], fill=ACCENTO_ROSSO, width=4)


def _componi_foto(sfondo: Image.Image, slide: SlideCompleta) -> Image.Image:
    """Le foto reali non hanno uno spazio vuoto dove scrivere: il testo va in alto
    sulla griglia blueprint e la foto sotto, come una stampa con bordo bianco e
    ombra, dentro i margini della slide."""
    candidati = CANDIDATI_DIMENSIONE_HOOK if slide.fase_narrativa == "hook" else CANDIDATI_DIMENSIONE
    larghezza_testo = LARGHEZZA_SLIDE - 2 * MARGINE_SICUREZZA
    altezza_massima_testo = int(ALTEZZA_SLIDE * FRAZIONE_FASCIA_MASSIMA) - MARGINE_SICUREZZA - PADDING_FASCIA
    box = BoxPixel(x=MARGINE_SICUREZZA, y=MARGINE_SICUREZZA, larghezza=larghezza_testo, altezza=altezza_massima_testo)
    font, righe = _adatta_testo_a_zona(slide.segmenti, box, candidati)

    fascia = int(MARGINE_SICUREZZA + altezza_blocco(font, righe) + PADDING_FASCIA)
    fascia = max(int(ALTEZZA_SLIDE * FRAZIONE_FASCIA_MINIMA), min(fascia, int(ALTEZZA_SLIDE * FRAZIONE_FASCIA_MASSIMA)))

    canvas = Image.new("RGB", (LARGHEZZA_SLIDE, ALTEZZA_SLIDE), NAVY)
    _griglia_blueprint(canvas)

    # Stampa: foto + bordo bianco, dentro i margini, con un'ombra morbida sotto.
    x0 = MARGINE_SICUREZZA
    x1 = LARGHEZZA_SLIDE - MARGINE_SICUREZZA
    y0 = fascia + DISTANZA_SEGNO_ANGOLO
    # La stampa (segni d'angolo compresi) finisce sopra il logo: sul bordo bianco o
    # su una foto chiara il logo bianco non si leggerebbe.
    y1 = _cima_logo() - 2 * DISTANZA_SEGNO_ANGOLO
    ombra = Image.new("L", (LARGHEZZA_SLIDE, ALTEZZA_SLIDE), 0)
    ImageDraw.Draw(ombra).rectangle([x0 + 10, y0 + 18, x1 + 10, y1 + 18], fill=150)
    ombra = ombra.filter(ImageFilter.GaussianBlur(18))
    canvas.paste(Image.new("RGB", canvas.size, (8, 10, 20)), (0, 0), ombra)

    ImageDraw.Draw(canvas).rectangle([x0, y0, x1, y1], fill=BIANCO)
    foto = cover_crop(sfondo, x1 - x0 - 2 * BORDO_STAMPA, y1 - y0 - 2 * BORDO_STAMPA)
    canvas.paste(foto, (x0 + BORDO_STAMPA, y0 + BORDO_STAMPA))
    _segni_angolo(ImageDraw.Draw(canvas), x0, y0, x1, y1)

    _disegna_righe(canvas, righe, font, MARGINE_SICUREZZA, MARGINE_SICUREZZA)
    return canvas


def _cima_logo() -> int:
    """Y del bordo superiore del logo, così come lo posiziona _incolla_logo."""
    logo = ImageOps.contain(Image.open(LOGO_PATH), (ZONA_LOGO_LATO, ZONA_LOGO_LATO))
    return ALTEZZA_SLIDE - MARGINE_SICUREZZA - ZONA_LOGO_LATO + (ZONA_LOGO_LATO - logo.height) // 2


def componi_slide(config: Config, percorso_sfondo: Path, slide: SlideCompleta) -> Image.Image:
    """Compone una singola slide: sfondo (cover-crop a 1080x1350) + testo + logo.

    Il prompt Nano Banana (src/image_prompts.py) chiede già il formato finale
    4:5 (1080x1350), ma senza garanzia che venga rispettato alla lettera: il
    cover-crop centrato qui sotto è una rete di sicurezza che adatta qualunque
    proporzione lo sfondo generato abbia davvero, senza mai deformarlo.

    Prima di scrivere il testo, lo sfondo viene analizzato con Gemini vision
    (layout_vision.rileva_zone_testo) per trovare le aree libere
    dall'illustrazione: il testo viene posizionato lì, diviso su due zone se
    necessario, invece che in una posizione fissa che potrebbe sovrapporsi
    alla grafica generata.
    """
    sfondo = Image.open(percorso_sfondo).convert("RGB")
    if slide.tipo_sfondo == "foto":
        canvas = _componi_foto(sfondo, slide)
    else:
        canvas = cover_crop(sfondo, LARGHEZZA_SLIDE, ALTEZZA_SLIDE)
        zone, verificata = _rileva_zone_pixel(config, canvas)
        _disegna_testo(canvas, slide, zone, verificata)
    _incolla_logo(canvas)

    return canvas
