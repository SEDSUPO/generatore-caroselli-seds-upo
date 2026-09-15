"""Stima la durata del parlato di un testo in italiano: non chiesta a Gemini
(che non ha un cronometro), calcolata dal conteggio parole — un metodo
grezzo ma verificabile, a differenza di una stima "a sensazione" del modello.
"""

from __future__ import annotations

# Velocità media di un parlato chiaro e divulgativo in italiano. Un lettore
# professionista è più veloce, un contenuto tecnico è più lento: 150 è una
# stima prudente, meglio sottostimare la durata (spinge verso copioni un
# po' più lunghi) che sovrastimarla.
PAROLE_AL_MINUTO = 150


def stima_secondi(testo: str) -> float:
    parole = len(testo.split())
    return parole / PAROLE_AL_MINUTO * 60


def formatta_durata(secondi: float) -> str:
    minuti = int(secondi // 60)
    resto = int(secondi % 60)
    if minuti == 0:
        return f"{resto}s"
    return f"{minuti}m {resto:02d}s"
