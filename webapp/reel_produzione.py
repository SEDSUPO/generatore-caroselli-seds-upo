"""Route Flask per la produzione del reel, dopo il copione: registrazione delle
scene, libreria di immagini/video (caricati, NASA, Wikimedia, YouTube) e timeline
dei visivi, montaggio, copertina e pubblicazione.

Le operazioni lunghe (download, import di video, montaggio) girano come lavori in
background (src/lavori.py): la route risponde subito con la chiave del lavoro e
l'interfaccia ne interroga lo stato.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import lavori, reel_voce
from src.config import carica_config_opzionale
from src.durata_parlato import formatta_durata, stima_secondi
from src.ffmpeg_utils import durata_media, estrai_fotogramma
from src.models import Reel
from src.musica_libera import ErroreMusica, cerca_musica, scarica_brano
from src.reel_copertina import componi_copertina
from src.reel_media import (
    LATO_MINIMO_CONSIGLIATO,
    ErroreMedia,
    RisultatoRicerca,
    cerca_nasa,
    cerca_wikimedia,
    importa_immagine,
    importa_risultato,
    importa_video,
    importa_youtube,
    rimuovi_asset,
)
from src.reel_montaggio import QUALITA_ANTEPRIMA, QUALITA_FINALE, RAPPORTO_OLTRE_CUI_SFONDO_SFOCATO, monta
from src.reel_produzione import (
    aggiungi_take,
    calcola_blocchi,
    carica_produzione,
    carica_reel,
    cartella_audio,
    cartella_media,
    cartella_output,
    cartella_reel,
    crediti_completi,
    elimina_file,
    mancanze,
    modifica_produzione,
    rimuovi_take,
    segmenti_parlati,
    voci_ai_usate,
)
from src.reel_visivi_ai import analizza_visivi, prompt_copertina
from webapp.reel_comune import stato_passi
from webapp.utils import NOME_PATTERN

bp = Blueprint("reel_prod", __name__)

ESTENSIONI_IMMAGINE = {".jpg", ".jpeg", ".png", ".webp"}
ESTENSIONI_VIDEO = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
ESTENSIONI_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".webm", ".flac", ".aac"}
_HOST_YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def _reel_o_404(nome: str) -> Reel:
    reel = carica_reel(nome) if NOME_PATTERN.match(nome) else None
    if reel is None:
        abort(404)
    return reel


def _errore(messaggio: str, codice: int = 400):
    return jsonify(ok=False, messaggio=messaggio), codice


def _chiave_valida(reel: Reel, chiave: str) -> bool:
    return any(s.chiave == chiave for s in segmenti_parlati(reel))


def _salva_upload_temporaneo(reel: Reel, file, estensioni: set[str]) -> Path:
    estensione = Path(file.filename or "").suffix.lower()
    if estensione not in estensioni:
        raise ErroreMedia(f"Formato non supportato ({estensione or 'senza estensione'}).")
    percorso = cartella_reel(reel.nome_reel) / f"_upload_{uuid.uuid4().hex[:8]}{estensione}"
    file.save(percorso)
    return percorso


def _versione_file(percorso: Path) -> int:
    """Per invalidare la cache del browser quando un file viene rigenerato."""
    return int(percorso.stat().st_mtime) if percorso.is_file() else 0


def _durate_segmenti(reel: Reel, produzione) -> dict[str, dict]:
    """Durata vera se registrato, altrimenti stimata dal testo (per dare comunque
    un'idea della lunghezza dei blocchi mentre si scelgono i visivi)."""
    durate = {}
    for segmento in segmenti_parlati(reel):
        take = produzione.segmenti[segmento.chiave].take_attivo()
        durate[segmento.chiave] = {
            "secondi": take.durata if take else stima_secondi(segmento.testo),
            "stimata": take is None,
        }
    return durate


def _contesto_comune(reel: Reel, passo: str) -> dict:
    return {"reel": reel, "passi": stato_passi(reel), "passo_attivo": passo}


# ---------------------------------------------------------------------------
# File (audio, media, output)
# ---------------------------------------------------------------------------


@bp.route("/file/reel/<nome>/<tipo>/<path:file>")
def file_reel(nome, tipo, file):
    if not NOME_PATTERN.match(nome):
        abort(404)
    cartelle = {"audio": cartella_audio, "media": cartella_media, "output": cartella_output}
    if tipo not in cartelle:
        abort(404)
    # send_from_directory rifiuta percorsi che escono dalla cartella e gestisce le
    # richieste Range (necessarie per spostarsi dentro audio e video nel browser).
    return send_from_directory(cartelle[tipo](nome), file, max_age=0)


# ---------------------------------------------------------------------------
# Lavori in background
# ---------------------------------------------------------------------------


@bp.route("/api/lavoro")
def stato_lavoro():
    lavoro = lavori.stato(request.args.get("chiave", ""))
    if lavoro is None:
        return jsonify(ok=True, lavoro=None)
    return jsonify(ok=True, lavoro=lavoro.come_dict())


def _avvia_lavoro(chiave: str, funzione):
    try:
        lavori.avvia(chiave, funzione)
    except lavori.LavoroGiaInCorso as e:
        return _errore(str(e), 409)
    return jsonify(ok=True, chiave=chiave)


# ---------------------------------------------------------------------------
# 2. Registrazione
# ---------------------------------------------------------------------------


@bp.route("/reel/<nome>/registrazione")
def registrazione(nome):
    reel = _reel_o_404(nome)
    produzione = carica_produzione(reel)
    segmenti_view = []
    for segmento in segmenti_parlati(reel):
        stato = produzione.segmenti[segmento.chiave]
        segmenti_view.append(
            {
                "segmento": segmento,
                "stato": stato,
                "durata_stimata": formatta_durata(stima_secondi(segmento.testo)),
                "dati": _take_json(nome, stato),
            }
        )
    return render_template(
        "reel_registrazione.html",
        segmenti_view=segmenti_view,
        voci=reel_voce.VOCI,
        motori=reel_voce.MOTORI,
        velocita_limiti=(reel_voce.VELOCITA_MINIMA, reel_voce.VELOCITA_MASSIMA),
        produzione=produzione,
        chiave_lavoro_voce=f"voce:{nome}",
        segmenti_js=[{"chiave": v["segmento"].chiave, "etichetta": v["segmento"].etichetta, "testo": v["segmento"].testo} for v in segmenti_view],
        **_contesto_comune(reel, "registrazione"),
    )


def _take_json(nome: str, stato) -> dict:
    return {
        "take_scelto": stato.take_scelto,
        "takes": [
            {
                "id": t.id,
                "durata": round(t.durata, 1),
                "voce_ai": t.voce_ai,
                "creato_il": t.creato_il,
                "url": url_for("reel_prod.file_reel", nome=nome, tipo="audio", file=t.file),
            }
            for t in stato.takes
        ],
    }


@bp.route("/api/reel/<nome>/take/<chiave>", methods=["POST"])
def carica_take(nome, chiave):
    reel = _reel_o_404(nome)
    if not _chiave_valida(reel, chiave):
        return _errore("Segmento non trovato.", 404)
    file = request.files.get("audio")
    if file is None or not file.filename:
        return _errore("Nessun audio ricevuto.")

    try:
        temporaneo = _salva_upload_temporaneo(reel, file, ESTENSIONI_AUDIO)
    except ErroreMedia as e:
        return _errore(str(e))
    try:
        with modifica_produzione(reel) as produzione:
            aggiungi_take(nome, produzione, chiave, temporaneo)
            dati = _take_json(nome, produzione.segmenti[chiave])
    except ValueError as e:
        return _errore(str(e))
    except RuntimeError:  # ffmpeg non riesce a leggere il file
        return _errore("Audio non leggibile: il file non sembra una registrazione valida.")
    finally:
        temporaneo.unlink(missing_ok=True)
    return jsonify(ok=True, **dati)


@bp.route("/api/reel/<nome>/take/<chiave>/<take_id>/scegli", methods=["POST"])
def scegli_take(nome, chiave, take_id):
    reel = _reel_o_404(nome)
    if not _chiave_valida(reel, chiave):
        return _errore("Segmento non trovato.", 404)
    with modifica_produzione(reel) as produzione:
        stato = produzione.segmenti[chiave]
        if not any(t.id == take_id for t in stato.takes):
            return _errore("Registrazione non trovata.", 404)
        stato.take_scelto = take_id
        dati = _take_json(nome, stato)
    return jsonify(ok=True, **dati)


@bp.route("/api/reel/<nome>/take/<chiave>/<take_id>", methods=["DELETE"])
def elimina_take(nome, chiave, take_id):
    reel = _reel_o_404(nome)
    if not _chiave_valida(reel, chiave):
        return _errore("Segmento non trovato.", 404)
    with modifica_produzione(reel) as produzione:
        rimuovi_take(nome, produzione, chiave, take_id)
        dati = _take_json(nome, produzione.segmenti[chiave])
    return jsonify(ok=True, **dati)


# --- Voce generata --------------------------------------------------------


def _leggi_impostazioni_voce() -> tuple[str, str, float, str]:
    """Dal form; solleva ErroreVoce se non valide."""
    motore = request.form.get("motore", "")
    voce = request.form.get("voce", "")
    try:
        velocita = round(float(request.form.get("velocita", "1")), 2)
    except ValueError as e:
        raise reel_voce.ErroreVoce("Velocità non valida.") from e
    istruzioni = request.form.get("istruzioni", "").strip()[:300]
    reel_voce.valida(motore, voce, velocita)
    return motore, voce, velocita, istruzioni


@bp.route("/api/reel/<nome>/voce/impostazioni", methods=["POST"])
def imposta_voce(nome):
    reel = _reel_o_404(nome)
    try:
        motore, voce, velocita, istruzioni = _leggi_impostazioni_voce()
    except reel_voce.ErroreVoce as e:
        return _errore(str(e))
    with modifica_produzione(reel) as produzione:
        produzione.voce_motore = motore
        produzione.voce_nome = voce
        produzione.voce_velocita = velocita
        produzione.voce_istruzioni = istruzioni
    return jsonify(ok=True)


@bp.route("/api/voce/anteprima", methods=["POST"])
def anteprima_voce():
    try:
        motore, voce, velocita, istruzioni = _leggi_impostazioni_voce()
    except reel_voce.ErroreVoce as e:
        return _errore(str(e))
    config = carica_config_opzionale()

    def lavoro(aggiorna):
        aggiorna("Preparazione dell'anteprima", 0.05)
        # Il messaggio finale è il nome del file: l'interfaccia lo apre da /file/anteprima-voce/.
        return reel_voce.anteprima(config, motore, voce, velocita, istruzioni, aggiorna).name

    return _avvia_lavoro("voce-anteprima", lavoro)


@bp.route("/file/anteprima-voce/<file>")
def file_anteprima_voce(file):
    return send_from_directory(reel_voce.CARTELLA_ANTEPRIME, file, max_age=0)


@bp.route("/api/reel/<nome>/voce/genera", methods=["POST"])
def genera_voce(nome):
    """Genera la voce per una parte (`chiave`, con testo modificabile) oppure per
    tutte le parti ancora senza ripresa (`chiave` = "mancanti")."""
    reel = _reel_o_404(nome)
    produzione = carica_produzione(reel)
    segmenti = {s.chiave: s for s in segmenti_parlati(reel)}
    chiave = request.form.get("chiave", "")

    if chiave == "mancanti":
        da_fare = [(c, s.testo) for c, s in segmenti.items() if produzione.segmenti[c].take_attivo() is None]
        if not da_fare:
            return _errore("Tutte le parti hanno già una ripresa: usa \"Genera voce\" sulla singola parte per rifarla.")
    elif chiave in segmenti:
        testo = request.form.get("testo", "").strip() or segmenti[chiave].testo
        da_fare = [(chiave, testo[:2000])]
    else:
        return _errore("Segmento non trovato.", 404)

    motore, voce, velocita = produzione.voce_motore, produzione.voce_nome, produzione.voce_velocita
    istruzioni = produzione.voce_istruzioni
    config = carica_config_opzionale()
    if motore == "gemini" and config is None:
        return _errore("API key non configurata.")
    etichetta = reel_voce.etichetta_voce(motore, voce)

    def lavoro(aggiorna):
        cartella = cartella_reel(nome)
        for indice, (chiave_segmento, testo) in enumerate(da_fare):
            base = indice / len(da_fare)
            passo = 1 / len(da_fare)
            nome_parte = segmenti[chiave_segmento].etichetta
            aggiorna(f"Voce: {nome_parte} ({indice + 1}/{len(da_fare)})", base)

            def avanzamento_download(fase, frazione, base=base, passo=passo):
                aggiorna(fase, base + frazione * passo * 0.5)

            grezzo = cartella / f"_voce_{uuid.uuid4().hex[:8]}.wav"
            try:
                reel_voce.genera(config, testo, motore, voce, velocita, istruzioni, grezzo, avanzamento_download)
                with modifica_produzione(reel) as aggiornata:
                    aggiungi_take(nome, aggiornata, chiave_segmento, grezzo, voce_ai=etichetta)
            finally:
                grezzo.unlink(missing_ok=True)
        return f"Voce generata per {len(da_fare)} {'parte' if len(da_fare) == 1 else 'parti'}."

    return _avvia_lavoro(f"voce:{nome}", lavoro)


# ---------------------------------------------------------------------------
# 3. Visivi: libreria, ricerca, timeline
# ---------------------------------------------------------------------------


@bp.route("/reel/<nome>/visivi")
def visivi(nome):
    reel = _reel_o_404(nome)
    produzione = carica_produzione(reel)
    durate = _durate_segmenti(reel, produzione)

    segmenti_view = []
    durata_blocco: dict[str, float] = {}  # chiave del primo segmento del blocco -> durata
    testa = None
    for indice, segmento in enumerate(segmenti_parlati(reel)):
        stato = produzione.segmenti[segmento.chiave]
        if indice == 0 or not stato.continua_precedente:
            testa = segmento.chiave
        durata_blocco[testa] = durata_blocco.get(testa, 0.0) + durate[segmento.chiave]["secondi"]
        segmenti_view.append(
            {
                "segmento": segmento,
                "stato": stato,
                "primo": indice == 0,
                "testa": testa,
                "asset": produzione.asset_per_id(stato.asset_id),
                "durata": formatta_durata(durate[segmento.chiave]["secondi"]),
                "durata_stimata": durate[segmento.chiave]["stimata"],
            }
        )
    uso_asset: dict[str, list[str]] = {}
    for view in segmenti_view:
        view["durata_blocco"] = round(durata_blocco[view["testa"]], 2)
        if view["asset"] and not (view["stato"].continua_precedente and not view["primo"]):
            uso_asset.setdefault(view["asset"].id, []).append(view["segmento"].etichetta)

    return render_template(
        "reel_visivi.html",
        segmenti_view=segmenti_view,
        produzione=produzione,
        uso_asset=uso_asset,
        lato_minimo=LATO_MINIMO_CONSIGLIATO,
        rapporto_orizzontale=RAPPORTO_OLTRE_CUI_SFONDO_SFOCATO,
        **_contesto_comune(reel, "visivi"),
    )


@bp.route("/api/reel/<nome>/visivi/suggerisci-query", methods=["POST"])
def suggerisci_query(nome):
    reel = _reel_o_404(nome)
    config = carica_config_opzionale()
    if config is None:
        return _errore("API key non configurata.")
    try:
        analisi = analizza_visivi(config, reel)
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return _errore(f"Suggerimento non riuscito: {e}", 502)

    # Con `solo_vuote` (compilazione automatica all'apertura della pagina) non si
    # sovrascrivono le ricerche già scritte a mano.
    solo_vuote = request.form.get("solo_vuote") == "1"
    with modifica_produzione(reel) as produzione:
        for segmento, query in zip(segmenti_parlati(reel), analisi.query):
            stato = produzione.segmenti[segmento.chiave]
            if not (solo_vuote and stato.query_ricerca):
                stato.query_ricerca = query
        if not (solo_vuote and produzione.soggetto_copertina):
            produzione.soggetto_copertina = analisi.soggetto_copertina
        if not (solo_vuote and produzione.musica_query):
            produzione.musica_query = analisi.musica_query
        query_finali = {chiave: stato.query_ricerca for chiave, stato in produzione.segmenti.items()}
    return jsonify(ok=True, query=query_finali)


@bp.route("/api/reel/cerca-media")
def cerca_media():
    query = request.args.get("q", "").strip()
    fonte = request.args.get("fonte", "")
    if not query:
        return _errore("Scrivi cosa cercare.")
    try:
        if fonte == "nasa-immagini":
            risultati = cerca_nasa(query, "immagine")
        elif fonte == "nasa-video":
            risultati = cerca_nasa(query, "video")
        elif fonte == "wikimedia":
            risultati = cerca_wikimedia(query)
        else:
            return _errore("Fonte non valida.")
    except Exception as e:  # noqa: BLE001 - archivio irraggiungibile o risposta inattesa
        return _errore(f"Ricerca non riuscita: {e}", 502)
    return jsonify(ok=True, risultati=[r.__dict__ for r in risultati])


def _aggiungi_asset(reel: Reel, asset, assegna_a: str | None) -> None:
    with modifica_produzione(reel) as produzione:
        produzione.asset.append(asset)
        if assegna_a in produzione.segmenti:
            stato = produzione.segmenti[assegna_a]
            stato.asset_id = asset.id
            stato.inizio_video = 0.0


def _segmento_da_assegnare(reel: Reel) -> str | None:
    chiave = (request.form.get("assegna_a") or "").strip()
    return chiave if chiave and _chiave_valida(reel, chiave) else None


@bp.route("/api/reel/<nome>/media/importa", methods=["POST"])
def importa_media(nome):
    reel = _reel_o_404(nome)
    try:
        risultato = RisultatoRicerca(
            fonte=request.form["fonte"],
            tipo=request.form["tipo"],
            titolo=request.form.get("titolo", "")[:300] or "Senza titolo",
            anteprima_url=request.form.get("anteprima_url", ""),
            riferimento=request.form["riferimento"],
            autore=request.form.get("autore") or None,
            licenza=request.form.get("licenza") or None,
            url_origine=request.form.get("url_origine") or None,
            larghezza=None,
            altezza=None,
        )
    except KeyError:
        return _errore("Dati del risultato incompleti.")
    if risultato.fonte not in ("nasa", "wikimedia") or risultato.tipo not in ("immagine", "video"):
        return _errore("Risultato non valido.")
    # Il server scarica l'URL ricevuto: si accettano solo file dell'archivio Wikimedia
    # (per NASA il riferimento è un id, l'URL lo costruisce il server).
    if risultato.fonte == "wikimedia" and urlparse(risultato.riferimento).netloc != "upload.wikimedia.org":
        return _errore("URL del file non valido.")
    assegna_a = _segmento_da_assegnare(reel)

    def lavoro(aggiorna):
        aggiorna("Download in corso", 0.1)
        asset = importa_risultato(nome, risultato)
        _aggiungi_asset(reel, asset, assegna_a)
        return f"Importato: {asset.titolo}"

    return _avvia_lavoro(f"importa:{nome}:{uuid.uuid4().hex[:8]}", lavoro)


@bp.route("/api/reel/<nome>/media/carica", methods=["POST"])
def carica_media(nome):
    reel = _reel_o_404(nome)
    file = request.files.get("file")
    if file is None or not file.filename:
        return _errore("Nessun file selezionato.")
    assegna_a = _segmento_da_assegnare(reel)
    estensione = Path(file.filename).suffix.lower()
    titolo = Path(file.filename).stem[:200]

    try:
        if estensione in ESTENSIONI_IMMAGINE:
            asset = importa_immagine(nome, file.read(), titolo, "caricato")
        elif estensione in ESTENSIONI_VIDEO:
            temporaneo = _salva_upload_temporaneo(reel, file, ESTENSIONI_VIDEO)
            try:
                asset = importa_video(nome, temporaneo, titolo, "caricato")
            finally:
                temporaneo.unlink(missing_ok=True)
        else:
            return _errore(f"Formato non supportato ({estensione or 'senza estensione'}): usa JPG/PNG/WEBP o MP4/MOV/WEBM.")
    except ErroreMedia as e:
        return _errore(str(e))

    _aggiungi_asset(reel, asset, assegna_a)
    return jsonify(ok=True, asset_id=asset.id)


@bp.route("/api/reel/<nome>/media/youtube", methods=["POST"])
def youtube(nome):
    reel = _reel_o_404(nome)
    url = request.form.get("url", "").strip()
    host = urlparse(url).netloc.lower()
    if urlparse(url).scheme not in ("http", "https") or host not in _HOST_YOUTUBE:
        return _errore("Incolla un link di YouTube (youtube.com o youtu.be).")
    config = carica_config_opzionale()
    if config is None:
        return _errore("API key non configurata.")
    assegna_a = _segmento_da_assegnare(reel)

    def lavoro(aggiorna):
        aggiorna("Download da YouTube (può richiedere qualche minuto)", 0.1)
        asset = importa_youtube(nome, url, config)
        _aggiungi_asset(reel, asset, assegna_a)
        return f"Scaricato: {asset.titolo}"

    return _avvia_lavoro(f"youtube:{nome}", lavoro)


@bp.route("/api/reel/<nome>/media/<asset_id>", methods=["DELETE"])
def elimina_media(nome, asset_id):
    reel = _reel_o_404(nome)
    with modifica_produzione(reel) as produzione:
        if produzione.asset_per_id(asset_id) is None:
            return _errore("Elemento non trovato.", 404)
        rimuovi_asset(nome, produzione, asset_id)
    return jsonify(ok=True)


@bp.route("/api/reel/<nome>/segmento/<chiave>/visivo", methods=["POST"])
def imposta_visivo(nome, chiave):
    """Aggiornamento parziale: si modificano solo i campi presenti nel form."""
    reel = _reel_o_404(nome)
    if not _chiave_valida(reel, chiave):
        return _errore("Segmento non trovato.", 404)
    primo = segmenti_parlati(reel)[0].chiave

    with modifica_produzione(reel) as produzione:
        stato = produzione.segmenti[chiave]
        if "asset_id" in request.form:
            asset_id = request.form["asset_id"] or None
            if asset_id and produzione.asset_per_id(asset_id) is None:
                return _errore("Elemento della libreria non trovato.", 404)
            if asset_id != stato.asset_id:
                stato.asset_id = asset_id
                stato.inizio_video = 0.0
                stato.riempi_schermo = False
                stato.inquadratura_x = 0.5
        if "riempi_schermo" in request.form:
            stato.riempi_schermo = request.form["riempi_schermo"] == "1"
        if "inquadratura_x" in request.form:
            try:
                stato.inquadratura_x = round(min(max(float(request.form["inquadratura_x"]), 0.0), 1.0), 3)
            except ValueError:
                return _errore("Inquadratura non valida.")
        if "continua_precedente" in request.form:
            stato.continua_precedente = request.form["continua_precedente"] == "1" and chiave != primo
        if "inizio_video" in request.form:
            try:
                inizio = max(float(request.form["inizio_video"]), 0.0)
            except ValueError:
                return _errore("Inizio del video non valido.")
            asset = produzione.asset_per_id(stato.asset_id)
            if asset and asset.durata:
                inizio = min(inizio, asset.durata)
            stato.inizio_video = round(inizio, 2)
        if "query_ricerca" in request.form:
            stato.query_ricerca = request.form["query_ricerca"].strip()[:120]
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# 4. Montaggio
# ---------------------------------------------------------------------------


@bp.route("/reel/<nome>/montaggio")
def montaggio(nome):
    reel = _reel_o_404(nome)
    produzione = carica_produzione(reel)
    etichette = {s.chiave: s.etichetta for s in segmenti_parlati(reel)}
    blocchi_view = [
        {
            "etichette": ", ".join(etichette[c] for c in b.segmenti),
            "asset": b.asset,
            "inizio": formatta_durata(b.inizio),
            "durata": f"{b.durata:.1f} s",
        }
        for b in calcola_blocchi(reel, produzione)
    ]
    uscita = cartella_output(nome)
    video = {}
    for qualita, chiave in ((QUALITA_ANTEPRIMA, "anteprima"), (QUALITA_FINALE, "finale")):
        percorso = uscita / qualita.nome_file
        if percorso.is_file():
            video[chiave] = {
                "url": url_for("reel_prod.file_reel", nome=nome, tipo="output", file=qualita.nome_file, v=_versione_file(percorso)),
                "creato_il": datetime.fromtimestamp(percorso.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
            }

    musica_url = None
    if produzione.musica_file:
        musica_url = url_for("reel_prod.file_reel", nome=nome, tipo="media", file=produzione.musica_file)

    return render_template(
        "reel_montaggio.html",
        produzione=produzione,
        mancanze=mancanze(reel, produzione),
        blocchi_view=blocchi_view,
        durata_totale=formatta_durata(sum(b.durata for b in calcola_blocchi(reel, produzione))),
        video=video,
        musica_url=musica_url,
        chiave_lavoro=f"monta:{nome}",
        **_contesto_comune(reel, "montaggio"),
    )


@bp.route("/api/reel/<nome>/opzioni", methods=["POST"])
def imposta_opzioni(nome):
    reel = _reel_o_404(nome)
    with modifica_produzione(reel) as produzione:
        if "sottotitoli_attivi" in request.form:
            produzione.sottotitoli_attivi = request.form["sottotitoli_attivi"] == "1"
        if "titolo_hook_attivo" in request.form:
            produzione.titolo_hook_attivo = request.form["titolo_hook_attivo"] == "1"
        if "musica_volume" in request.form:
            try:
                produzione.musica_volume = min(max(float(request.form["musica_volume"]), 0.0), 1.0)
            except ValueError:
                return _errore("Volume non valido.")
    return jsonify(ok=True)


@bp.route("/api/reel/<nome>/musica", methods=["POST"])
def carica_musica(nome):
    reel = _reel_o_404(nome)
    file = request.files.get("musica")
    if file is None or not file.filename:
        return _errore("Nessun file selezionato.")
    estensione = Path(file.filename).suffix.lower()
    if estensione not in ESTENSIONI_AUDIO:
        return _errore("Formato non supportato: usa MP3, WAV, M4A, OGG o FLAC.")

    cartella = cartella_media(nome)
    cartella.mkdir(parents=True, exist_ok=True)
    nome_file = f"musica_{uuid.uuid4().hex[:8]}{estensione}"
    file.save(cartella / nome_file)
    if not durata_media(cartella / nome_file):
        (cartella / nome_file).unlink(missing_ok=True)
        return _errore("Il file non è un audio leggibile.")

    _imposta_musica(reel, nome_file, titolo=Path(file.filename).stem[:200])
    return jsonify(ok=True)


def _imposta_musica(reel: Reel, nome_file: str, titolo: str | None = None, autore: str | None = None,
                    licenza: str | None = None, url: str | None = None, attribuzione: str | None = None) -> None:
    """Sostituisce il brano e i suoi crediti (vuoti per un file caricato a mano)."""
    with modifica_produzione(reel) as produzione:
        vecchio = produzione.musica_file
        produzione.musica_file = nome_file
        produzione.musica_titolo = titolo
        produzione.musica_autore = autore
        produzione.musica_licenza = licenza
        produzione.musica_url = url
        produzione.musica_attribuzione = attribuzione
    if vecchio and vecchio != nome_file:
        elimina_file(cartella_media(reel.nome_reel) / vecchio)


@bp.route("/api/musica/cerca")
def cerca_musica_route():
    query = request.args.get("q", "").strip()
    if not query:
        return _errore("Scrivi cosa cercare.")
    try:
        brani = cerca_musica(query, includi_nd=request.args.get("includi_nd") == "1")
    except ErroreMusica as e:
        return _errore(str(e), 502)
    return jsonify(ok=True, brani=[b.__dict__ for b in brani])


@bp.route("/api/reel/<nome>/musica/libera", methods=["POST"])
def usa_musica_libera(nome):
    reel = _reel_o_404(nome)
    try:
        percorso, brano = scarica_brano(request.form.get("id", ""), cartella_media(nome), f"musica_{uuid.uuid4().hex[:8]}")
    except ErroreMusica as e:
        return _errore(str(e), 502)
    if not durata_media(percorso):
        elimina_file(percorso)
        return _errore("Il file scaricato non è un audio leggibile.", 502)
    _imposta_musica(reel, percorso.name, brano.titolo, brano.autore, brano.licenza, brano.pagina, brano.attribuzione)
    return jsonify(ok=True)


@bp.route("/api/reel/<nome>/musica", methods=["DELETE"])
def elimina_musica(nome):
    reel = _reel_o_404(nome)
    with modifica_produzione(reel) as produzione:
        vecchio = produzione.musica_file
        produzione.musica_file = None
        produzione.musica_titolo = produzione.musica_autore = produzione.musica_licenza = None
        produzione.musica_url = produzione.musica_attribuzione = None
    if vecchio:
        elimina_file(cartella_media(nome) / vecchio)
    return jsonify(ok=True)


@bp.route("/api/reel/<nome>/monta", methods=["POST"])
def avvia_montaggio(nome):
    reel = _reel_o_404(nome)
    config = carica_config_opzionale()
    if config is None:
        return _errore("API key non configurata.")
    problemi = mancanze(reel, carica_produzione(reel))
    if problemi:
        return _errore("Mancano ancora: " + "; ".join(problemi))
    anteprima = request.form.get("anteprima") == "1"

    def lavoro(aggiorna):
        monta(config, reel, anteprima, aggiorna)
        return "Anteprima pronta." if anteprima else "Video finale pronto."

    return _avvia_lavoro(f"monta:{nome}", lavoro)


# ---------------------------------------------------------------------------
# 5. Pubblica: copertina, caption, crediti, download
# ---------------------------------------------------------------------------


@bp.route("/reel/<nome>/pubblica")
def pubblica(nome):
    reel = _reel_o_404(nome)
    produzione = carica_produzione(reel)
    uscita = cartella_output(nome)
    config = carica_config_opzionale()

    reel_mp4 = uscita / QUALITA_FINALE.nome_file
    copertina = uscita / "copertina.jpg"
    path_caption = cartella_reel(nome) / "caption.txt"
    caption_esistente = path_caption.read_text(encoding="utf-8") if path_caption.is_file() else None

    return render_template(
        "reel_pubblica.html",
        produzione=produzione,
        prompt_copertina=prompt_copertina(produzione.soggetto_copertina) if produzione.soggetto_copertina else None,
        video_url=(
            url_for("reel_prod.file_reel", nome=nome, tipo="output", file=reel_mp4.name, v=_versione_file(reel_mp4))
            if reel_mp4.is_file()
            else None
        ),
        copertina_url=(
            url_for("reel_prod.file_reel", nome=nome, tipo="output", file=copertina.name, v=_versione_file(copertina))
            if copertina.is_file()
            else None
        ),
        ha_sfondo_copertina=(uscita / "copertina_sfondo.jpg").is_file(),
        caption_esistente=caption_esistente,
        limite_caption=config.max_caratteri_caption if config else 1600,
        crediti=crediti_completi(reel, produzione),
        voci_ai=voci_ai_usate(produzione),
        **_contesto_comune(reel, "pubblica"),
    )


@bp.route("/api/reel/<nome>/copertina/prompt", methods=["POST"])
def genera_prompt_copertina(nome):
    reel = _reel_o_404(nome)
    soggetto = request.form.get("soggetto", "").strip()

    if not soggetto:
        config = carica_config_opzionale()
        if config is None:
            return _errore("API key non configurata.")
        try:
            analisi = analizza_visivi(config, reel)
        except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
            return _errore(f"Suggerimento del soggetto non riuscito: {e}", 502)
        soggetto = analisi.soggetto_copertina

    with modifica_produzione(reel) as produzione:
        produzione.soggetto_copertina = soggetto[:200]
    return jsonify(ok=True, soggetto=soggetto, prompt=prompt_copertina(soggetto))


@bp.route("/api/reel/<nome>/copertina", methods=["POST"])
def componi_copertina_route(nome):
    """Sfondo da: immagine caricata, fotogramma del video finale, oppure lo sfondo
    già usato (per cambiare solo il titolo)."""
    reel = _reel_o_404(nome)
    uscita = cartella_output(nome)
    uscita.mkdir(parents=True, exist_ok=True)
    sfondo = uscita / "copertina_sfondo.jpg"
    sorgente = request.form.get("sorgente", "")
    titolo = request.form.get("titolo", "").strip()[:160]

    try:
        if sorgente == "file":
            file = request.files.get("immagine")
            if file is None or not file.filename:
                return _errore("Nessuna immagine selezionata.")
            if Path(file.filename).suffix.lower() not in ESTENSIONI_IMMAGINE:
                return _errore("Formato non supportato: usa JPG, PNG o WEBP.")
            from PIL import Image, ImageOps, UnidentifiedImageError  # noqa: PLC0415

            try:
                immagine = ImageOps.exif_transpose(Image.open(file.stream)).convert("RGB")
            except (UnidentifiedImageError, OSError):
                return _errore("Il file caricato non è un'immagine valida.")
            immagine.save(sfondo, quality=93)
        elif sorgente == "fotogramma":
            video = uscita / QUALITA_FINALE.nome_file
            if not video.is_file():
                return _errore("Monta prima il video finale per usarne un fotogramma.")
            try:
                secondo = float(request.form.get("secondo", "0"))
            except ValueError:
                return _errore("Secondo non valido.")
            # Oltre la fine ffmpeg termina senza errori ma senza scrivere il fotogramma.
            secondo = min(secondo, max((durata_media(video) or 0) - 0.1, 0))
            sfondo.unlink(missing_ok=True)
            estrai_fotogramma(video, secondo, sfondo, larghezza=1080)
            if not sfondo.is_file():
                return _errore("Fotogramma non disponibile in quel punto del video.")
        elif sorgente == "esistente":
            if not sfondo.is_file():
                return _errore("Nessuno sfondo ancora scelto per la copertina.")
        else:
            return _errore("Sorgente non valida.")

        componi_copertina(sfondo, titolo, uscita / "copertina.jpg")
    except RuntimeError as e:  # ffmpeg
        return _errore(f"Estrazione del fotogramma non riuscita: {e}", 500)

    with modifica_produzione(reel) as produzione:
        produzione.titolo_copertina = titolo
    copertina = uscita / "copertina.jpg"
    return jsonify(
        ok=True,
        url=url_for("reel_prod.file_reel", nome=nome, tipo="output", file=copertina.name, v=_versione_file(copertina)),
    )


@bp.route("/reel/<nome>/passo-successivo")
def passo_successivo(nome):
    """Dal copione porta al primo passo non ancora completo."""
    reel = _reel_o_404(nome)
    passi = stato_passi(reel)
    for passo in ("registrazione", "visivi", "montaggio"):
        if not passi[passo]:
            return redirect(url_for(f"reel_prod.{passo}", nome=nome))
    flash("Video pronto: manca solo la pubblicazione.", "successo")
    return redirect(url_for("reel_prod.pubblica", nome=nome))
