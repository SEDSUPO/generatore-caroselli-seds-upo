"""Stato di produzione di un reel: segmenti parlati, registrazioni, libreria di
immagini/video e timeline dei visivi. Salvato in reel/<nome>/produzione.json,
separato dal copione (dati.json) che resta rigenerabile.

Un "segmento" è una parte parlata registrata a sé: l'hook scelto, ogni scena,
la chiusura. Un "blocco" è una sequenza di segmenti consecutivi che condividono
lo stesso visivo (es. un video che scorre senza tagli sotto tre scene).
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import REEL_DIR
from .ffmpeg_utils import pulisci_voce
from .models import Asset, ProduzioneReel, Reel, StatoSegmento, Take
from .reel_script import formatta_teleprompter

_lock_file = threading.Lock()


@dataclass
class SegmentoParlato:
    chiave: str
    etichetta: str
    testo: str
    visivo_suggerito: str


@dataclass
class Blocco:
    segmenti: list[str]  # chiavi, in ordine
    asset: Asset | None
    inizio: float  # secondi dall'inizio del reel
    durata: float
    inizio_video: float
    riempi_schermo: bool = False
    inquadratura_x: float = 0.5


def cartella_reel(nome: str) -> Path:
    return REEL_DIR / nome


def carica_reel(nome: str) -> Reel | None:
    percorso = cartella_reel(nome) / "dati.json"
    if not percorso.is_file():
        return None
    return Reel.model_validate_json(percorso.read_text(encoding="utf-8"))


def salva_reel(reel: Reel) -> None:
    cartella = cartella_reel(reel.nome_reel)
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "dati.json").write_text(json.dumps(reel.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    (cartella / "copione.txt").write_text(formatta_teleprompter(reel), encoding="utf-8")


def elimina_file(percorso: Path) -> None:
    """Su Windows un file aperto (audio in riproduzione, video in montaggio) non si
    può cancellare: resta orfano su disco, ma l'operazione dell'utente va avanti."""
    try:
        percorso.unlink(missing_ok=True)
    except OSError:
        pass


def cartella_audio(nome: str) -> Path:
    return cartella_reel(nome) / "audio"


def cartella_media(nome: str) -> Path:
    return cartella_reel(nome) / "media"


def cartella_output(nome: str) -> Path:
    return cartella_reel(nome) / "output"


def segmenti_parlati(reel: Reel) -> list[SegmentoParlato]:
    segmenti = [SegmentoParlato("hook", "Hook", reel.hook_scelto, "Il gancio iniziale: il visivo più d'impatto")]
    for scena in reel.scene:
        segmenti.append(
            SegmentoParlato(f"scena-{scena.numero}", f"Scena {scena.numero}", scena.testo_parlato, scena.visivo_suggerito)
        )
    segmenti.append(SegmentoParlato("chiusura", "Chiusura", reel.chiusura_cta, "Chiusura con invito ad azione"))
    return segmenti


def carica_produzione(reel: Reel) -> ProduzioneReel:
    """Carica lo stato e lo allinea al copione attuale: aggiunge i segmenti nuovi
    e rimuove quelli che il copione non ha più (es. dopo una rigenerazione)."""
    percorso = cartella_reel(reel.nome_reel) / "produzione.json"
    if percorso.is_file():
        produzione = ProduzioneReel.model_validate_json(percorso.read_text(encoding="utf-8"))
    else:
        produzione = ProduzioneReel()

    chiavi_attuali = [s.chiave for s in segmenti_parlati(reel)]
    produzione.segmenti = {
        chiave: produzione.segmenti.get(chiave) or StatoSegmento(chiave=chiave) for chiave in chiavi_attuali
    }
    # Il primo segmento non ha un precedente da continuare.
    produzione.segmenti[chiavi_attuali[0]].continua_precedente = False
    return produzione


def salva_produzione(nome: str, produzione: ProduzioneReel) -> None:
    cartella = cartella_reel(nome)
    cartella.mkdir(parents=True, exist_ok=True)
    temporaneo = cartella / "produzione.json.tmp"
    with _lock_file:
        temporaneo.write_text(json.dumps(produzione.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporaneo.replace(cartella / "produzione.json")


_lock_per_reel: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
_lock_registro = threading.Lock()


@contextmanager
def modifica_produzione(reel: Reel):
    """Carica → modifica → salva sotto un lock per reel: le richieste web e i lavori
    in background (import, download YouTube, trascrizione) toccano lo stesso file e
    senza lock uno sovrascriverebbe le modifiche dell'altro."""
    with _lock_registro:
        lock = _lock_per_reel[reel.nome_reel]
    with lock:
        produzione = carica_produzione(reel)
        yield produzione
        salva_produzione(reel.nome_reel, produzione)


