"""Route Flask per il carosello notizie: creazione, aggiunta slide da URL o
scelte tra le ultime notizie dei feed RSS delle testate,
upload/sostituzione foto, composizione (senza QA vision: layout fisso, non
serve un giudizio estetico), caption unica del carosello, eliminazione.

Blueprint separato da app.py perché il modello di dati e il flusso sono
diversi dal carosello scientifico (una slide = una notizia indipendente, non
una fase di un arco narrativo unico), ma senza duplicare logica: tutta la
business logic sta in src/, qui solo orchestrazione.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from PIL import Image, UnidentifiedImageError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.caption import formatta_caption, genera_caption_notizie
from src.compose_notizie import componi_slide_notizia
from src.config import (
    INPUT_IMMAGINI_NOTIZIE_DIR,
    NOTIZIE_DIR,
    OUTPUT_NOTIZIE_DIR,
    carica_config_opzionale,
)
from src import lavori
from src.models import CarosalloNotizie
from src.notizie_analisi import analizza_da_testo, analizza_notizia
from src.notizie_feed import notizie_in_cache, ultime_notizie
from src.og_image import scarica_immagine_suggerita
from src.segmenti_markup import markup_da_segmenti, segmenti_da_markup
from webapp.utils import NOME_PATTERN, slugify

bp = Blueprint("notizie", __name__)


# ---------------------------------------------------------------------------
# Helper condivisi
# ---------------------------------------------------------------------------


def _carica_carosello(nome: str) -> CarosalloNotizie | None:
    path_dati = NOTIZIE_DIR / nome / "dati.json"
    if not path_dati.is_file():
        return None
    return CarosalloNotizie.model_validate_json(path_dati.read_text(encoding="utf-8"))


def _salva_carosello(carosello: CarosalloNotizie) -> None:
    cartella = NOTIZIE_DIR / carosello.nome_carosello
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "dati.json").write_text(
        json.dumps(carosello.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def elenca_caroselli() -> list[dict]:
    risultati = []
    if not NOTIZIE_DIR.is_dir():
        return risultati
    for cartella in sorted(NOTIZIE_DIR.iterdir(), reverse=True):
        if not (cartella / "dati.json").is_file():
            continue
        try:
            carosello = _carica_carosello(cartella.name)
        except ValueError:  # dati.json illeggibile: si salta invece di bloccare la home
            continue
        if carosello is None:
            continue
        cartella_output = OUTPUT_NOTIZIE_DIR / carosello.nome_carosello
        composte = sum(
            1 for s in carosello.slides if (cartella_output / f"{s.numero:02d}.png").is_file()
        )
        risultati.append(
            {
                "nome": carosello.nome_carosello,
                "numero_notizie": len(carosello.slides),
                "composte": composte,
            }
        )
    return risultati


# ---------------------------------------------------------------------------
# Creazione + eliminazione carosello
# ---------------------------------------------------------------------------


@bp.route("/notizie/nuovo", methods=["POST"])
def nuovo_carosello():
    titolo = request.form.get("nome", "").strip()
    nome = slugify(titolo)

    if not NOME_PATTERN.match(nome):
        flash("Inserisci un titolo per il carosello notizie.", "errore")
        return redirect(url_for("home"))
    if (NOTIZIE_DIR / nome / "dati.json").is_file():
        flash(f"Esiste già un carosello notizie chiamato '{nome}'.", "errore")
        return redirect(url_for("home"))

    _salva_carosello(CarosalloNotizie(nome_carosello=nome, slides=[]))
    flash(f"Carosello notizie '{nome}' creato. Scegli le notizie dai feed o aggiungile da un URL.", "successo")
    return redirect(url_for("notizie.dettaglio", nome=nome))


@bp.route("/api/notizie/<nome>", methods=["DELETE"])
def elimina_carosello(nome):
    if not NOME_PATTERN.match(nome):
        return jsonify(ok=False, messaggio="Nome carosello non valido."), 400

    trovato = False
    for cartella in (NOTIZIE_DIR / nome, INPUT_IMMAGINI_NOTIZIE_DIR / nome, OUTPUT_NOTIZIE_DIR / nome):
        if cartella.is_dir():
            shutil.rmtree(cartella)
            trovato = True

    if not trovato:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Dettaglio carosello + aggiunta/modifica/eliminazione notizie
# ---------------------------------------------------------------------------


@bp.route("/notizie/<nome>")
def dettaglio(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        flash("Carosello notizie non trovato.", "errore")
        return redirect(url_for("home"))

    cartella_input = INPUT_IMMAGINI_NOTIZIE_DIR / nome
    cartella_output = OUTPUT_NOTIZIE_DIR / nome
    config = carica_config_opzionale()

    slides_view = []
    for slide in carosello.slides:
        slides_view.append(
            {
                "slide": slide,
                "immagine_presente": (cartella_input / f"{slide.numero:02d}.png").is_file(),
                "composta": (cartella_output / f"{slide.numero:02d}.png").is_file(),
                "testo_markup": markup_da_segmenti(slide.segmenti),
            }
        )

    path_caption = cartella_output / "caption.txt"
    caption_esistente = path_caption.read_text(encoding="utf-8") if path_caption.is_file() else None

    return render_template(
        "carosello_notizie.html",
        carosello=carosello,
        slides_view=slides_view,
        caption_esistente=caption_esistente,
        limite_caption=config.max_caratteri_caption if config else 1600,
        limite_titolo=config.max_caratteri_notizia if config else 90,
    )


@bp.route("/api/notizie/<nome>/aggiungi", methods=["POST"])
def aggiungi_notizia(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404

    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(ok=False, messaggio="Inserisci l'URL della notizia."), 400

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    numero = max((s.numero for s in carosello.slides), default=0) + 1

    try:
        slide = analizza_notizia(config, url, numero)
    except ValueError as e:
        return jsonify(ok=False, messaggio=str(e)), 400
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Errore nell'analisi della notizia: {e}"), 500

    carosello.slides.append(slide)
    _salva_carosello(carosello)

    immagine_suggerita = False
    foto = scarica_immagine_suggerita(url)
    if foto is not None:
        cartella_input = INPUT_IMMAGINI_NOTIZIE_DIR / nome
        cartella_input.mkdir(parents=True, exist_ok=True)
        foto.save(cartella_input / f"{numero:02d}.png")
        immagine_suggerita = True

    return jsonify(ok=True, numero=numero, immagine_suggerita=immagine_suggerita)


# ---------------------------------------------------------------------------
# Notizie dai feed RSS
# ---------------------------------------------------------------------------

CHIAVE_LAVORO_FEED = "feed-lettura"


@bp.route("/api/feed/carica", methods=["POST"])
def carica_feed():
    """Legge i feed in background (con un centinaio di testate servono decine di
    secondi); l'elenco si prende poi da /api/feed/notizie."""
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400
    forza = request.form.get("forza") == "1"

    def lavoro(aggiorna):
        aggiorna("Lettura dei feed delle testate", 0.1)
        elenco = ultime_notizie(config, forza=forza)
        return f"{len(elenco.voci)} notizie"

    try:
        lavori.avvia(CHIAVE_LAVORO_FEED, lavoro)
    except lavori.LavoroGiaInCorso:
        pass  # una lettura è già in corso: basta seguire quella
    return jsonify(ok=True, chiave=CHIAVE_LAVORO_FEED)


