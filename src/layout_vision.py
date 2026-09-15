"""Step 5a: individua le aree "pulite" dello sfondo (senza illustrazione) dove
posizionare il testo, così che la composizione non copra mai la grafica generata.

Se questa analisi fallisce (errore di rete, quota, risposta non valida), il
chiamante deve ricadere su una posizione di default: non è un passaggio
critico quanto l'analisi testo o la QA finale, quindi qui l'errore viene
catturato e loggato invece di interrompere la composizione.
"""

from __future__ import annotations

import io
import sys

from google.genai import types
from PIL import Image

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .models import AnalisiZoneTesto, ZonaTesto

_ISTRUZIONI = """\
Questa è un'illustrazione line-art di sfondo (stile blueprint tecnico aerospaziale, senza \
testo) per una slide di un carosello Instagram, dimensioni 1080x1350 px.

Individua fino a 2 rettangoli che indicano le aree più "pulite": zone di solo sfondo blu navy \
uniforme (o cielo stellato rado), SENZA linee dell'illustrazione, senza forme, senza dettagli \
fitti, dove del testo bianco/rosso sovrapposto resterebbe perfettamente leggibile senza toccare \
alcun elemento del disegno.

Regole:
- Coordinate come frazioni 0-1 dell'immagine (x,y = angolo in alto a sinistra del rettangolo).
- Ogni rettangolo deve avere larghezza almeno 0.75 e altezza almeno 0.12.
- Evita SEMPRE l'angolo in basso a destra (ultimo 15% di larghezza e altezza): è riservato al logo.
- Restituisci i rettangoli in ordine dal più adatto al meno adatto. Se esiste una sola zona ampia \
e pulita, restituisci solo quella (un elemento nella lista).
- Se lo sfondo è quasi interamente occupato dall'illustrazione, restituisci comunque il \
rettangolo meno peggio (il più vuoto disponibile), anche se piccolo.
"""


def rileva_zone_testo(config: Config, sfondo: Image.Image) -> list[ZonaTesto]:
    """Ritorna 1-2 zone (in ordine di preferenza), oppure una lista vuota se
    l'analisi non è riuscita: in quel caso il chiamante userà un fallback.
    """
    try:
        client = crea_client(config)

        buffer = io.BytesIO()
        sfondo.save(buffer, format="PNG")
        parte_immagine = types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png")

        def _chiamata(modello: str):
            return client.models.generate_content(
                model=modello,
                contents=[_ISTRUZIONI, parte_immagine],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AnalisiZoneTesto,
                ),
            )

        response = chiama_con_retry(config, _chiamata, config.modelli_vision)
        analisi: AnalisiZoneTesto = response.parsed
        return analisi.zone
    except Exception as e:  # noqa: BLE001 - fallback intenzionale, non deve bloccare la composizione
        print(
            f"  [layout] rilevamento zone testo non riuscito ({e}); uso la posizione di default.",
            file=sys.stderr,
        )
        return []
