"""App web locale per il generatore caroselli Instagram SEDS UPO.

Interfaccia grafica sulle stesse funzioni di src/ usate dalla CLI: nessuna
logica di business duplicata qui dentro, solo route Flask.

Avvio: python -m webapp.app  (oppure avvia_app.bat su Windows)
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
import threading
import time
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import set_key
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from PIL import Image, UnidentifiedImageError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aggiorna_app
from src.caption import formatta_caption, genera_caption
from src.compose import componi_slide
from src.config import (
    ENV_PATH,
    VERSIONE_APP,
    INPUT_IMMAGINI_DIR,
    OUTPUT_DIR,
    PROMPTS_DIR,
    aggiorna_lista_modelli_yaml,
    Feed,
    aggiorna_valore_yaml,
    carica_config_opzionale,
    carica_feeds,
    salva_feeds,
)
from src.gemini_client import chiama_con_retry, crea_client
from src import lavori, ytdlp_manager
from src.feed_scoperta import scopri_feed
from src.notizie_feed import svuota_cache as svuota_cache_notizie
from src.image_prompts import costruisci_carosello_completo, salva_output
from src.immagini_carosello import salva_immagine_pubblica, suggerisci_query, testo_crediti
from src.models import CarosalloCompleto, CreditoImmagine
from src.reel_media import ErroreMedia
from src.qa_vision import valuta_slide
from src.reel_trascrizione import MODELLI_DISPONIBILI as MODELLI_WHISPER
from src.reel_trascrizione import carica_modello, modello_scaricato
from src.reel_voce import carica_kokoro, kokoro_scaricato
from src.reel_topic import ISTRUZIONI as PROMPT_SUGGERISCI_TOPIC
from src.segmenti_markup import markup_da_segmenti, segmenti_da_markup
from src.text_analysis import analizza_testo
from src.validate_images import ImmaginiMancantiError, valida_immagini_presenti
from webapp import utils
from webapp.notizie import bp as notizie_bp
from webapp.notizie import elenca_caroselli as notizie_elenca_caroselli
from webapp.reel import bp as reel_bp
from webapp.reel import elenca_reel
from webapp.reel_produzione import bp as reel_prod_bp

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # app locale monoutente: basta un segreto per-processo
# Senza questo, in debug=False Flask mette in cache i template compilati: una modifica
# a un file .html non si vedrebbe finché non si riavvia il processo a mano.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.register_blueprint(notizie_bp)
app.register_blueprint(reel_bp)
app.register_blueprint(reel_prod_bp)

NOME_PATTERN = utils.NOME_PATTERN
_slugify = utils.slugify


@app.before_request
def _richiedi_api_key():
    endpoint_liberi = {
        "impostazioni", "salva_impostazioni", "verifica_chiave", "static",
        "controlla_aggiornamento", "installa_aggiornamento", "versione_in_esecuzione",
    }
    if request.endpoint in endpoint_liberi:
        return
    if carica_config_opzionale() is None:
        flash("Configura la tua GOOGLE_API_KEY per iniziare a usare l'app.", "avviso")
        return redirect(url_for("impostazioni"))


# ---------------------------------------------------------------------------
# Impostazioni (API key)
# ---------------------------------------------------------------------------


@app.route("/impostazioni", methods=["GET"])
def impostazioni():
    config = carica_config_opzionale()
    return render_template(
        "impostazioni.html",
        configurata=config is not None,
        modelli_testo=config.modelli_testo if config else [],
        modelli_vision=config.modelli_vision if config else [],
        modelli_tts=config.modelli_tts if config else [],
        kokoro_scaricato=kokoro_scaricato(),
        feeds=carica_feeds(),
        config=config,
        modelli_whisper=MODELLI_WHISPER,
        whisper_scaricato=modello_scaricato(config.whisper_modello) if config else False,
        versione=VERSIONE_APP,
    )


@app.route("/impostazioni", methods=["POST"])
def salva_impostazioni():
    chiave = request.form.get("api_key", "").strip()
    if not chiave:
        flash("Inserisci una API key valida.", "errore")
        return redirect(url_for("impostazioni"))

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "GOOGLE_API_KEY", chiave)

    flash("API key salvata.", "successo")
    return redirect(url_for("home") if carica_config_opzionale() else url_for("impostazioni"))


@app.route("/impostazioni/verifica", methods=["POST"])
def verifica_chiave():
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="Nessuna chiave salvata.")
    client = crea_client(config)

    def _chiamata(modello: str):
        return modello, client.models.generate_content(model=modello, contents="Rispondi solo con: ok")

    try:
        modello_riuscito, _ = chiama_con_retry(config, _chiamata, config.modelli_testo)
        return jsonify(ok=True, messaggio=f"Connessione a Gemini riuscita (modello: {modello_riuscito}).")
    except Exception as e:  # noqa: BLE001 - vogliamo mostrare qualunque errore all'utente
        return jsonify(ok=False, messaggio=f"Connessione fallita su tutti i modelli configurati: {e}")


_CHIAVE_YAML_PER_TIPO = {"testo": "modelli_testo", "vision": "modelli_vision", "tts": "modelli_tts"}


def _lista_modelli_attuale(config, tipo: str) -> list[str]:
    return getattr(config, _CHIAVE_YAML_PER_TIPO[tipo])


@app.route("/impostazioni/modelli/<tipo>/aggiungi", methods=["POST"])
def aggiungi_modello(tipo):
    if tipo not in _CHIAVE_YAML_PER_TIPO:
        return jsonify(ok=False, messaggio="Tipo non valido."), 400

    nome_modello = request.form.get("modello", "").strip()
    if not nome_modello:
        return jsonify(ok=False, messaggio="Inserisci il nome di un modello."), 400

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    modelli_attuali = _lista_modelli_attuale(config, tipo)
    if nome_modello in modelli_attuali:
        return jsonify(ok=False, messaggio="Questo modello è già nella lista."), 400

    try:
        # Il client va tenuto in una variabile: se lo si chiama e scarta nella stessa
        # espressione, il garbage collector può chiuderlo (chiude l'httpx.Client
        # sottostante) prima ancora che la richiesta HTTP sia completata.
        client = crea_client(config)
        client.models.get(model=nome_modello)
    except Exception as e:  # noqa: BLE001 - qualunque errore = modello non valido/accessibile
        return jsonify(ok=False, messaggio=f"Modello non trovato o non accessibile con la tua chiave: {e}"), 400

    nuova_lista = modelli_attuali + [nome_modello]
    aggiorna_lista_modelli_yaml(_CHIAVE_YAML_PER_TIPO[tipo], nuova_lista)
    return jsonify(ok=True, modelli=nuova_lista)


@app.route("/impostazioni/modelli/<tipo>/rimuovi", methods=["POST"])
def rimuovi_modello(tipo):
    if tipo not in _CHIAVE_YAML_PER_TIPO:
        return jsonify(ok=False, messaggio="Tipo non valido."), 400

    nome_modello = request.form.get("modello", "").strip()
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    modelli_attuali = _lista_modelli_attuale(config, tipo)
    if len(modelli_attuali) <= 1:
        return jsonify(ok=False, messaggio="Non puoi rimuovere l'ultimo modello: la lista non può restare vuota."), 400

    nuova_lista = [m for m in modelli_attuali if m != nome_modello]
    if len(nuova_lista) == len(modelli_attuali):
        return jsonify(ok=False, messaggio="Modello non trovato nella lista."), 404

    aggiorna_lista_modelli_yaml(_CHIAVE_YAML_PER_TIPO[tipo], nuova_lista)
    return jsonify(ok=True, modelli=nuova_lista)


@app.route("/impostazioni/modelli/<tipo>/sposta", methods=["POST"])
def sposta_modello(tipo):
    if tipo not in _CHIAVE_YAML_PER_TIPO:
        return jsonify(ok=False, messaggio="Tipo non valido."), 400

    nome_modello = request.form.get("modello", "").strip()
    direzione = request.form.get("direzione", "").strip()
    if direzione not in ("su", "giu"):
        return jsonify(ok=False, messaggio="Direzione non valida."), 400

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    modelli_attuali = list(_lista_modelli_attuale(config, tipo))
    if nome_modello not in modelli_attuali:
        return jsonify(ok=False, messaggio="Modello non trovato nella lista."), 404

    indice = modelli_attuali.index(nome_modello)
    nuovo_indice = indice - 1 if direzione == "su" else indice + 1
    if not (0 <= nuovo_indice < len(modelli_attuali)):
        return jsonify(ok=True, modelli=modelli_attuali)  # già in cima/fondo, nessun cambiamento

    modelli_attuali[indice], modelli_attuali[nuovo_indice] = modelli_attuali[nuovo_indice], modelli_attuali[indice]
    aggiorna_lista_modelli_yaml(_CHIAVE_YAML_PER_TIPO[tipo], modelli_attuali)
    return jsonify(ok=True, modelli=modelli_attuali)


# ---------------------------------------------------------------------------
# Aggiornamento dell'app da GitHub
# ---------------------------------------------------------------------------

_cache_aggiornamento: dict = {}
DURATA_CACHE_AGGIORNAMENTO = 10 * 60  # GitHub concede 60 richieste l'ora senza login


@app.route("/impostazioni/aggiornamento/controlla")
def controlla_aggiornamento():
    attuale = aggiorna_app.versione_locale()
    if attuale == "sviluppo":
        return jsonify(ok=True, sviluppo=True, attuale=attuale)
    adesso = time.time()
    if request.args.get("forza") == "1" or adesso - _cache_aggiornamento.get("letto_il", 0) > DURATA_CACHE_AGGIORNAMENTO:
        try:
            _cache_aggiornamento.update(ultima=aggiorna_app.ultima_versione_github(), errore=None, letto_il=adesso)
        except aggiorna_app.ErroreAggiornamento as e:
            _cache_aggiornamento.update(ultima=None, errore=str(e), letto_il=adesso)
    if _cache_aggiornamento["errore"]:
        return jsonify(ok=False, attuale=attuale, messaggio=_cache_aggiornamento["errore"])
    ultima = _cache_aggiornamento["ultima"]
    return jsonify(
        ok=True,
        attuale=attuale,
        ultima=ultima["versione"],
        disponibile=aggiorna_app.e_piu_recente(ultima["versione"], attuale),
        note=ultima["note"],
        pagina=ultima["pagina"],
    )


@app.route("/impostazioni/aggiornamento/installa", methods=["POST"])
def installa_aggiornamento():
    """Avvia aggiorna_app.py in una finestra sua e chiude l'app: l'aggiornamento
    aspetta che l'app sia chiusa, sostituisce il codice e la riapre."""
    if aggiorna_app.versione_locale() == "sviluppo":
        return jsonify(ok=False, messaggio="Questa è la copia di sviluppo: si aggiorna con git, non da qui."), 400
    subprocess.Popen(
        [sys.executable, str(Path(aggiorna_app.__file__)), "--automatico", "--riavvia"],
        cwd=Path(aggiorna_app.__file__).parent,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    # Il tempo di mandare la risposta alla pagina, poi l'app si chiude (codice 0: anche
    # il processo che sorveglia i riavvii automatici termina, invece di rilanciarla).
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


@app.route("/impostazioni/aggiornamento/versione")
def versione_in_esecuzione():
    """Letta dal file a ogni richiesta: dopo il riavvio la pagina capisce che è cambiata."""
    return jsonify(ok=True, versione=aggiorna_app.versione_locale())


# ---------------------------------------------------------------------------
# Impostazioni reel: yt-dlp, Whisper, YouTube
# ---------------------------------------------------------------------------

CHIAVE_LAVORO_YTDLP = "ytdlp-aggiorna"


@app.route("/impostazioni/ytdlp/stato")
def stato_ytdlp():
    """Chiamata a parte (non nel rendering della pagina): interroga PyPI e lancia
    un sottoprocesso, qualche secondo che non deve rallentare l'apertura."""
    installata = ytdlp_manager.versione_installata()
    ultima = ytdlp_manager.ultima_versione()
    return jsonify(
        ok=True,
        installata=installata,
        ultima=ultima,
        aggiornata=bool(installata and ultima and ytdlp_manager.stessa_versione(installata, ultima)),
    )


@app.route("/impostazioni/ytdlp/aggiorna", methods=["POST"])
def aggiorna_ytdlp():
    def lavoro(aggiorna):
        aggiorna("Aggiornamento di yt-dlp in corso", 0.2)
        riuscito, messaggio = ytdlp_manager.aggiorna()
        if not riuscito:
            raise RuntimeError(messaggio)
        return messaggio

    try:
        lavori.avvia(CHIAVE_LAVORO_YTDLP, lavoro)
    except lavori.LavoroGiaInCorso as e:
        return jsonify(ok=False, messaggio=str(e)), 409
    return jsonify(ok=True, chiave=CHIAVE_LAVORO_YTDLP)


@app.route("/impostazioni/reel", methods=["POST"])
def salva_impostazioni_reel():
    whisper = request.form.get("whisper_modello", "")
    if whisper not in MODELLI_WHISPER:
        return jsonify(ok=False, messaggio="Modello Whisper non valido."), 400
    try:
        durata_massima = int(request.form.get("youtube_durata_massima_minuti", ""))
    except ValueError:
        return jsonify(ok=False, messaggio="Durata massima non valida."), 400
    if not 1 <= durata_massima <= 240:
        return jsonify(ok=False, messaggio="La durata massima deve essere tra 1 e 240 minuti."), 400
    automatico = request.form.get("ytdlp_aggiornamento_automatico") == "1"

    aggiorna_valore_yaml("whisper_modello", whisper)
    aggiorna_valore_yaml("youtube_durata_massima_minuti", str(durata_massima))
    aggiorna_valore_yaml("ytdlp_aggiornamento_automatico", "true" if automatico else "false")
    return jsonify(ok=True, messaggio="Impostazioni salvate.", whisper_scaricato=modello_scaricato(whisper))


_URL_NEL_TESTO = re.compile(r"https?://[^\s\"',<>]+")


@app.route("/impostazioni/feed/aggiungi", methods=["POST"])
def aggiungi_feed():
    """Uno o più indirizzi di siti (anche incollati come lista con virgole e
    virgolette): per ognuno si cerca il feed RSS in background."""
    indirizzi = list(dict.fromkeys(_URL_NEL_TESTO.findall(request.form.get("testo", ""))))
    if not indirizzi:
        return jsonify(ok=False, messaggio="Incolla almeno un indirizzo che inizi con http."), 400

    def lavoro(aggiorna):
        esistenti = carica_feeds()
        url_presenti = {f.url for f in esistenti}
        nuovi, doppi, falliti, impronte = [], 0, [], set()
        with ThreadPoolExecutor(max_workers=12) as pool:
            for indice, (indirizzo, trovato) in enumerate(zip(indirizzi, pool.map(scopri_feed, indirizzi))):
                aggiorna(f"Ricerca dei feed ({indice + 1}/{len(indirizzi)})", (indice + 1) / len(indirizzi))
                if trovato is None:
                    falliti.append(indirizzo)
                elif trovato.url in url_presenti or trovato.impronta in impronte:
                    doppi += 1
                else:
                    url_presenti.add(trovato.url)
                    impronte.add(trovato.impronta)
                    nome = re.sub(r"\s*[-|:]\s*(rss|feed|atom)([\s\w]*)$", "", trovato.titolo, flags=re.IGNORECASE).strip()
                    nuovi.append(Feed(nome=(nome or trovato.url.split("/")[2])[:80], url=trovato.url))
        salva_feeds(esistenti + nuovi)
        svuota_cache_notizie()
        messaggio = f"Feed aggiunti: {len(nuovi)}. Già presenti o doppioni: {doppi}. Senza feed: {len(falliti)}."
        if falliti:
            messaggio += "\nSenza feed RSS: " + ", ".join(falliti[:20]) + ("..." if len(falliti) > 20 else "")
        return messaggio

    try:
        lavori.avvia("feed-scoperta", lavoro)
    except lavori.LavoroGiaInCorso as e:
        return jsonify(ok=False, messaggio=str(e)), 409
    return jsonify(ok=True, chiave="feed-scoperta")


@app.route("/impostazioni/feed/rimuovi", methods=["POST"])
def rimuovi_feed():
    url = request.form.get("url", "")
    feeds = carica_feeds()
    rimasti = [f for f in feeds if f.url != url]
    if len(rimasti) == len(feeds):
        return jsonify(ok=False, messaggio="Feed non trovato."), 404
    salva_feeds(rimasti)
    svuota_cache_notizie()
    return jsonify(ok=True, numero=len(rimasti))


@app.route("/impostazioni/kokoro/scarica", methods=["POST"])
def scarica_kokoro():
    def lavoro(aggiorna):
        carica_kokoro(aggiorna)
        return "Voce gratuita pronta."

    try:
        lavori.avvia("kokoro-scarica", lavoro)
    except lavori.LavoroGiaInCorso as e:
        return jsonify(ok=False, messaggio=str(e)), 409
    return jsonify(ok=True, chiave="kokoro-scarica")


@app.route("/impostazioni/whisper/scarica", methods=["POST"])
def scarica_whisper():
    nome_modello = request.form.get("modello", "")
    if nome_modello not in MODELLI_WHISPER:
        return jsonify(ok=False, messaggio="Modello Whisper non valido."), 400
    chiave = f"whisper-scarica:{nome_modello}"

    def lavoro(aggiorna):
        aggiorna(f"Download del modello {nome_modello}", 0.1)
        carica_modello(nome_modello)
        return f"Modello {nome_modello} pronto."

    try:
        lavori.avvia(chiave, lavoro)
    except lavori.LavoroGiaInCorso as e:
        return jsonify(ok=False, messaggio=str(e)), 409
    return jsonify(ok=True, chiave=chiave)


# ---------------------------------------------------------------------------
# Home: elenco caroselli + creazione nuovo
# ---------------------------------------------------------------------------


def _elenca_caroselli() -> list[dict]:
    risultati = []
    if not PROMPTS_DIR.is_dir():
        return risultati
    for cartella in sorted(PROMPTS_DIR.iterdir(), reverse=True):
        path_json = cartella / "slides.json"
        if not path_json.is_file():
            continue
        try:
            carosello = CarosalloCompleto.model_validate_json(path_json.read_text(encoding="utf-8"))
        except ValueError:  # slides.json illeggibile: si salta invece di bloccare la home
            continue
        cartella_input = INPUT_IMMAGINI_DIR / carosello.nome_carosello
        cartella_output = OUTPUT_DIR / carosello.nome_carosello
        immagini_presenti = sum(
            1 for s in carosello.slides if (cartella_input / f"{s.numero:02d}.png").is_file()
        )
        immagini_composte = sum(
            1 for s in carosello.slides if (cartella_output / f"{s.numero:02d}.png").is_file()
        )
        risultati.append(
            {
                "nome": carosello.nome_carosello,
                "argomento": carosello.argomento,
                "numero_slide": carosello.numero_slide,
                "immagini_presenti": immagini_presenti,
                "immagini_composte": immagini_composte,
            }
        )
    return risultati


@app.route("/")
def home():
    return render_template(
        "home.html",
        caroselli=_elenca_caroselli(),
        caroselli_notizie=notizie_elenca_caroselli(),
        reel=elenca_reel(),
        prompt_suggerisci_topic=PROMPT_SUGGERISCI_TOPIC,
    )


@app.route("/nuovo", methods=["POST"])
def nuovo_carosello():
    titolo = request.form.get("nome", "").strip()
    testo = request.form.get("testo", "").strip()
    nome = _slugify(titolo)

    if not NOME_PATTERN.match(nome):
        flash(
            "Inserisci un titolo per il carosello (deve contenere almeno una lettera o un numero).",
            "errore",
        )
        return redirect(url_for("home"))
    if not testo:
        flash("Il testo grezzo della ricerca non può essere vuoto.", "errore")
        return redirect(url_for("home"))
    if (PROMPTS_DIR / nome / "slides.json").is_file():
        flash(f"Esiste già un carosello chiamato '{nome}'.", "errore")
        return redirect(url_for("home"))

    config = carica_config_opzionale()
    try:
        analisi = analizza_testo(config, testo)
        carosello = costruisci_carosello_completo(nome, analisi)
        salva_output(carosello)
        (PROMPTS_DIR / nome / "testo_originale.txt").write_text(testo, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - errori Gemini/validazione mostrati all'utente
        flash(f"Errore nell'analisi del testo: {e}", "errore")
        return redirect(url_for("home"))

    flash(f"Carosello '{nome}' creato con {carosello.numero_slide} slide.", "successo")
    return redirect(url_for("carosello_dettaglio", nome=nome))


@app.route("/api/carosello/<nome>", methods=["DELETE"])
def elimina_carosello(nome):
    if not NOME_PATTERN.match(nome):
        return jsonify(ok=False, messaggio="Nome carosello non valido."), 400

    trovato = False
    for cartella in (PROMPTS_DIR / nome, INPUT_IMMAGINI_DIR / nome, OUTPUT_DIR / nome):
        if cartella.is_dir():
            shutil.rmtree(cartella)
            trovato = True

    if not trovato:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Dettaglio carosello: prompt immagine, upload, composizione, QA
# ---------------------------------------------------------------------------


def _carica_carosello(nome: str) -> CarosalloCompleto | None:
    path_json = PROMPTS_DIR / nome / "slides.json"
    if not path_json.is_file():
        return None
    return CarosalloCompleto.model_validate_json(path_json.read_text(encoding="utf-8"))


def _carica_qa_log(nome: str) -> dict[int, dict]:
    path_qa_log = OUTPUT_DIR / nome / "qa_log.json"
    if not path_qa_log.is_file():
        return {}
    voci = json.loads(path_qa_log.read_text(encoding="utf-8"))
    return {voce["numero"]: voce for voce in voci}


@app.route("/carosello/<nome>")
def carosello_dettaglio(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        flash("Carosello non trovato.", "errore")
        return redirect(url_for("home"))

    cartella_input = INPUT_IMMAGINI_DIR / nome
    cartella_output = OUTPUT_DIR / nome
    qa_per_numero = _carica_qa_log(nome)
    config = carica_config_opzionale()

    slides_view = []
    for slide in carosello.slides:
        slides_view.append(
            {
                "slide": slide,
                "immagine_presente": (cartella_input / f"{slide.numero:02d}.png").is_file(),
                "composta": (cartella_output / f"{slide.numero:02d}.png").is_file(),
                "qa": qa_per_numero.get(slide.numero),
                "testo_markup": markup_da_segmenti(slide.segmenti),
            }
        )

    tutte_presenti = all(v["immagine_presente"] for v in slides_view)

    path_caption = cartella_output / "caption.txt"
    caption_esistente = path_caption.read_text(encoding="utf-8") if path_caption.is_file() else None
    ha_testo_originale = (PROMPTS_DIR / nome / "testo_originale.txt").is_file()

    return render_template(
        "carosello.html",
        carosello=carosello,
        slides_view=slides_view,
        tutte_presenti=tutte_presenti,
        soglia=config.soglia_qualita if config else 7.0,
        caption_esistente=caption_esistente,
        ha_testo_originale=ha_testo_originale,
        limite_caption=config.max_caratteri_caption if config else 1600,
        limite_slide=config.max_caratteri_slide if config else 280,
    )


@app.route("/file/input/<nome>/<int:numero>.png")
def file_input(nome, numero):
    return send_from_directory(INPUT_IMMAGINI_DIR / nome, f"{numero:02d}.png")


@app.route("/file/output/<nome>/<int:numero>.png")
def file_output(nome, numero):
    return send_from_directory(OUTPUT_DIR / nome, f"{numero:02d}.png")


@app.route("/api/carosello/<nome>/slide/<int:numero>/immagine", methods=["POST"])
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

    cartella = INPUT_IMMAGINI_DIR / nome
    cartella.mkdir(parents=True, exist_ok=True)
    immagine.save(cartella / f"{numero:02d}.png")

    # Un'immagine caricata a mano non ha crediti da citare (il tipo di layout resta quello scelto).
    slide = next(s for s in carosello.slides if s.numero == numero)
    if slide.credito_immagine is not None:
        slide.credito_immagine = None
        salva_output(carosello)

    return jsonify(ok=True, url=url_for("file_input", nome=nome, numero=numero))


def _invalida_slide_composta(nome: str, numero: int) -> None:
    """La slide composta non corrisponde più a testo/sfondo/layout: si toglie insieme
    alla sua voce di QA, finché non viene ricomposta."""
    cartella_output = OUTPUT_DIR / nome
    (cartella_output / f"{numero:02d}.png").unlink(missing_ok=True)
    path_qa_log = cartella_output / "qa_log.json"
    if path_qa_log.is_file():
        log = json.loads(path_qa_log.read_text(encoding="utf-8"))
        log = [v for v in log if v["numero"] != numero]
        path_qa_log.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/carosello/<nome>/query-immagini", methods=["POST"])
def query_immagini(nome):
    """Parole chiave per cercare immagini pubbliche, per tutte le slide in una sola
    chiamata. Con `solo_vuote` non tocca quelle già scritte a mano."""
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400
    try:
        query = suggerisci_query(config, carosello)
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Suggerimento non riuscito: {e}"), 502

    solo_vuote = request.form.get("solo_vuote") == "1"
    for slide, q in zip(carosello.slides, query):
        if not (solo_vuote and slide.query_immagine):
            slide.query_immagine = q
    salva_output(carosello)
    return jsonify(ok=True, query={s.numero: s.query_immagine for s in carosello.slides})


@app.route("/api/carosello/<nome>/slide/<int:numero>/query", methods=["POST"])
def salva_query_immagine(nome, numero):
    carosello = _carica_carosello(nome)
    slide = next((s for s in carosello.slides if s.numero == numero), None) if carosello else None
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404
    slide.query_immagine = request.form.get("query", "").strip()[:120]
    salva_output(carosello)
    return jsonify(ok=True)


@app.route("/api/carosello/<nome>/slide/<int:numero>/immagine-pubblica", methods=["POST"])
def usa_immagine_pubblica(nome, numero):
    carosello = _carica_carosello(nome)
    slide = next((s for s in carosello.slides if s.numero == numero), None) if carosello else None
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404
    fonte = request.form.get("fonte", "")
    if fonte not in ("nasa", "wikimedia"):
        return jsonify(ok=False, messaggio="Fonte non valida."), 400

    try:
        salva_immagine_pubblica(nome, numero, fonte, request.form.get("riferimento", ""))
    except ErroreMedia as e:
        return jsonify(ok=False, messaggio=str(e)), 502

    slide.tipo_sfondo = "foto"
    slide.credito_immagine = CreditoImmagine(
        fonte=fonte,
        titolo=request.form.get("titolo", "")[:300] or "Senza titolo",
        autore=request.form.get("autore") or None,
        licenza=request.form.get("licenza") or None,
        url=request.form.get("url_origine") or None,
    )
    salva_output(carosello)
    _invalida_slide_composta(nome, numero)
    return jsonify(ok=True, url=url_for("file_input", nome=nome, numero=numero))


@app.route("/api/carosello/<nome>/slide/<int:numero>/tipo-sfondo", methods=["POST"])
def imposta_tipo_sfondo(nome, numero):
    carosello = _carica_carosello(nome)
    slide = next((s for s in carosello.slides if s.numero == numero), None) if carosello else None
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404
    tipo = request.form.get("tipo", "")
    if tipo not in ("illustrazione", "foto"):
        return jsonify(ok=False, messaggio="Tipo non valido."), 400
    if slide.tipo_sfondo != tipo:
        slide.tipo_sfondo = tipo
        salva_output(carosello)
        _invalida_slide_composta(nome, numero)
    return jsonify(ok=True)


@app.route("/api/carosello/<nome>/slide/<int:numero>/testo", methods=["POST"])
def modifica_testo_slide(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    slide = next((s for s in carosello.slides if s.numero == numero), None)
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    config = carica_config_opzionale()
    limite = config.max_caratteri_slide if config else 280

    testo_markup = request.form.get("testo", "").strip()
    if not testo_markup:
        return jsonify(ok=False, messaggio="Il testo non può essere vuoto."), 400

    nuovi_segmenti = segmenti_da_markup(testo_markup)
    testo_piatto = "".join(s.testo for s in nuovi_segmenti)
    if len(testo_piatto) > limite:
        return (
            jsonify(
                ok=False,
                messaggio=f"Testo di {len(testo_piatto)} caratteri, oltre il limite di {limite}.",
            ),
            400,
        )

    slide.segmenti = nuovi_segmenti
    salva_output(carosello)

    # La slide composta (se esiste) mostra ancora il testo vecchio: va invalidata,
    # insieme all'eventuale voce di QA associata, così la UI non mostra uno stato
    # incoerente finché l'utente non la ricompone.
    _invalida_slide_composta(nome, numero)

    return jsonify(ok=True, testo=testo_piatto, lunghezza=len(testo_piatto), limite=limite)


def _componi_e_valuta(config, cartella_input: Path, cartella_output: Path, slide) -> dict:
    percorso_sfondo = cartella_input / f"{slide.numero:02d}.png"
    immagine = componi_slide(config, percorso_sfondo, slide)
    immagine.save(cartella_output / f"{slide.numero:02d}.png")

    qa = valuta_slide(config, immagine, slide.numero)
    dati = qa.model_dump()
    dati["punteggio_medio"] = round(qa.punteggio_medio(), 2)
    return dati


def _aggiorna_qa_log(cartella_output: Path, nuove_voci: list[dict]) -> None:
    path_qa_log = cartella_output / "qa_log.json"
    esistenti = json.loads(path_qa_log.read_text(encoding="utf-8")) if path_qa_log.is_file() else []
    numeri_nuovi = {v["numero"] for v in nuove_voci}
    log = [v for v in esistenti if v["numero"] not in numeri_nuovi] + nuove_voci
    log.sort(key=lambda v: v["numero"])
    path_qa_log.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/carosello/<nome>/componi", methods=["POST"])
def componi_carosello_route(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    try:
        cartella_input = valida_immagini_presenti(carosello)
    except ImmaginiMancantiError as e:
        return jsonify(ok=False, messaggio=str(e)), 400

    cartella_output = OUTPUT_DIR / nome
    cartella_output.mkdir(parents=True, exist_ok=True)

    risultati = [
        _componi_e_valuta(config, cartella_input, cartella_output, slide) for slide in carosello.slides
    ]
    _aggiorna_qa_log(cartella_output, risultati)

    return jsonify(ok=True, risultati=risultati, soglia=config.soglia_qualita)


@app.route("/api/carosello/<nome>/slide/<int:numero>/ricomponi", methods=["POST"])
def ricomponi_slide_route(nome, numero):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404
    slide = next((s for s in carosello.slides if s.numero == numero), None)
    if slide is None:
        return jsonify(ok=False, messaggio="Slide non trovata."), 404

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    cartella_input = INPUT_IMMAGINI_DIR / nome
    if not (cartella_input / f"{numero:02d}.png").is_file():
        return jsonify(ok=False, messaggio="Immagine di sfondo mancante per questa slide."), 400

    cartella_output = OUTPUT_DIR / nome
    cartella_output.mkdir(parents=True, exist_ok=True)

    risultato = _componi_e_valuta(config, cartella_input, cartella_output, slide)
    _aggiorna_qa_log(cartella_output, [risultato])

    return jsonify(ok=True, risultato=risultato, soglia=config.soglia_qualita)


@app.route("/api/carosello/<nome>/caption", methods=["POST"])
def genera_caption_route(nome):
    carosello = _carica_carosello(nome)
    if carosello is None:
        return jsonify(ok=False, messaggio="Carosello non trovato."), 404

    path_testo = PROMPTS_DIR / nome / "testo_originale.txt"
    if not path_testo.is_file():
        return jsonify(
            ok=False,
            messaggio=(
                "Testo originale non disponibile: questo carosello è stato creato con una "
                "versione precedente dell'app."
            ),
        ), 400

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    nome_autore = request.form.get("autore", "")

    try:
        caption = genera_caption(config, path_testo.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Errore nella generazione della caption: {e}"), 500

    crediti = testo_crediti(carosello)
    if crediti:
        caption.testo = f"{caption.testo.rstrip()}\n\n{crediti}"
    testo_finale = formatta_caption(caption, nome_autore)

    cartella_output = OUTPUT_DIR / nome
    cartella_output.mkdir(parents=True, exist_ok=True)
    (cartella_output / "caption.txt").write_text(testo_finale, encoding="utf-8")

    return jsonify(
        ok=True, caption=testo_finale, lunghezza=len(testo_finale), limite=config.max_caratteri_caption
    )


@app.route("/carosello/<nome>/scarica")
def scarica_output(nome):
    cartella_output = OUTPUT_DIR / nome
    if not cartella_output.is_dir() or not any(cartella_output.glob("*.png")):
        flash("Nessun output da scaricare per questo carosello: componi prima le slide.", "errore")
        return redirect(url_for("carosello_dettaglio", nome=nome))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(cartella_output.glob("*.png")):
            zf.write(file, arcname=file.name)
        path_caption = cartella_output / "caption.txt"
        if path_caption.is_file():
            zf.write(path_caption, arcname="caption.txt")
    buffer.seek(0)

    return send_file(
        buffer, mimetype="application/zip", as_attachment=True, download_name=f"{nome}_carosello.zip"
    )


if __name__ == "__main__":
    # use_reloader=True (senza debug=True, quindi senza il debugger interattivo esposto):
    # riavvia da solo il processo quando un file .py cambia, invece di dover chiudere e
    # rilanciare a mano ogni volta.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=True)
