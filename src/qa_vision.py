"""Step 7: valutazione qualità della slide composta con Gemini vision."""

from __future__ import annotations

import io

from google.genai import types
from PIL import Image

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .models import QASlide

_ISTRUZIONI = """\
Valuta questa slide di un carosello Instagram di divulgazione scientifica spaziale (SEDS UPO).

Deve rispettare questo design system:
- Sfondo blu navy scuro (#282d43) con illustrazione line-art tecnica in stile blueprint \
aerospaziale (bianco e rosso corallo #aa2922).
- Testo bianco, con parole chiave evidenziate in rosso corallo #aa2922, leggibile su mobile.
- Logo bianco (badge con razzo) in basso a destra, pulito e non sovrapposto ad altri elementi.
- Margini di sicurezza rispettati (nessun elemento tagliato ai bordi).

Assegna un punteggio intero da 0 a 10 per ciascuna di queste dimensioni:
- leggibilita_testo: il testo è facilmente leggibile su schermo mobile?
- contrasto: il contrasto tra testo e sfondo è sufficiente?
- bilanciamento_visivo: la composizione (testo, illustrazione, logo) è bilanciata?
- aderenza_design_system: colori, stile illustrazione e posizione logo rispettano le regole sopra?
- qualita_estetica_generale: impressione estetica complessiva.

Aggiungi un commento breve (1-2 frasi) che spieghi il punteggio più basso, se ce n'è uno \
sotto 7.
"""


def valuta_slide(config: Config, immagine_composta: Image.Image, numero_slide: int) -> QASlide:
    client = crea_client(config)

    buffer = io.BytesIO()
    immagine_composta.save(buffer, format="PNG")
    parte_immagine = types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png")

    prompt = f"{_ISTRUZIONI}\n\nQuesta è la slide numero {numero_slide}."

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=[prompt, parte_immagine],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=QASlide,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_vision)
    qa: QASlide = response.parsed
    qa.numero = numero_slide
    return qa
