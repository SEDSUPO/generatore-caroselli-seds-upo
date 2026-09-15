"""Route Flask per il Creatore Reel, parte copione: scelta/suggerimento argomento,
generazione del copione (hook + scene + CTA), modifica, caption, download ZIP,
eliminazione. Registrazione, visivi, montaggio e copertina sono in
webapp/reel_produzione.py.
"""

from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import lavori
from src.caption import formatta_caption, genera_caption
from src.config import REEL_DIR, carica_config_opzionale
from src.durata_parlato import formatta_durata, stima_secondi
from src.models import Reel, ScenaReel
from src.reel_produzione import (
    azzera_produzione_copione,
    carica_produzione,
    carica_reel,
    cartella_output,
    cartella_reel,
    crediti_breve,
    crediti_completi,
    rinomina_segmenti,
    salva_reel,
)
from src.reel_script import formatta_teleprompter, genera_copione
from src.reel_topic import suggerisci_topic
from webapp.reel_comune import stato_passi
from webapp.utils import NOME_PATTERN, slugify

bp = Blueprint("reel", __name__)


def _testo_per_caption(reel: Reel) -> str:
    """Testo grezzo da passare al generatore di caption (riuso di caption.py,
    scritto per un testo di ricerca, ma abbastanza generico da adattarsi)."""
    return (
        f"Argomento: {reel.argomento}\n\n"
        f"{reel.hook_scelto}\n\n" + "\n".join(s.testo_parlato for s in reel.scene) + f"\n\n{reel.chiusura_cta}"
    )


def elenca_reel() -> list[dict]:
    risultati = []
    if not REEL_DIR.is_dir():
        return risultati
    for cartella in sorted(REEL_DIR.iterdir(), reverse=True):
        try:
            reel = carica_reel(cartella.name) if (cartella / "dati.json").is_file() else None
        except ValueError:  # dati.json illeggibile: si salta invece di bloccare la home
            continue
        if reel is None:
            continue
        secondi = stima_secondi(reel.testo_completo())
        risultati.append(
            {
                "nome": reel.nome_reel,
                "argomento": reel.argomento,
                "numero_scene": len(reel.scene),
                "durata": formatta_durata(secondi),
                "durata_ok": secondi >= 55,
                "video_pronto": (cartella_output(reel.nome_reel) / "reel.mp4").is_file(),
            }
        )
    return risultati


# ---------------------------------------------------------------------------
# Suggerimento argomento (indipendente da un reel specifico)
# ---------------------------------------------------------------------------


@bp.route("/api/reel/suggerisci-topic", methods=["POST"])
def suggerisci_topic_route():
    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    try:
        topic = suggerisci_topic(config)
    except Exception as e:  # noqa: BLE001 - errori Gemini/quota mostrati all'utente
        return (
            jsonify(
                ok=False,
                messaggio=f"Impossibile suggerire argomenti in questo momento: {e}",
            ),
            502,
        )

    return jsonify(ok=True, topic=[t.model_dump() for t in topic])


# ---------------------------------------------------------------------------
# Creazione + eliminazione
# ---------------------------------------------------------------------------


@bp.route("/reel/nuovo", methods=["POST"])
def nuovo_reel():
    titolo = request.form.get("nome", "").strip()
    argomento = request.form.get("argomento", "").strip()
    nome = slugify(titolo)

    if not NOME_PATTERN.match(nome):
        flash("Inserisci un titolo per il reel.", "errore")
        return redirect(url_for("home"))
    if not argomento:
        flash("Inserisci un argomento (scritto a mano o scelto tra i suggerimenti).", "errore")
        return redirect(url_for("home"))
    if (REEL_DIR / nome / "dati.json").is_file():
        flash(f"Esiste già un reel chiamato '{nome}'.", "errore")
        return redirect(url_for("home"))

    config = carica_config_opzionale()
    try:
        copione = genera_copione(config, argomento)
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        flash(f"Errore nella generazione del copione: {e}", "errore")
        return redirect(url_for("home"))

    reel = Reel(
        nome_reel=nome,
        argomento=argomento,
        hook_varianti=copione.hook_varianti,
        hook_scelto=copione.hook_varianti[0],
        scene=copione.scene,
        chiusura_cta=copione.chiusura_cta,
    )
    salva_reel(reel)

    flash(f"Reel '{nome}' creato con {len(reel.scene)} scene.", "successo")
    return redirect(url_for("reel.dettaglio", nome=nome))