@bp.route("/api/feed/notizie")
def notizie_feed():
    elenco = notizie_in_cache()
    if elenco is None:
        return jsonify(ok=False, messaggio="Elenco non ancora caricato."), 404
    return jsonify(
        ok=True,
        notizie=[v.model_dump() for v in elenco.voci],
        errori=elenco.errori,
        totale_lette=elenco.totale_lette,
        numero_feed=elenco.numero_feed,
        letto_il=elenco.letto_il,
    )


@bp.route("/api/notizie/<nome>/aggiungi-da-feed", methods=["POST"])
def aggiungi_da_feed(nome):
    """Aggiunge in blocco le notizie scelte dall'elenco, in background: ogni
    notizia è una lettura della pagina con Gemini più la ricerca della foto."""
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    selezionate = [v for v in (request.get_json(silent=True) or {}).get("notizie") or [] if (v.get("url") or "").startswith("http")]
    if not selezionate:
        return jsonify(ok=False, messaggio="Nessuna notizia selezionata."), 400

    def lavoro(aggiorna):
        aggiunte, saltate, errori = 0, 0, []
        for indice, voce in enumerate(selezionate):
            titolo = voce.get("titolo_tradotto") or voce.get("titolo") or voce["url"]
            aggiorna(f"Notizia {indice + 1} di {len(selezionate)}: {titolo[:60]}", indice / len(selezionate))
            attuale = _carica_carosello(nome)
            if attuale is None:
                raise RuntimeError("Il carosello è stato eliminato.")
            if any(s.url == voce["url"] for s in attuale.slides):
                saltate += 1
                continue
            numero = max((s.numero for s in attuale.slides), default=0) + 1
            try:
                slide = analizza_notizia(config, voce["url"], numero)
            except Exception:  # noqa: BLE001 - pagina non leggibile: si ripiega sul testo del feed
                try:
                    slide = analizza_da_testo(
                        config, voce["url"], numero, voce.get("titolo") or "", voce.get("riassunto_grezzo") or ""
                    )
                except Exception as e:  # noqa: BLE001 - errore Gemini: questa notizia si salta
                    errori.append(f"{titolo[:60]}: {e}")
                    continue

            attuale.slides.append(slide)
            _salva_carosello(attuale)
            foto = scarica_immagine_suggerita(voce["url"], voce.get("url_immagine"))
            if foto is not None:
                cartella_input = INPUT_IMMAGINI_NOTIZIE_DIR / nome
                cartella_input.mkdir(parents=True, exist_ok=True)
                foto.save(cartella_input / f"{numero:02d}.png")
            aggiunte += 1

        messaggio = f"Aggiunte {aggiunte} notizie."
        if saltate:
            messaggio += f" {saltate} già presenti."
        if errori:
            messaggio += " Non aggiunte: " + "; ".join(errori)
        if not aggiunte and errori:
            raise RuntimeError(messaggio)
        return messaggio

    chiave = f"notizie-aggiungi:{nome}"
    try:
        lavori.avvia(chiave, lavoro)
    except lavori.LavoroGiaInCorso as e:
        return jsonify(ok=False, messaggio=str(e)), 409
    return jsonify(ok=True, chiave=chiave)


