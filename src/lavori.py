"""Lavori lunghi in background (montaggio video, download, trascrizioni) con stato
interrogabile dall'interfaccia: fase corrente, avanzamento 0-1, esito.

Stato solo in memoria: se il server si riavvia il lavoro in corso si perde (i
file già prodotti restano su disco). Un solo lavoro alla volta per chiave, così
due clic su "Monta" non avviano due rendering dello stesso reel.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass
class Lavoro:
    chiave: str
    stato: str = "in_corso"  # in_corso | completato | errore
    fase: str = "In avvio"
    avanzamento: float = 0.0
    messaggio: str = ""
    avviato_il: float = field(default_factory=time.time)
    concluso_il: float | None = None

    def come_dict(self) -> dict:
        return asdict(self)


class LavoroGiaInCorso(RuntimeError):
    pass


_lavori: dict[str, Lavoro] = {}
_lock = threading.Lock()


def avvia(chiave: str, funzione: Callable[[Callable[[str, float], None]], str | None]) -> Lavoro:
    """Avvia `funzione(aggiorna)` in un thread. `aggiorna(fase, frazione)` aggiorna lo
    stato; il valore restituito dalla funzione diventa il messaggio finale."""
    with _lock:
        esistente = _lavori.get(chiave)
        if esistente and esistente.stato == "in_corso":
            raise LavoroGiaInCorso("C'è già un'operazione in corso per questo elemento.")
        lavoro = Lavoro(chiave=chiave)
        _lavori[chiave] = lavoro

    def aggiorna(fase: str, frazione: float) -> None:
        lavoro.fase = fase
        lavoro.avanzamento = max(lavoro.avanzamento, min(max(frazione, 0.0), 1.0))

    def corpo() -> None:
        try:
            lavoro.messaggio = funzione(aggiorna) or ""
            lavoro.avanzamento = 1.0
            lavoro.stato = "completato"
        except Exception as e:  # noqa: BLE001 - qualunque errore va mostrato nell'interfaccia
            traceback.print_exc()
            lavoro.messaggio = str(e)
            lavoro.stato = "errore"
        finally:
            lavoro.concluso_il = time.time()

    threading.Thread(target=corpo, daemon=True, name=f"lavoro-{chiave}").start()
    return lavoro


def stato(chiave: str) -> Lavoro | None:
    return _lavori.get(chiave)