@bp.route("/api/reel/<nome>", methods=["DELETE"])
def elimina_reel(nome):
    if not NOME_PATTERN.match(nome):
        return jsonify(ok=False, messaggio="Nome non valido."), 400

    cartella = REEL_DIR / nome
    if not cartella.is_dir():
        return jsonify(ok=False, messaggio="Reel non trovato."), 404

    lavoro = lavori.stato(f"monta:{nome}")
    if lavoro and lavoro.stato == "in_corso":
        return jsonify(ok=False, messaggio="C'è un montaggio in corso per questo reel: attendi che finisca."), 409
    try:
        shutil.rmtree(cartella)
    except OSError:
        return jsonify(ok=False, messaggio="Alcuni file del reel sono in uso: chiudi i video aperti e riprova."), 409
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Copione: dettaglio + modifica
# ---------------------------------------------------------------------------


@bp.route("/reel/<nome>")
def dettaglio(nome):
    reel = carica_reel(nome)
    if reel is None:
        flash("Reel non trovato.", "errore")
        return redirect(url_for("home"))

    durata_totale = stima_secondi(reel.testo_completo())
    produzione = carica_produzione(reel)
    scene_view = [
        {
            "scena": s,
            "durata": formatta_durata(stima_secondi(s.testo_parlato)),
            "registrata": bool(produzione.segmenti[f"scena-{s.numero}"].takes),
            "nuova": s.testo_parlato == TESTO_NUOVA_SCENA,
        }
        for s in reel.scene
    ]

    return render_template(
        "reel.html",
        reel=reel,
        scene_view=scene_view,
        durata_totale=formatta_durata(durata_totale),
        durata_ok=durata_totale >= 55,
        passi=stato_passi(reel),
        ha_registrazioni=any(stato.takes for stato in produzione.segmenti.values()),
    )


@bp.route("/api/reel/<nome>/hook", methods=["POST"])
def imposta_hook(nome):
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404

    hook = request.form.get("hook", "").strip()
    if hook not in reel.hook_varianti:
        return jsonify(ok=False, messaggio="Variante hook non riconosciuta."), 400

    cambiato = hook != reel.hook_scelto
    reel.hook_scelto = hook
    salva_reel(reel)

    avviso = ""
    if cambiato and carica_produzione(reel).segmenti["hook"].takes:
        avviso = "Hai già registrato l'hook precedente: ricordati di registrare di nuovo quello nuovo."
    return jsonify(ok=True, avviso=avviso)


@bp.route("/api/reel/<nome>/scena/<int:numero>", methods=["POST"])
def modifica_scena(nome, numero):
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404
    scena = next((s for s in reel.scene if s.numero == numero), None)
    if scena is None:
        return jsonify(ok=False, messaggio="Scena non trovata."), 404

    testo_parlato = request.form.get("testo_parlato", "").strip()
    visivo_suggerito = request.form.get("visivo_suggerito", "").strip()
    if not testo_parlato:
        return jsonify(ok=False, messaggio="Il testo della scena non può essere vuoto."), 400

    scena.testo_parlato = testo_parlato
    scena.visivo_suggerito = visivo_suggerito
    salva_reel(reel)

    durata_totale = stima_secondi(reel.testo_completo())
    return jsonify(
        ok=True,
        durata_scena=formatta_durata(stima_secondi(testo_parlato)),
        durata_totale=formatta_durata(durata_totale),
        durata_ok=durata_totale >= 55,
    )


TESTO_NUOVA_SCENA = "Nuova scena: scrivi qui cosa dire."


def _rinumera_scene(reel: Reel, eliminate: list[int]) -> None:
    """Numera di nuovo le scene 1..N nell'ordine della lista e sposta sulle nuove
    chiavi lo stato di produzione (registrazioni, visivi) delle scene esistenti."""
    mappa: dict[str, str | None] = {f"scena-{numero}": None for numero in eliminate}
    for posizione, scena in enumerate(reel.scene, start=1):
        if scena.numero > 0:  # le scene appena inserite hanno numero 0: nessuno stato da spostare
            mappa[f"scena-{scena.numero}"] = f"scena-{posizione}"
        scena.numero = posizione
    rinomina_segmenti(reel.nome_reel, mappa)
    salva_reel(reel)