def rinomina_segmenti(nome: str, mappa: dict[str, str | None]) -> None:
    """Dopo aver aggiunto o tolto scene cambiano i numeri, e quindi le chiavi
    "scena-N": registrazioni, trascrizioni e visivi si spostano sulla nuova chiave
    invece di finire sulla scena sbagliata. `None` = scena eliminata: le sue
    riprese vengono cancellate. Le chiavi non nella mappa restano come sono.

    Lavora sul file grezzo: carica_produzione allinea già al copione nuovo e
    scarterebbe proprio le chiavi da rinominare."""
    percorso = cartella_reel(nome) / "produzione.json"
    with _lock_registro:
        lock = _lock_per_reel[nome]
    with lock:
        if not percorso.is_file():
            return
        produzione = ProduzioneReel.model_validate_json(percorso.read_text(encoding="utf-8"))
        rinominati: dict[str, StatoSegmento] = {}
        for chiave, stato in produzione.segmenti.items():
            destinazione = mappa.get(chiave, chiave)
            if destinazione is None:
                for take in stato.takes:
                    elimina_file(cartella_audio(nome) / take.file)
                continue
            stato.chiave = destinazione
            rinominati[destinazione] = stato
        produzione.segmenti = rinominati
        salva_produzione(nome, produzione)


def aggiungi_take(nome: str, produzione: ProduzioneReel, chiave: str, sorgente: Path, voce_ai: str | None = None) -> Take:
    """Ripulisce una registrazione (qualunque formato audio) o una voce generata e la
    aggiunge come nuova ripresa del segmento, selezionandola come quella attiva."""
    take_id = uuid.uuid4().hex[:10]
    nome_file = f"{chiave}_{take_id}.wav"
    durata = pulisci_voce(sorgente, cartella_audio(nome) / nome_file, riduci_rumore=voce_ai is None)
    if durata < 0.5:
        (cartella_audio(nome) / nome_file).unlink(missing_ok=True)
        raise ValueError("La registrazione sembra vuota o troppo corta: controlla il microfono e riprova.")

    take = Take(
        id=take_id, file=nome_file, durata=durata, creato_il=datetime.now().isoformat(timespec="seconds"), voce_ai=voce_ai
    )
    stato = produzione.segmenti[chiave]
    stato.takes.append(take)
    stato.take_scelto = take_id
    return take


def rimuovi_take(nome: str, produzione: ProduzioneReel, chiave: str, take_id: str) -> None:
    stato = produzione.segmenti[chiave]
    take = next((t for t in stato.takes if t.id == take_id), None)
    if take is None:
        return
    elimina_file(cartella_audio(nome) / take.file)
    stato.takes = [t for t in stato.takes if t.id != take_id]
    if stato.take_scelto == take_id:
        stato.take_scelto = stato.takes[-1].id if stato.takes else None


def azzera_produzione_copione(reel: Reel) -> None:
    """Dopo una rigenerazione del copione le registrazioni non corrispondono più al
    testo: si azzerano take, trascrizioni e timeline, ma si tiene la libreria media."""
    percorso = cartella_reel(reel.nome_reel) / "produzione.json"
    if not percorso.is_file():
        return
    vecchia = ProduzioneReel.model_validate_json(percorso.read_text(encoding="utf-8"))
    nuova = ProduzioneReel(
        asset=vecchia.asset,
        musica_file=vecchia.musica_file,
        musica_volume=vecchia.musica_volume,
        musica_titolo=vecchia.musica_titolo,
        musica_autore=vecchia.musica_autore,
        musica_licenza=vecchia.musica_licenza,
        musica_url=vecchia.musica_url,
        musica_attribuzione=vecchia.musica_attribuzione,
        sottotitoli_attivi=vecchia.sottotitoli_attivi,
        titolo_hook_attivo=vecchia.titolo_hook_attivo,
        titolo_copertina=vecchia.titolo_copertina,
        voce_motore=vecchia.voce_motore,
        voce_nome=vecchia.voce_nome,
        voce_velocita=vecchia.voce_velocita,
        voce_istruzioni=vecchia.voce_istruzioni,
    )
    salva_produzione(reel.nome_reel, nuova)


