"""Helper condivisi dalle pagine del reel (copione e produzione)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import Reel
from src.reel_produzione import calcola_blocchi, carica_produzione, cartella_output, cartella_reel


def stato_passi(reel: Reel) -> dict[str, bool]:
    """Quali passi del flusso sono completi, per le spunte nella barra dei passi."""
    produzione = carica_produzione(reel)
    blocchi = calcola_blocchi(reel, produzione)
    uscita = cartella_output(reel.nome_reel)
    return {
        "copione": True,
        "registrazione": all(s.take_attivo() for s in produzione.segmenti.values()),
        "visivi": all(b.asset for b in blocchi),
        "montaggio": (uscita / "reel.mp4").is_file(),
        "pubblica": (uscita / "copertina.jpg").is_file() and (cartella_reel(reel.nome_reel) / "caption.txt").is_file(),
    }
