"""CLI del generatore caroselli Instagram SEDS UPO.

Due comandi, con la pausa umana per la generazione manuale delle immagini nel mezzo:

    python -m src.main genera-prompt --input testo.txt --nome nome_carosello
    ... genera manualmente le immagini con Nano Banana e salvale in input_immagini/<nome>/ ...
    python -m src.main componi --nome nome_carosello
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compose import componi_slide
from .config import OUTPUT_DIR, PROMPTS_DIR, carica_config
from .image_prompts import costruisci_carosello_completo, salva_output
from .models import CarosalloCompleto
from .qa_vision import valuta_slide
from .text_analysis import analizza_testo
from .validate_images import ImmaginiMancantiError, valida_immagini_presenti


def _comando_genera_prompt(args: argparse.Namespace) -> None:
    config = carica_config()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"ERRORE: file di input non trovato: {input_path}", file=sys.stderr)
        sys.exit(1)

    testo_grezzo = input_path.read_text(encoding="utf-8")
    if not testo_grezzo.strip():
        print(f"ERRORE: file di input vuoto: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Analisi del testo con Gemini ({' -> '.join(config.modelli_testo)})...")
    analisi = analizza_testo(config, testo_grezzo)
    print(f"  -> {len(analisi.slides)} slide generate.")

    carosello = costruisci_carosello_completo(args.nome, analisi)
    path_json, path_txt = salva_output(carosello)

    print(f"\nSalvato: {path_json}")
    print(f"Salvato: {path_txt}")
    print(
        f"\nProssimo passo: genera manualmente le {carosello.numero_slide} immagini con "
        f"Nano Banana usando i prompt in {path_txt}, e salvale come:\n"
        f"  input_immagini/{carosello.nome_carosello}/01.png\n"
        f"  input_immagini/{carosello.nome_carosello}/02.png\n"
        "  ...\n"
        f"Poi lancia: python -m src.main componi --nome {carosello.nome_carosello}"
    )


def _chiedi_azione_slide_scarsa(numero: int, qa) -> str:
    print(
        f"\n  ATTENZIONE slide {numero:02d}: punteggio medio {qa.punteggio_medio():.1f}/10 "
        "(sotto soglia)"
    )
    print(
        f"    leggibilita_testo={qa.leggibilita_testo} contrasto={qa.contrasto} "
        f"bilanciamento_visivo={qa.bilanciamento_visivo} "
        f"aderenza_design_system={qa.aderenza_design_system} "
        f"qualita_estetica_generale={qa.qualita_estetica_generale}"
    )
    print(f"    Commento Gemini: {qa.commento}")
    while True:
        risposta = input(
            "    Vuoi (r)icaricare una nuova immagine per questa slide o (p)rocedere "
            "comunque? [p]: "
        ).strip().lower()
        if risposta in ("", "p", "procedi"):
            return "procedi"
        if risposta in ("r", "ricarica"):
            return "ricarica"
        print("    Risposta non valida, digita 'r' o 'p'.")


def _comando_componi(args: argparse.Namespace) -> None:
    config = carica_config()

    path_json = PROMPTS_DIR / args.nome / "slides.json"
    if not path_json.is_file():
        print(
            f"ERRORE: {path_json} non trovato. Esegui prima 'genera-prompt --nome {args.nome}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    carosello = CarosalloCompleto.model_validate_json(path_json.read_text(encoding="utf-8"))

    try:
        cartella_input = valida_immagini_presenti(carosello)
    except ImmaginiMancantiError as e:
        print(f"ERRORE: {e}", file=sys.stderr)
        sys.exit(1)

    cartella_output = OUTPUT_DIR / carosello.nome_carosello
    cartella_output.mkdir(parents=True, exist_ok=True)

    log_qa = []
    slide_da_ricaricare = []

    for slide in carosello.slides:
        percorso_sfondo = cartella_input / f"{slide.numero:02d}.png"
        print(f"Compongo slide {slide.numero:02d}...")
        immagine = componi_slide(config, percorso_sfondo, slide)

        print(f"  QA vision ({' -> '.join(config.modelli_vision)})...")
        qa = valuta_slide(config, immagine, slide.numero)
        media = qa.punteggio_medio()

        azione = "ok"
        if media < config.soglia_qualita:
            azione = _chiedi_azione_slide_scarsa(slide.numero, qa)

        if azione == "ricarica":
            slide_da_ricaricare.append(slide.numero)
        else:
            percorso_output = cartella_output / f"{slide.numero:02d}.png"
            immagine.save(percorso_output)
            print(f"  Salvata: {percorso_output} (punteggio medio {media:.1f}/10)")

        log_qa.append({**qa.model_dump(), "punteggio_medio": round(media, 2), "azione": azione})

    path_log = cartella_output / "qa_log.json"
    path_log.write_text(json.dumps(log_qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLog QA salvato: {path_log}")

    if slide_da_ricaricare:
        numeri = ", ".join(f"{n:02d}" for n in slide_da_ricaricare)
        print(
            f"\n{len(slide_da_ricaricare)} slide non salvate in output (da ricaricare): "
            f"{numeri}\n"
            f"Sostituisci le immagini in input_immagini/{carosello.nome_carosello}/ e "
            f"rilancia 'componi --nome {carosello.nome_carosello}'."
        )
    else:
        print(f"\nCarosello completo in: {cartella_output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generatore caroselli Instagram SEDS UPO")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)

    p_genera = sottocomandi.add_parser(
        "genera-prompt", help="Analizza il testo grezzo e genera i prompt immagine"
    )
    p_genera.add_argument("--input", required=True, help="File di testo grezzo della ricerca")
    p_genera.add_argument("--nome", required=True, help="Nome del carosello")
    p_genera.set_defaults(func=_comando_genera_prompt)

    p_componi = sottocomandi.add_parser(
        "componi", help="Compone le slide finali dopo aver caricato le immagini manuali"
    )
    p_componi.add_argument("--nome", required=True, help="Nome del carosello")
    p_componi.set_defaults(func=_comando_componi)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
