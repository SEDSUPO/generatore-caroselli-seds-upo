"""Aggiorna l'app all'ultima versione pubblicata su GitHub (o da uno ZIP).

Di solito lo avvia l'app stessa, dal pulsante "Aggiorna" in Impostazioni. Si può
anche usare a mano (doppio clic su aggiorna.bat):
    python aggiorna_app.py                 # ultima versione da GitHub (o ZIP in Download/Desktop)
    python aggiorna_app.py percorso.zip    # da uno ZIP del codice
    python aggiorna_app.py --ripristina    # torna alla versione precedente
Opzioni usate dall'app: --automatico (nessuna domanda), --riavvia (riapre l'app alla fine).

Cosa fa:
1. controlla che l'app sia chiusa (con --automatico aspetta che si chiuda da sola);
2. salva una copia del codice attuale in .aggiornamenti/, per poter tornare indietro;
3. sostituisce solo il codice. Non tocca MAI: .env (la chiave), i lavori (caroselli,
   notizie, reel), i modelli scaricati, il Python dell'app (.venv o python\\) e le
   impostazioni personali (config.yaml, feeds.yaml), prese dallo ZIP solo se mancano;
4. reinstalla le librerie solo se requirements.txt è cambiato.

Solo libreria standard: deve funzionare anche se un aggiornamento venuto male ha
rotto le librerie dell'app.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY_GITHUB = "SEDSUPO/generatore-caroselli-seds-upo"
NOME_CARTELLA = "Generatore_caroselli_SEDS_UPO"
NOME_ZIP_CODICE = f"{NOME_CARTELLA}_codice.zip"
CARTELLA_BACKUP = ROOT / ".aggiornamenti"
BACKUP_DA_TENERE = 3
PORTA_APP = int(os.environ.get("GENERATORE_PORTA", "5000"))

# Mai sovrascritti né cancellati: dati, chiave, modelli, ambiente Python.
PROTETTI = {
    ".env", ".venv", "venv", "python", ".aggiornamenti", ".stato", "modelli",
    "reel", "prompts", "input_immagini", "output",
    "notizie", "input_immagini_notizie", "output_notizie",
}
# Impostazioni personali: si prendono dallo ZIP solo se mancano.
PERSONALI = {"config.yaml", "feeds.yaml"}
# Cartelle di solo codice: i file che la nuova versione non ha più vengono tolti
# (spostati nel backup), altrimenti un modulo eliminato resterebbe in giro.
CARTELLE_CODICE = ("src", "webapp", "assets")
# File della cartella principale che versioni precedenti avevano e ora non servono più
# (nella cartella principale non si tolgono file sconosciuti: potrebbero essere dell'utente).
FILE_OBSOLETI = ("esporta_app.py", "esporta_app.bat")


class ErroreAggiornamento(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Versioni
# ---------------------------------------------------------------------------


def versione_locale() -> str:
    percorso = ROOT / "versione.txt"
    return percorso.read_text(encoding="utf-8").strip() if percorso.is_file() else "sviluppo"


def e_piu_recente(nuova: str, attuale: str) -> bool:
    """Versioni "AAAA.MM.GG-HHMM": si confrontano come testo. Una copia di sviluppo
    (senza versione) non viene mai aggiornata in automatico."""
    return attuale not in ("sviluppo", "sconosciuta") and nuova > attuale


NOME_NOTA = "nota.txt"


def _apri(url: str, timeout: float):
    richiesta = urllib.request.Request(url, headers={"User-Agent": "Generatore-Caroselli-SEDS-UPO"})
    return urllib.request.urlopen(richiesta, timeout=timeout)


def ultima_versione_github(timeout: float = 15) -> dict:
    """{versione, note, pagina, url_codice} dell'ultima versione pubblicata.

    Senza l'API di GitHub, che senza login concede 60 richieste l'ora per connessione:
    su una rete condivisa (es. università) il limite si esaurisce in fretta. Si usano
    invece gli indirizzi pubblici, senza limiti: /releases/latest rimanda alla pagina
    dell'ultima versione (/releases/tag/vX), e i file allegati si scaricano da
    /releases/download/vX/<nome>. La nota della versione è allegata come nota.txt.
    """
    base = f"https://github.com/{REPOSITORY_GITHUB}/releases"
    try:
        with _apri(f"{base}/latest", timeout) as risposta:
            indirizzo_finale = risposta.geturl()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ErroreAggiornamento("Repository non trovato su GitHub.") from e
        raise ErroreAggiornamento(f"GitHub non risponde come previsto (errore {e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErroreAggiornamento("GitHub non raggiungibile: controlla la connessione.") from e

    trovato = re.search(r"/releases/tag/([^/?#]+)$", indirizzo_finale)
    if not trovato:  # senza versioni pubblicate GitHub rimanda all'elenco vuoto
        raise ErroreAggiornamento("Nessuna versione pubblicata su GitHub.")
    etichetta = urllib.parse.unquote(trovato.group(1))

    note = ""
    try:
        with _apri(f"{base}/download/{etichetta}/{NOME_NOTA}", timeout) as risposta:
            note = risposta.read().decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        pass  # versioni pubblicate prima che esistesse la nota
    return {
        "versione": etichetta.removeprefix("v"),
        "note": note,
        "pagina": f"{base}/tag/{etichetta}",
        "url_codice": f"{base}/download/{etichetta}/{NOME_ZIP_CODICE}",
    }


def scarica(url: str, destinazione: Path) -> Path:
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    richiesta = urllib.request.Request(url, headers={"User-Agent": "Generatore-Caroselli-SEDS-UPO"})
    try:
        with urllib.request.urlopen(richiesta, timeout=120) as risposta, open(destinazione, "wb") as uscita:
            shutil.copyfileobj(risposta, uscita)
    except urllib.error.HTTPError as e:
        destinazione.unlink(missing_ok=True)
        if e.code == 404:
            raise ErroreAggiornamento("Il pacchetto della nuova versione non è ancora pronto su GitHub: riprova tra qualche minuto.") from e
        raise ErroreAggiornamento(f"Download non riuscito (errore {e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        destinazione.unlink(missing_ok=True)
        raise ErroreAggiornamento(f"Download non riuscito: {e}") from e
    return destinazione


def versione_zip(percorso: Path) -> str | None:
    """None se il file non è uno ZIP del codice dell'app."""
    try:
        with zipfile.ZipFile(percorso) as archivio:
            nomi = set(archivio.namelist())
            if f"{NOME_CARTELLA}/run_app.py" not in nomi or f"{NOME_CARTELLA}/versione.txt" not in nomi:
                return None
            return archivio.read(f"{NOME_CARTELLA}/versione.txt").decode("utf-8").strip()
    except (zipfile.BadZipFile, OSError):
        return None