@bp.route("/api/notizie/<nome>/slide/<int:numero>/immagine", methods=["POST"])
def carica_immagine(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None or not any(s.numero == numero for s in carosello.slides):
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    file = request.files.get("immagine")
    if file is None or file.filename == "":
        return jsonify(ok=False, messaggio="Nessun file selezionato."), 400

    try:
        immagine = Image.open(file.stream)
        immagine.load()
        immagine = immagine.convert("RGB")
    except UnidentifiedImageError:
        return jsonify(ok=False, messaggio="Il file caricato non è un'immagine valida."), 400

    cartella = INPUT_IMMAGINI_NOTIZIE_DIR / nome
    cartella.mkdir(parents=True, exist_ok=True)
    immagine.save(cartella / f"{numero:02d}.png")

    return jsonify(ok=True, url=url_for("notizie.file_input", nome=nome, numero=numero))


@bp.route("/api/notizie/<nome>/slide/<int:numero>/testo", methods=["POST"])
def modifica_testo(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    slide = next((s for s in carosello.slides if s.numero == numero), None)
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    config = carica_config_opzionale()
    limite = config.max_caratteri_notizia if config else 90

    testo_markup = request.form.get("testo", "").strip()
    if not testo_markup:
        return jsonify(ok=False, messaggio="Il titolo non può essere vuoto."), 400

    nuovi_segmenti = segmenti_da_markup(testo_markup)
    testo_piatto = "".join(s.testo for s in nuovi_segmenti)
    if len(testo_piatto) > limite:
        return (
            jsonify(
                ok=False,
                messaggio=f"Titolo di {len(testo_piatto)} caratteri, oltre il limite di {limite}.",
            ),
            400,
        )

    slide.segmenti = nuovi_segmenti
    _salva_carosello(carosello)

    cartella_output = OUTPUT_NOTIZIE_DIR / nome
    percorso_output = cartella_output / f"{numero:02d}.png"
    if percorso_output.is_file():
        percorso_output.unlink()

    return jsonify(ok=True, testo=testo_piatto, lunghezza=len(testo_piatto), limite=limite)


@bp.route("/api/notizie/<nome>/slide/<int:numero>/elimina", methods=["DELETE"])
def elimina_slide(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404

    slide = next((s for s in carosello.slides if s.numero == numero), None)
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    carosello.slides = [s for s in carosello.slides if s.numero != numero]
    _salva_carosello(carosello)

    for cartella in (INPUT_IMMAGINI_NOTIZIE_DIR / nome, OUTPUT_NOTIZIE_DIR / nome):
        percorso = cartella / f"{numero:02d}.png"
        if percorso.is_file():
            percorso.unlink()

    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# File statici (foto caricate / slide composte)
# ---------------------------------------------------------------------------


@bp.route("/file/notizie/input/<nome>/<int:numero>.png")
def file_input(nome, numero):
    return send_from_directory(INPUT_IMMAGINI_NOTIZIE_DIR / nome, f"{numero:02d}.png")


@bp.route("/file/notizie/output/<nome>/<int:numero>.png")
def file_output(nome, numero):
    return send_from_directory(OUTPUT_NOTIZIE_DIR / nome, f"{numero:02d}.png")


# ---------------------------------------------------------------------------
# Composizione (nessuna QA vision: layout fisso, non serve giudizio estetico)
# ---------------------------------------------------------------------------


def _componi(cartella_input: Path, cartella_output: Path, slide) -> None:
    percorso_foto = cartella_input / f"{slide.numero:02d}.png"
    immagine = componi_slide_notizia(percorso_foto, slide)
    cartella_output.mkdir(parents=True, exist_ok=True)
    immagine.save(cartella_output / f"{slide.numero:02d}.png")


@bp.route("/api/notizie/<nome>/componi", methods=["POST"])
def componi_tutte(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404

    cartella_input = INPUT_IMMAGINI_NOTIZIE_DIR / nome
    cartella_output = OUTPUT_NOTIZIE_DIR / nome

    composte, saltate = 0, 0
    for slide in carosello.slides:
        if not (cartella_input / f"{slide.numero:02d}.png").is_file():
            saltate += 1
            continue
        _componi(cartella_input, cartella_output, slide)
        composte += 1

    return jsonify(ok=True, composte=composte, saltate=saltate)


@bp.route("/api/notizie/<nome>/slide/<int:numero>/ricomponi", methods=["POST"])
def ricomponi_slide(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    slide = next((s for s in carosello.slides if s.numero == numero), None)
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    cartella_input = INPUT_IMMAGINI_NOTIZIE_DIR / nome
    if not (cartella_input / f"{numero:02d}.png").is_file():
        return jsonify(ok=False, messaggio="Foto mancante per questa slide."), 400

    _componi(cartella_input, OUTPUT_NOTIZIE_DIR / nome, slide)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Caption (una sola per l'intero carosello) + download
# ---------------------------------------------------------------------------


@bp.route("/api/notizie/<nome>/caption", methods=["POST"])
def genera_caption_route(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    if not carosello.slides:
        return jsonify(ok=False, messaggio="Aggiungi almeno una notizia prima di generare la caption."), 400

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    nome_autore = request.form.get("autore", "")

    try:
        caption = genera_caption_notizie(config, carosello)
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Errore nella generazione della caption: {e}"), 500

    testo_finale = formatta_caption(caption, nome_autore)

    cartella_output = OUTPUT_NOTIZIE_DIR / nome
    cartella_output.mkdir(parents=True, exist_ok=True)
    (cartella_output / "caption.txt").write_text(testo_finale, encoding="utf-8")

    return jsonify(
        ok=True, caption=testo_finale, lunghezza=len(testo_finale), limite=config.max_caratteri_caption
    )


@bp.route("/notizie/<nome>/scarica")
def scarica(nome):
    cartella_output = OUTPUT_NOTIZIE_DIR / nome
    if not cartella_output.is_dir() or not any(cartella_output.glob("*.png")):
        flash("Nessun output da scaricare per questo carosello: componi prima le slide.", "errore")
        return redirect(url_for("notizie.dettaglio", nome=nome))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(cartella_output.glob("*.png")):
            zf.write(file, arcname=file.name)
        path_caption = cartella_output / "caption.txt"
        if path_caption.is_file():
            zf.write(path_caption, arcname="caption.txt")
    buffer.seek(0)

    return send_file(
        buffer, mimetype="application/zip", as_attachment=True, download_name=f"{nome}_notizie.zip"
    )
