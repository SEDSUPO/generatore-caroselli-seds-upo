"""Crea i pacchetti di una versione dell'app (lo esegue GitHub Actions a ogni
nuova versione, vedi .github/workflows/pubblica.yml; si può lanciare anche a mano
per provarli).

    python strumenti/crea_pacchetti.py --versione 2026.09.15-1430 [--uscita dist] [--solo-codice]

Pacchetti prodotti:
- Generatore_caroselli_SEDS_UPO_codice.zip
    solo il codice (pochi MB): lo usa il pulsante "Aggiorna" dell'app, oppure chi
    preferisce installare con installa.bat e il proprio Python.
- Generatore_caroselli_SEDS_UPO_windows.zip
    pronto all'uso: codice + Python "embeddable" di python.org + tutte le librerie
    già installate + le DLL del runtime Visual C++. Chi lo scarica non installa
    niente: estrae e fa doppio clic su avvia_app.bat.

I file inclusi sono quelli del repository (tracciati o non ignorati da .gitignore),
quindi mai chiavi o dati personali, esclusi gli strumenti per chi sviluppa.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOME_CARTELLA = "Generatore_caroselli_SEDS_UPO"
# Servono solo a chi sviluppa e pubblica: fuori dai pacchetti per i collaboratori.
ESCLUSI = (".github/", "strumenti/", ".gitignore", "pubblica.bat")
# DLL del runtime Visual C++ che alcune librerie native (onnxruntime, ctranslate2)
# si aspettano di trovare nel sistema: copiandole accanto a python.exe l'app parte
# anche sui PC senza il "Visual C++ Redistributable" installato.
DLL_RUNTIME_VC = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")


def file_del_repository() -> list[str]:
    risultato = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    file = sorted({riga.strip() for riga in risultato.stdout.splitlines() if riga.strip()})
    return [f for f in file if not f.startswith(ESCLUSI) and (ROOT / f).is_file()]


def crea_zip_codice(file: list[str], versione: str, destinazione: Path) -> None:
    with zipfile.ZipFile(destinazione, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archivio:
        for relativo in file:
            archivio.write(ROOT / relativo, f"{NOME_CARTELLA}/{relativo}")
        archivio.writestr(f"{NOME_CARTELLA}/versione.txt", versione + "\n")


def _prepara_python_portatile(cartella_python: Path) -> None:
    versione_python = ".".join(map(str, sys.version_info[:3]))
    url = f"https://www.python.org/ftp/python/{versione_python}/python-{versione_python}-embed-amd64.zip"
    print(f"Scarico Python {versione_python} embeddable...")
    with urllib.request.urlopen(url, timeout=120) as risposta:
        zipfile.ZipFile(io.BytesIO(risposta.read())).extractall(cartella_python)

    # Il file ._pth decide da dove Python importa: si aggiungono le librerie installate
    # e la cartella dell'app (quella sopra python\), poi si abilita il modulo site.
    nome_pth = f"python{sys.version_info[0]}{sys.version_info[1]}._pth"
    (cartella_python / nome_pth).write_text(
        f"python{sys.version_info[0]}{sys.version_info[1]}.zip\n.\nLib\\site-packages\n..\nimport site\n",
        encoding="utf-8",
    )

    sistema = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for dll in DLL_RUNTIME_VC:
        if (sistema / dll).is_file() and not (cartella_python / dll).exists():
            shutil.copy2(sistema / dll, cartella_python / dll)


def crea_zip_portatile(file: list[str], versione: str, lavoro: Path, destinazione: Path) -> None:
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Il pacchetto pronto all'uso si crea solo con Python Windows a 64 bit.")
    app = lavoro / NOME_CARTELLA
    shutil.rmtree(lavoro, ignore_errors=True)
    app.mkdir(parents=True)
    for relativo in file:
        (app / relativo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relativo, app / relativo)
    (app / "versione.txt").write_text(versione + "\n", encoding="utf-8")

    cartella_python = app / "python"
    _prepara_python_portatile(cartella_python)

    print("Installo le librerie nel Python portatile...")
    site_packages = cartella_python / "Lib" / "site-packages"
    # Stessa versione di Python del pacchetto: le librerie compilate sono compatibili.
    # Si installa anche pip, così l'app può aggiornare yt-dlp e le librerie da sola.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(site_packages), "--no-warn-script-location",
         "--disable-pip-version-check", "-r", str(app / "requirements.txt"), "pip"],
        check=True,
    )

    print("Verifico che l'app parta con il Python portatile...")
    subprocess.run(
        [str(cartella_python / "python.exe"), "-c",
         "import webapp.app, faster_whisper, kokoro_onnx, ctranslate2, onnxruntime, imageio_ffmpeg, yt_dlp; print('ok')"],
        cwd=app, check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    # Le cache create dalla verifica (e da pip) non vanno nel pacchetto: si rigenerano da sole.
    for cache in [p for p in app.rglob("__pycache__") if "python" not in p.relative_to(app).parts[:1]]:
        shutil.rmtree(cache, ignore_errors=True)

    print("Comprimo il pacchetto...")
    if destinazione.exists():
        destinazione.unlink()
    shutil.make_archive(str(destinazione.with_suffix("")), "zip", root_dir=lavoro, base_dir=NOME_CARTELLA)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--versione", required=True)
    parser.add_argument("--uscita", default="dist")
    parser.add_argument("--solo-codice", action="store_true")
    argomenti = parser.parse_args()

    versione = argomenti.versione.removeprefix("v")
    uscita = (ROOT / argomenti.uscita).resolve()
    uscita.mkdir(parents=True, exist_ok=True)
    file = file_del_repository()
    vietati = [f for f in file if Path(f).name == ".env" or f.split("/")[0] in {"reel", "prompts", "output", "modelli"}]
    if vietati:
        raise SystemExit(f"STOP: file personali tra quelli del repository: {vietati}")

    codice = uscita / f"{NOME_CARTELLA}_codice.zip"
    crea_zip_codice(file, versione, codice)
    print(f"Creato {codice.name}: {len(file)} file, {codice.stat().st_size / 1024 / 1024:.1f} MB")

    if not argomenti.solo_codice:
        portatile = uscita / f"{NOME_CARTELLA}_windows.zip"
        crea_zip_portatile(file, versione, uscita / "portatile", portatile)
        shutil.rmtree(uscita / "portatile", ignore_errors=True)
        print(f"Creato {portatile.name}: {portatile.stat().st_size / 1024 / 1024:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