def calcola_blocchi(reel: Reel, produzione: ProduzioneReel) -> list[Blocco]:
    """Raggruppa i segmenti in blocchi visivi con i tempi calcolati dalle durate dei
    take scelti. Un segmento senza take conta come durata 0 (non ancora registrato)."""
    blocchi: list[Blocco] = []
    tempo = 0.0
    for segmento in segmenti_parlati(reel):
        stato = produzione.segmenti[segmento.chiave]
        take = stato.take_attivo()
        durata = take.durata if take else 0.0

        if stato.continua_precedente and blocchi:
            blocchi[-1].segmenti.append(segmento.chiave)
            blocchi[-1].durata += durata
        else:
            blocchi.append(
                Blocco(
                    segmenti=[segmento.chiave],
                    asset=produzione.asset_per_id(stato.asset_id),
                    inizio=tempo,
                    durata=durata,
                    inizio_video=stato.inizio_video,
                    riempi_schermo=stato.riempi_schermo,
                    inquadratura_x=stato.inquadratura_x,
                )
            )
        tempo += durata
    return blocchi


def durata_totale(reel: Reel, produzione: ProduzioneReel) -> float:
    return sum(b.durata for b in calcola_blocchi(reel, produzione))


def blocco_di(reel: Reel, produzione: ProduzioneReel, chiave: str) -> Blocco | None:
    return next((b for b in calcola_blocchi(reel, produzione) if chiave in b.segmenti), None)


_NOMI_FONTE = {"nasa": "NASA", "wikimedia": "Wikimedia Commons", "youtube": "YouTube", "caricato": "Materiale proprio"}


def asset_usati(reel: Reel, produzione: ProduzioneReel) -> list[Asset]:
    visti: dict[str, Asset] = {}
    for blocco in calcola_blocchi(reel, produzione):
        if blocco.asset and blocco.asset.id not in visti:
            visti[blocco.asset.id] = blocco.asset
    return list(visti.values())


def crediti_breve(reel: Reel, produzione: ProduzioneReel) -> str:
    """Una riga per la caption: chi ha fatto le immagini/video usati e con che licenza.
    Il materiale proprio non ha bisogno di crediti."""
    voci = []
    for asset in asset_usati(reel, produzione):
        if asset.fonte == "caricato":
            continue
        voce = asset.autore or _NOMI_FONTE[asset.fonte]
        if asset.licenza:
            voce += f" ({asset.licenza})"
        if asset.fonte != "nasa" and _NOMI_FONTE[asset.fonte] not in voce:
            voce = f"{_NOMI_FONTE[asset.fonte]}: {voce}"
        if voce not in voci:
            voci.append(voce)
    righe = []
    if voci:
        righe.append("Crediti immagini e video: " + "; ".join(voci))
    if produzione.musica_file and produzione.musica_licenza:
        righe.append("Musica: " + credito_musica(produzione))
    return "\n".join(righe)


def credito_musica(produzione: ProduzioneReel) -> str:
    from .musica_libera import credito

    return credito(produzione.musica_titolo, produzione.musica_autore, produzione.musica_licenza)


def voci_ai_usate(produzione: ProduzioneReel) -> list[str]:
    """Voci sintetiche nelle riprese scelte: per il promemoria dell'etichetta AI su Instagram."""
    voci = []
    for stato in produzione.segmenti.values():
        take = stato.take_attivo()
        if take and take.voce_ai and take.voce_ai not in voci:
            voci.append(take.voce_ai)
    return voci


def crediti_completi(reel: Reel, produzione: ProduzioneReel) -> str:
    righe = []
    for asset in asset_usati(reel, produzione):
        righe.append(
            f"- {asset.titolo} | fonte: {_NOMI_FONTE[asset.fonte]} | autore: {asset.autore or 'n/d'} | "
            f"licenza: {asset.licenza or 'n/d'} | {asset.url_origine or ''}".rstrip(" |")
        )
    if produzione.musica_file and produzione.musica_licenza:
        righe.append(
            f"- Musica: {produzione.musica_attribuzione or credito_musica(produzione)}"
            + (f" | {produzione.musica_url}" if produzione.musica_url else "")
        )
    for voce in voci_ai_usate(produzione):
        righe.append(f"- Voce generata con sintesi vocale AI: {voce}")
    return "\n".join(righe)


def mancanze(reel: Reel, produzione: ProduzioneReel) -> list[str]:
    """Cosa manca per poter montare il reel, in parole semplici per l'interfaccia."""
    problemi = []
    etichette = {s.chiave: s.etichetta for s in segmenti_parlati(reel)}
    for chiave, stato in produzione.segmenti.items():
        if stato.take_attivo() is None:
            problemi.append(f"{etichette[chiave]}: manca la registrazione")
    for blocco in calcola_blocchi(reel, produzione):
        if blocco.asset is None:
            nomi = ", ".join(etichette[c] for c in blocco.segmenti)
            problemi.append(f"{nomi}: manca il visivo")
    return problemi