def _trova_zip_locale() -> Path | None:
    candidati = []
    for cartella in (Path.home() / "Downloads", Path.home() / "Desktop", ROOT.parent):
        if cartella.is_dir():
            for percorso in cartella.glob(f"{NOME_CARTELLA}*.zip"):
                versione = versione_zip(percorso)
                if versione:
                    candidati.append((versione, percorso))
    return max(candidati)[1] if candidati else None


# ---------------------------------------------------------------------------
# App aperta / riavvio
# ---------------------------------------------------------------------------


def _cartelle_python_app() -> list[str]:
    return [str(p).lower() for p in (ROOT / ".venv", ROOT / "python") if p.is_dir()]


def app_aperta() -> bool:
    """Aperta se c'è un processo Python avviato dal Python dell'app (.venv o python\\)
    che non sia questo aggiornamento. Con il Python di sistema (copia di sviluppo) non
    si può distinguere l'app dagli altri programmi: si guarda se risponde la sua porta."""
    cartelle = _cartelle_python_app()
    if not cartelle:
        with socket.socket() as prova:
            prova.settimeout(1)
            return prova.connect_ex(("127.0.0.1", PORTA_APP)) == 0
    if sys.platform != "win32":
        return False
    comando = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "ForEach-Object { $_.ExecutablePath + '|' + $_.CommandLine }"
    )
    try:
        risultato = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    for riga in risultato.stdout.splitlines():
        eseguibile, _, comando_riga = riga.partition("|")
        if any(eseguibile.lower().startswith(c) for c in cartelle) and "aggiorna_app.py" not in comando_riga:
            return True
    return False


