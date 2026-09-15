"""Step 1: analisi del testo grezzo con Gemini -> struttura carosello (JSON strutturato)."""

from __future__ import annotations

from google.genai import types

from .config import Config
from .gemini_client import chiama_con_retry, crea_client
from .models import CarosalloAnalisi

_ISTRUZIONI_SISTEMA = """\
Sei un editor scientifico che trasforma testi di ricerca spaziale/aerospaziale in copy \
per un carosello Instagram di divulgazione (pagina SEDS UPO).

Regole tassative:
- Il numero di slide va deciso da te in base a quante idee autonome contiene il testo, \
nell'intervallo indicato; non forzare un numero fisso.
- Segui SEMPRE questo schema narrativo, nell'ordine, usando una o più slide per fase quando \
la complessità del contenuto lo richiede (fasi ammesse, in questo ordine): \
hook, contesto, scoperta_chiave, meccanismo, implicazioni, chiusura.
- Tono divulgativo ma rigoroso: mai clickbait, mai affermazioni non supportate dal testo fornito.
- Il testo di ogni slide (concatenazione dei segmenti) non deve superare {max_caratteri} caratteri.
- Scomponi il copy di ogni slide in una sequenza ordinata di segmenti di testo; imposta \
`evidenziato=true` SOLO per parole o brevi espressioni chiave (mai frasi intere), con parsimonia \
(1-3 evidenziazioni per slide al massimo).
- Per `soggetto_immagine` scrivi, in inglese, UN soggetto con un'azione o un contesto chiaro \
(non un sostantivo nudo e generico, non un concetto astratto senza forma visiva): un oggetto \
concreto che sta facendo o subendo qualcosa di specifico al contenuto di quella slide. Deve \
restare UNA sola composizione semplice, mai una scena con più oggetti diversi o un diagramma \
con più parti etichettate. Mai testo, lettere, numeri o etichette da includere nell'immagine \
(verranno comunque esclusi a valle, ma non vanno nemmeno suggeriti nella descrizione). Esempio: \
per un testo sulla sanificazione di una navicella, il soggetto giusto è "a spacecraft being \
sanitized/decontaminated" — NON "a spacecraft" da solo (troppo generico, perde il concetto \
della slide) e NON una scena complessa con più strumenti, pannelli e didascalie.
- Nella slide di chiusura includi nel copy un riferimento alla fonte della ricerca (se nota dal \
testo) e un invito a seguire/salvare/commentare la pagina.
- La slide hook è una copertina, non un paragrafo: il suo copy deve essere un titolo breve e \
d'impatto, al massimo 90 caratteri (idealmente 6-12 parole), senza spiegare già il "come" o il \
"perché" (quello arriva nelle slide successive). Al massimo 1 evidenziazione in rosso su questa \
slide.
"""


def analizza_testo(config: Config, testo_grezzo: str) -> CarosalloAnalisi:
    client = crea_client(config)

    istruzioni = _ISTRUZIONI_SISTEMA.format(max_caratteri=config.max_caratteri_slide)
    prompt = (
        f"{istruzioni}\n\n"
        f"Numero di slide ammesso: tra {config.min_slide} e {config.max_slide}.\n\n"
        f"Testo grezzo della ricerca:\n\"\"\"\n{testo_grezzo.strip()}\n\"\"\"\n"
    )

    def _chiamata(modello: str):
        return client.models.generate_content(
            model=modello,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CarosalloAnalisi,
            ),
        )

    response = chiama_con_retry(config, _chiamata, config.modelli_testo)
    analisi: CarosalloAnalisi = response.parsed

    _valida_analisi(config, analisi)
    return analisi


def _valida_analisi(config: Config, analisi: CarosalloAnalisi) -> None:
    errori = []

    if not (config.min_slide <= len(analisi.slides) <= config.max_slide):
        errori.append(
            f"numero di slide ({len(analisi.slides)}) fuori dal range "
            f"[{config.min_slide}, {config.max_slide}]"
        )

    for slide in analisi.slides:
        testo_piatto = "".join(s.testo for s in slide.segmenti)
        if len(testo_piatto) > config.max_caratteri_slide:
            errori.append(
                f"slide {slide.numero}: copy di {len(testo_piatto)} caratteri "
                f"(max {config.max_caratteri_slide})"
            )
        if not slide.segmenti:
            errori.append(f"slide {slide.numero}: nessun segmento di testo")

    numeri = [s.numero for s in analisi.slides]
    if numeri != list(range(1, len(numeri) + 1)):
        errori.append(f"numerazione slide non sequenziale: {numeri}")

    if errori:
        raise ValueError(
            "Risposta Gemini non conforme ai vincoli richiesti:\n- " + "\n- ".join(errori)
        )