@bp.route("/api/reel/<nome>/scena/aggiungi", methods=["POST"])
def aggiungi_scena(nome):
    """Inserisce una scena vuota dopo la scena `dopo` (0 = subito dopo l'hook)."""
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404
    try:
        dopo = int(request.form.get("dopo", ""))
    except ValueError:
        return jsonify(ok=False, messaggio="Posizione non valida."), 400
    if not 0 <= dopo <= len(reel.scene):
        return jsonify(ok=False, messaggio="Posizione non valida."), 400

    reel.scene.insert(dopo, ScenaReel(numero=0, testo_parlato=TESTO_NUOVA_SCENA, visivo_suggerito=""))
    _rinumera_scene(reel, eliminate=[])
    return jsonify(ok=True, numero=dopo + 1)


@bp.route("/api/reel/<nome>/scena/<int:numero>", methods=["DELETE"])
def elimina_scena(nome, numero):
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404
    if not any(s.numero == numero for s in reel.scene):
        return jsonify(ok=False, messaggio="Scena non trovata."), 404
    if len(reel.scene) <= 1:
        return jsonify(ok=False, messaggio="Il reel deve avere almeno una scena."), 400

    reel.scene = [s for s in reel.scene if s.numero != numero]
    _rinumera_scene(reel, eliminate=[numero])
    return jsonify(ok=True)


@bp.route("/api/reel/<nome>/rigenera", methods=["POST"])
def rigenera(nome):
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    try:
        copione = genera_copione(config, reel.argomento)
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Errore nella generazione del copione: {e}"), 500

    reel.hook_varianti = copione.hook_varianti
    reel.hook_scelto = copione.hook_varianti[0]
    reel.scene = copione.scene
    reel.chiusura_cta = copione.chiusura_cta
    salva_reel(reel)
    # Le registrazioni del vecchio copione non corrispondono più al testo.
    azzera_produzione_copione(reel)

    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Caption + download
# ---------------------------------------------------------------------------


@bp.route("/api/reel/<nome>/caption", methods=["POST"])
def genera_caption_route(nome):
    reel = carica_reel(nome)
    if reel is None:
        return jsonify(ok=False, messaggio="Reel non trovato."), 404

    config = carica_config_opzionale()
    if config is None:
        return jsonify(ok=False, messaggio="API key non configurata."), 400

    nome_autore = request.form.get("autore", "")

    try:
        caption = genera_caption(config, _testo_per_caption(reel))
    except Exception as e:  # noqa: BLE001 - errori Gemini mostrati all'utente
        return jsonify(ok=False, messaggio=f"Errore nella generazione della caption: {e}"), 500

    crediti = crediti_breve(reel, carica_produzione(reel))
    if crediti:
        caption.testo = f"{caption.testo.rstrip()}\n\n{crediti}"
    testo_finale = formatta_caption(caption, nome_autore)

    cartella = cartella_reel(nome)
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "caption.txt").write_text(testo_finale, encoding="utf-8")

    return jsonify(
        ok=True, caption=testo_finale, lunghezza=len(testo_finale), limite=config.max_caratteri_caption
    )


@bp.route("/reel/<nome>/scarica")
def scarica(nome):
    reel = carica_reel(nome)
    if reel is None:
        flash("Reel non trovato.", "errore")
        return redirect(url_for("home"))

    cartella = cartella_reel(nome)
    uscita = cartella_output(nome)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for percorso, nome_nello_zip in [
            (uscita / "reel.mp4", "reel.mp4"),
            (uscita / "copertina.jpg", "copertina.jpg"),
            (cartella / "caption.txt", "caption.txt"),
        ]:
            if percorso.is_file():
                zf.write(percorso, arcname=nome_nello_zip)
        zf.writestr("copione.txt", formatta_teleprompter(reel))
        crediti = crediti_completi(reel, carica_produzione(reel))
        if crediti:
            zf.writestr("crediti.txt", crediti + "\n")
    buffer.seek(0)

    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=f"{nome}_reel.zip")