def _python_app() -> list[str]:
    for candidato in (ROOT / "python" / "python.exe", ROOT / ".venv" / "Scripts" / "python.exe"):
        if candidato.is_file():
            return [str(candidato)]
    return [sys.executable]


def ambiente_pulito(**aggiunte: str) -> dict[str, str]:
    """Variabili d'ambiente senza quelle interne del riavvio automatico di Werkzeug:
    ereditate dall'app appena chiusa, farebbero fallire l'avvio della nuova."""
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith("WERKZEUG_")}
    ambiente.update(aggiunte)
    return ambiente


def riavvia_app() -> None:
    ambiente = ambiente_pulito(NON_APRIRE_BROWSER="1")  # la pagina dell'app si ricarica da sola
    subprocess.Popen(["cmd", "/c", "start", "", str(ROOT / "avvia_app.bat")], cwd=ROOT, env=ambiente)


# ---------------------------------------------------------------------------
# Aggiornamento
# ---------------------------------------------------------------------------


def _chiedi(domanda: str, automatico: bool) -> bool:
    return True if automatico else input(f"{domanda} [s/n] ").strip().lower() in ("s", "si", "sì", "y")


def _impronta(percorso: Path) -> str:
    return hashlib.sha256(percorso.read_bytes()).hexdigest() if percorso.is_file() else ""


def _attendi_chiusura(automatico: bool) -> None:
    if automatico:
        limite = time.time() + 90
        while app_aperta():
            if time.time() > limite:
                raise ErroreAggiornamento("L'app non si è chiusa: chiudi la finestra di avvia_app.bat e riprova.")
            time.sleep(1)
        return
    while app_aperta():
        print("\nL'app è aperta: chiudi la finestra nera di avvia_app.bat.")
        input("Quando l'hai chiusa premi Invio per continuare... ")


def _installa_librerie() -> None:
    print("Le librerie sono cambiate: le aggiorno, attendi...\n")
    esito = subprocess.run([*_python_app(), "-m", "pip", "install", "-r", "requirements.txt", "--disable-pip-version-check"], cwd=ROOT)
    if esito.returncode != 0:
        raise ErroreAggiornamento("Installazione delle librerie non riuscita: controlla la connessione e riprova.")


def applica_zip(percorso_zip: Path, automatico: bool = False) -> str:
    nuova = versione_zip(percorso_zip)
    if nuova is None:
        raise ErroreAggiornamento(f"{percorso_zip} non è uno ZIP del codice del Generatore Caroselli SEDS UPO.")
    attuale = versione_locale()
    print(f"Versione installata: {attuale}")
    print(f"Nuova versione:      {nuova}")
    if not e_piu_recente(nuova, attuale) and not _chiedi("Non è più recente di quella installata. Aggiornare comunque?", automatico):
        return attuale

    _attendi_chiusura(automatico)
    backup = CARTELLA_BACKUP / f"{datetime.now():%Y%m%d-%H%M%S}_da_{attuale}"
    backup.mkdir(parents=True)
    requisiti_prima = _impronta(ROOT / "requirements.txt")

    with zipfile.ZipFile(percorso_zip) as archivio:
        nuovi: set[str] = set()
        for voce in archivio.infolist():
            if voce.is_dir() or not voce.filename.startswith(f"{NOME_CARTELLA}/"):
                continue
            relativo = voce.filename[len(NOME_CARTELLA) + 1:]
            if not relativo or relativo.split("/")[0] in PROTETTI:
                continue
            nuovi.add(relativo)
            destinazione = ROOT / relativo
            if relativo in PERSONALI and destinazione.exists():
                continue
            if destinazione.is_file():
                copia = backup / relativo
                copia.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destinazione, copia)
            destinazione.parent.mkdir(parents=True, exist_ok=True)
            with archivio.open(voce) as sorgente, open(destinazione, "wb") as uscita:
                shutil.copyfileobj(sorgente, uscita)

    for cartella in CARTELLE_CODICE:
        for percorso in (ROOT / cartella).rglob("*"):
            if percorso.is_file() and "__pycache__" not in percorso.parts:
                relativo = percorso.relative_to(ROOT).as_posix()
                if relativo not in nuovi:
                    copia = backup / relativo
                    copia.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(percorso), copia)
    for nome in FILE_OBSOLETI:
        if (ROOT / nome).is_file() and nome not in nuovi:
            shutil.move(str(ROOT / nome), backup / nome)
    for cache in ROOT.rglob("__pycache__"):
        if not (set(cache.relative_to(ROOT).parts) & PROTETTI):
            shutil.rmtree(cache, ignore_errors=True)

    (backup / "versione_precedente.txt").write_text(attuale, encoding="utf-8")
    for vecchio in sorted(p for p in CARTELLA_BACKUP.iterdir() if p.is_dir() and p.name[:1].isdigit())[:-BACKUP_DA_TENERE]:
        shutil.rmtree(vecchio, ignore_errors=True)
    print(f"Codice aggiornato (copia della versione precedente in {backup.relative_to(ROOT)}).")

    if _impronta(ROOT / "requirements.txt") != requisiti_prima:
        _installa_librerie()
    else:
        print("Librerie invariate: niente da reinstallare.")
    print(f"\nFatto: ora hai la versione {nuova}.")
    return nuova


