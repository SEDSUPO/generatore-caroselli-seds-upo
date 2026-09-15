"""Pubblica una nuova versione dell'app su GitHub (doppio clic su pubblica.bat).

1. controlla che nel repository non finiscano la chiave (.env) o dati personali;
2. chiede in una riga cosa cambia (diventa la nota mostrata ai collaboratori);
3. salva le modifiche con git, crea l'etichetta di versione (vAAAA.MM.GG-HHMM) e
   invia tutto a GitHub.

Poi GitHub Actions (.github/workflows/pubblica.yml) prepara i pacchetti e la pagina
"Releases": dopo qualche minuto i collaboratori trovano l'aggiornamento nell'app.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aggiorna_app  # noqa: E402

DATI_PERSONALI = {"reel", "prompts", "input_immagini", "output", "notizie", "input_immagini_notizie",
                  "output_notizie", "modelli", ".stato", ".aggiornamenti", "python", ".venv"}


def git(*argomenti: str, controlla: bool = True) -> str:
    esito = subprocess.run(["git", *argomenti], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if controlla and esito.returncode != 0:
        raise SystemExit(f"Errore di git ({' '.join(argomenti)}):\n{esito.stderr.strip()}")
    return esito.stdout.strip()


def _repository_remoto() -> str:
    indirizzo = git("remote", "get-url", "origin", controlla=False)
    trovato = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", indirizzo)
    if not trovato:
        raise SystemExit("Il repository non è collegato a GitHub (manca il remote 'origin').")
    return trovato.group(1)


def _controlli_di_sicurezza() -> list[str]:
    file = [f for f in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines() if f]
    vietati = [f for f in file if Path(f).name == ".env" or f.split("/")[0] in DATI_PERSONALI]
    if vietati:
        raise SystemExit(f"STOP: stanno per finire su GitHub file personali: {vietati[:10]}\nControlla .gitignore.")

    percorso_env = ROOT / ".env"
    if percorso_env.is_file():
        from dotenv import dotenv_values

        chiave = (dotenv_values(percorso_env).get("GOOGLE_API_KEY") or "").strip().encode()
        if chiave:
            for relativo in file:
                percorso = ROOT / relativo
                if percorso.is_file() and percorso.stat().st_size < 20 * 1024 * 1024 and chiave in percorso.read_bytes():
                    raise SystemExit(f"STOP: la tua API key compare nel file {relativo}. Toglila e riprova.")
    return file


def main() -> int:
    print("Pubblicazione di una nuova versione su GitHub\n")
    repository = _repository_remoto()
    if repository.lower() != aggiorna_app.REPOSITORY_GITHUB.lower():
        raise SystemExit(
            f"STOP: il repository remoto è {repository}, ma aggiorna_app.py cerca gli aggiornamenti in "
            f"{aggiorna_app.REPOSITORY_GITHUB}. Allinea REPOSITORY_GITHUB prima di pubblicare."
        )
    _controlli_di_sicurezza()

    modifiche = git("status", "--short")
    print("Modifiche da pubblicare:" if modifiche else "Nessuna modifica al codice rispetto all'ultima pubblicazione.")
    if modifiche:
        print(modifiche)
    print()
    nota = input("Cosa cambia in questa versione? (una riga, che vedranno i collaboratori): ").strip()
    versione = f"{datetime.now():%Y.%m.%d-%H%M}"
    messaggio = nota or f"Versione {versione}"

    if modifiche:
        git("add", "-A")
        git("commit", "-m", messaggio)
    git("tag", "-a", f"v{versione}", "-m", messaggio)

    print("\nInvio a GitHub...")
    ramo = git("rev-parse", "--abbrev-ref", "HEAD")
    # Prima il codice, poi l'etichetta, in due invii separati: inviati insieme, GitHub a
    # volte non avvia il workflow della versione (osservato sul primo invio).
    esito = subprocess.run(["git", "push", "origin", ramo], cwd=ROOT)
    if esito.returncode == 0:
        esito = subprocess.run(["git", "push", "origin", f"v{versione}"], cwd=ROOT)
    if esito.returncode != 0:
        git("tag", "-d", f"v{versione}", controlla=False)
        raise SystemExit("Invio non riuscito (connessione o login a GitHub). Le modifiche restano salvate: riprova.")

    print(f"\nFatto: versione {versione} inviata.")
    print("GitHub ora prepara i pacchetti (circa 10 minuti). Avanzamento:")
    print(f"  https://github.com/{repository}/actions")
    print("Quando ha finito, la versione compare qui e i collaboratori la vedono nell'app:")
    print(f"  https://github.com/{repository}/releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
