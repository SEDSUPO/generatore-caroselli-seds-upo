"""Step 3: assembla i prompt Nano Banana e li salva (slides.json + prompts_immagini.txt).

Il Blocco di Stile Fisso e il template sono copiati LETTERALMENTE dalla Sezione 3 di
design_system.md: qui vengono trattati come costanti, mai generati o riformulati da Gemini,
in modo che restino identici in ogni carosello.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import PROMPTS_DIR
from .models import CarosalloAnalisi, CarosalloCompleto, SlideAnalisi, SlideCompleta
from .text_metrics import (
    ALTEZZA_SLIDE,
    CANDIDATI_DIMENSIONE,
    CANDIDATI_DIMENSIONE_HOOK,
    INTERLINEA,
    LARGHEZZA_SLIDE,
    MARGINE_SICUREZZA,
    altezza_blocco,
    stima_blocco_testo,
)

BLOCCO_STILE_FISSO = (
    "Style: fine line-art illustration, single-weight clean lines, minimal shading, "
    "technical blueprint aesthetic, precise and orderly, reminiscent of NASA/ESA technical "
    "diagrams, no readable text or labels anywhere in the image. Color palette: background "
    "solid dark navy #282d43, line art in coral red #aa2922 and white. Lighting: flat, no "
    "shadows, technical diagram style."
)

# Il canvas finale è 1080x1350 (4:5, Sezione 4): si chiede a Nano Banana di generare
# direttamente in questo formato, invece del 9:16 storico del template, per evitare lo
# spreco di pixel e la matematica di compensazione del cover-crop. Il cover-crop in
# src/compose.py resta comunque come rete di sicurezza per qualunque proporzione Nano
# Banana restituisca davvero (nessuna garanzia che rispetti il formato alla lettera).
_PADDING_INFERIORE_ZONA = 40
_PERCENTUALE_MINIMA = 25
_PERCENTUALE_MASSIMA = 60


def _percentuale_margine_vuoto(slide_analisi: SlideAnalisi) -> int:
    """Stima, in base alla quantità di testo della slide, quanta percentuale del
    frame 4:5 richiesto a Nano Banana va lasciata come sfondo vuoto per ospitare
    il testo sovrapposto.
    """
    candidati = CANDIDATI_DIMENSIONE_HOOK if slide_analisi.fase_narrativa == "hook" else CANDIDATI_DIMENSIONE
    larghezza_max = LARGHEZZA_SLIDE - 2 * MARGINE_SICUREZZA

    font, righe = stima_blocco_testo(slide_analisi.segmenti, candidati, larghezza_max)
    altezza_zona_finale = MARGINE_SICUREZZA + altezza_blocco(font, righe) + _PADDING_INFERIORE_ZONA

    percentuale = round(altezza_zona_finale / ALTEZZA_SLIDE * 100 / 5) * 5
    return max(_PERCENTUALE_MINIMA, min(percentuale, _PERCENTUALE_MASSIMA))


def _assembla_prompt(soggetto_immagine: str, percentuale_margine: int) -> str:
    return (
        f'Subject: a simple technical line-art illustration of "{soggetto_immagine}", '
        "clean and minimal, a single clear subject, no diagram labels or callouts.\n"
        "Composition: vertical format 4:5 (1080x1350), subject horizontally centered and "
        f"composed in the lower part of the frame, completely empty solid dark navy "
        f"background (no illustration elements, no linework, no texture) covering at least "
        f"the top {percentuale_margine}% of the frame for text overlay, subtle grid lines "
        "only in the area with the subject.\n"
        f"{BLOCCO_STILE_FISSO}\n"
        "Constraints: no text, no letters, no numbers, no labels, no watermark, no realistic "
        "textures, no photographic elements, no clutter."
    )


def costruisci_carosello_completo(nome_carosello: str, analisi: CarosalloAnalisi) -> CarosalloCompleto:
    slides = [
        SlideCompleta(
            numero=s.numero,
            fase_narrativa=s.fase_narrativa,
            segmenti=s.segmenti,
            soggetto_immagine=s.soggetto_immagine,
            prompt_immagine=_assembla_prompt(s.soggetto_immagine, _percentuale_margine_vuoto(s)),
        )
        for s in analisi.slides
    ]
    return CarosalloCompleto(
        nome_carosello=nome_carosello,
        argomento=analisi.argomento,
        numero_slide=len(slides),
        slides=slides,
    )


def salva_output(carosello: CarosalloCompleto) -> tuple[Path, Path]:
    cartella = PROMPTS_DIR / carosello.nome_carosello
    cartella.mkdir(parents=True, exist_ok=True)

    path_json = cartella / "slides.json"
    path_json.write_text(
        json.dumps(carosello.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    path_txt = cartella / "prompts_immagini.txt"
    righe = [
        f"CAROSELLO: {carosello.nome_carosello}",
        f"Argomento: {carosello.argomento}",
        f"Numero slide: {carosello.numero_slide}",
        "=" * 70,
        "",
    ]
    for slide in carosello.slides:
        righe.append(f"--- Slide {slide.numero:02d} [{slide.fase_narrativa}] ---")
        righe.append(f"Copy: {slide.testo_piatto()}")
        righe.append("")
        righe.append(slide.prompt_immagine)
        righe.append("")
        righe.append(
            f"Salva l'immagine generata come: input_immagini/{carosello.nome_carosello}/"
            f"{slide.numero:02d}.png"
        )
        righe.append("")
        righe.append("=" * 70)
        righe.append("")
    path_txt.write_text("\n".join(righe), encoding="utf-8")

    return path_json, path_txt