def aggiorna_da_github(automatico: bool = False) -> str:
    print("Cerco l'ultima versione su GitHub...")
    ultima = ultima_versione_github()
    attuale = versione_locale()
    if not e_piu_recente(ultima["versione"], attuale):
        print(f"Hai già l'ultima versione ({attuale}).")
        if automatico or not _chiedi("Reinstallarla comunque?", automatico=False):
            return attuale
    zip_scaricato = scarica(ultima["url_codice"], CARTELLA_BACKUP / "scaricati" / f"{NOME_CARTELLA}_{ultima['versione']}_codice.zip")
    return applica_zip(zip_scaricato, automatico)


def ripristina(automatico: bool = False) -> str:
    backup = sorted(p for p in CARTELLA_BACKUP.iterdir() if p.is_dir() and p.name[:1].isdigit()) if CARTELLA_BACKUP.is_dir() else []
    if not backup:
        raise ErroreAggiornamento("Nessuna versione precedente salvata.")
    ultimo = backup[-1]
    precedente = (ultimo / "versione_precedente.txt").read_text(encoding="utf-8").strip()
    if not _chiedi(f"Tornare alla versione {precedente}?", automatico):
        return versione_locale()
    _attendi_chiusura(automatico)
    requisiti_prima = _impronta(ROOT / "requirements.txt")
    for percorso in ultimo.rglob("*"):
        if percorso.is_file() and percorso.name != "versione_precedente.txt":
            destinazione = ROOT / percorso.relative_to(ultimo)
            destinazione.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(percorso, destinazione)
    (ROOT / "versione.txt").write_text(precedente + "\n", encoding="utf-8")
    shutil.rmtree(ultimo, ignore_errors=True)
    if _impronta(ROOT / "requirements.txt") != requisiti_prima:
        _installa_librerie()
    print(f"Fatto: tornato alla versione {precedente}.")
    return precedente


def main() -> int:
    argomenti = [a for a in sys.argv[1:] if not a.startswith("--")]
    automatico = "--automatico" in sys.argv
    riavvia = "--riavvia" in sys.argv
    print("Aggiornamento del Generatore Caroselli SEDS UPO\n")
    try:
        if "--ripristina" in sys.argv:
            ripristina(automatico)
        elif argomenti:
            applica_zip(Path(argomenti[0]), automatico)
        else:
            try:
                aggiorna_da_github(automatico)
            except ErroreAggiornamento as e:
                zip_locale = None if automatico else _trova_zip_locale()
                if zip_locale is None:
                    raise
                print(f"{e}\nUso lo ZIP trovato: {zip_locale}")
                applica_zip(zip_locale, automatico)
        esito = 0
    except ErroreAggiornamento as e:
        print(f"\nERRORE: {e}")
        esito = 1

    if riavvia:
        print("\nRiapro l'app...")
        riavvia_app()
        if esito:
            input("Premi Invio per chiudere questa finestra... ")
        else:
            time.sleep(3)
    return esito


if __name__ == "__main__":
    sys.exit(main())
