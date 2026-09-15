"""Schemi dati condivisi (Pydantic) per output strutturato Gemini e per i file JSON su disco."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FaseNarrativa = Literal[
    "hook",
    "contesto",
    "scoperta_chiave",
    "meccanismo",
    "implicazioni",
    "chiusura",
]


class Segmento(BaseModel):
    """Un frammento di testo della slide con eventuale evidenziazione rossa."""

    testo: str
    evidenziato: bool


class SlideAnalisi(BaseModel):
    """Ciò che Gemini (testo) genera per ogni slide: fase, copy a segmenti, soggetto immagine.

    Il prompt Nano Banana completo NON viene chiesto a Gemini: viene assemblato in
    Python concatenando il Blocco di Stile Fisso (design_system.md, Sezione 3) con
    questo `soggetto_immagine`, per garantire che il blocco fisso resti sempre
    identico e non venga mai riparafrasato dal modello.
    """

    numero: int
    fase_narrativa: FaseNarrativa
    segmenti: list[Segmento]
    soggetto_immagine: str = Field(
        description=(
            "Un oggetto concreto con un'azione/contesto chiaro (es. 'a spacecraft being "
            "sanitized', non 'a spacecraft' da solo), in inglese, adatto a sostituire "
            "[SOGGETTO DELLA SLIDE] nel template Nano Banana. Non una scena o un diagramma "
            "con più elementi. Niente testo, lettere, numeri o etichette."
        )
    )


class CarosalloAnalisi(BaseModel):
    """Output strutturato completo della fase 1 (analisi testo con Gemini)."""

    argomento: str
    slides: list[SlideAnalisi]


class CreditoImmagine(BaseModel):
    """Da dove viene un'immagine pubblica usata come sfondo (per i crediti in caption)."""

    fonte: Literal["nasa", "wikimedia"]
    titolo: str
    autore: str | None = None
    licenza: str | None = None
    url: str | None = None


class SlideCompleta(BaseModel):
    """Slide arricchita con il prompt immagine assemblato: quello che finisce su disco."""

    numero: int
    fase_narrativa: FaseNarrativa
    segmenti: list[Segmento]
    soggetto_immagine: str
    prompt_immagine: str
    # "illustrazione": sfondo generato con spazio vuoto per il testo (zone rilevate con
    # Gemini vision). "foto": immagine qualunque (es. NASA), il testo va in una fascia
    # navy in alto e la foto sotto.
    tipo_sfondo: Literal["illustrazione", "foto"] = "illustrazione"
    query_immagine: str = ""  # parola chiave in inglese per cercare un'immagine pubblica
    credito_immagine: CreditoImmagine | None = None

    def testo_piatto(self) -> str:
        return "".join(s.testo for s in self.segmenti)


class CaptionGenerata(BaseModel):
    """Output strutturato della generazione caption: testo esteso + hashtag.

    La firma dell'autore e la formattazione finale del file non sono chieste a
    Gemini: vengono assemblate in Python (src/caption.py), così l'utente può
    cambiare il nome dell'autore senza rigenerare il testo.
    """

    testo: str = Field(description="Corpo della caption, discorsivo, senza hashtag e senza firma")
    tag: list[str] = Field(
        description="Circa 5 hashtag pertinenti, parole singole minuscole, senza il simbolo #"
    )


class CarosalloCompleto(BaseModel):
    nome_carosello: str
    argomento: str
    numero_slide: int
    slides: list[SlideCompleta]


class QueryImmaginiSlide(BaseModel):
    query: list[str] = Field(description="Parola chiave in inglese (1-3 parole) per ogni slide, nello stesso ordine")


class ZonaTesto(BaseModel):
    """Un rettangolo (frazioni 0-1 delle dimensioni immagine) di sfondo "pulito",
    individuato da Gemini vision, adatto a ospitare testo sovrapposto senza
    coprire l'illustrazione.
    """

    x: float = Field(ge=0, le=1, description="Angolo sinistro, frazione della larghezza")
    y: float = Field(ge=0, le=1, description="Angolo superiore, frazione dell'altezza")
    larghezza: float = Field(ge=0, le=1)
    altezza: float = Field(ge=0, le=1)


class AnalisiZoneTesto(BaseModel):
    """Output strutturato dell'analisi di layout: fino a 2 zone libere, in ordine
    di preferenza (dalla più adatta alla meno adatta)."""

    zone: list[ZonaTesto]


class QASlide(BaseModel):
    """Punteggi di qualità 0-10 assegnati da Gemini vision a una slide composta."""

    numero: int
    leggibilita_testo: int = Field(ge=0, le=10)
    contrasto: int = Field(ge=0, le=10)
    bilanciamento_visivo: int = Field(ge=0, le=10)
    aderenza_design_system: int = Field(ge=0, le=10)
    qualita_estetica_generale: int = Field(ge=0, le=10)
    commento: str

    def punteggio_medio(self) -> float:
        valori = [
            self.leggibilita_testo,
            self.contrasto,
            self.bilanciamento_visivo,
            self.aderenza_design_system,
            self.qualita_estetica_generale,
        ]
        return sum(valori) / len(valori)


class VoceFeed(BaseModel):
    """Una notizia letta da un feed RSS, prima di diventare una slide."""

    titolo: str
    # Titolo in italiano per l'elenco di scelta (src/feed_traduzione.py); None se la
    # traduzione è disattivata o non è riuscita: si mostra l'originale.
    titolo_tradotto: str | None = None
    # Deciso insieme alla traduzione: i siti generalisti (quotidiani, portali)
    # pubblicano anche notizie che con lo spazio non c'entrano.
    pertinente: bool = True
    url: str
    fonte: str
    feed: str
    riassunto_grezzo: str = ""
    url_immagine: str | None = None
    data: str | None = None  # ISO 8601, None se il feed non la espone


class TitoloNotiziaGenerato(BaseModel):
    """Output strutturato dell'analisi di una singola notizia (Gemini legge
    l'URL con lo strumento url_context, non testo grezzo incollato a mano)."""

    segmenti: list[Segmento] = Field(
        description="Titolo breve e d'impatto della notizia, a segmenti per l'evidenziazione rossa"
    )
    riassunto: str = Field(
        description="Riassunto di 1-2 frasi della notizia, usato per comporre la caption finale "
        "del carosello — non mostrato direttamente sulla slide"
    )


class SlideNotizia(BaseModel):
    """Una slide del carosello notizie: una notizia, una foto reale, un titolo breve."""

    numero: int
    url: str
    fonte: str
    segmenti: list[Segmento]
    riassunto: str

    def titolo_piatto(self) -> str:
        return "".join(s.testo for s in self.segmenti)


class CarosalloNotizie(BaseModel):
    nome_carosello: str
    slides: list[SlideNotizia]


TipoTopic = Literal["notizia_recente", "approfondimento"]


class TopicSuggerito(BaseModel):
    """Un'idea di argomento per un reel, trovata da Gemini con ricerca web."""

    titolo: str
    tipo: TipoTopic
    perche_interessante: str = Field(description="Una frase su perché è un buon argomento per un reel ora")


class TopicSuggeriti(BaseModel):
    topic: list[TopicSuggerito]


class ScenaReel(BaseModel):
    """Una scena del corpo del reel (esclusi hook e chiusura)."""

    numero: int
    testo_parlato: str = Field(description="Cosa dire/scrivere in sovraimpressione in questa scena")
    visivo_suggerito: str = Field(
        description="Descrizione concreta in italiano di cosa riprendere o mostrare in questa "
        "scena (non un prompt di generazione immagine: è per procurarsi il materiale)"
    )


class CopioneGenerato(BaseModel):
    """Output strutturato della generazione del copione reel."""

    hook_varianti: list[str] = Field(
        description="2-3 varianti alternative per il gancio iniziale (i primi 2-3 secondi, "
        "la parte che decide se chi guarda continua o scorre via)"
    )
    scene: list[ScenaReel]
    chiusura_cta: str = Field(description="Frase di chiusura con invito ad azione (segui/salva/commenta)")


class Reel(BaseModel):
    """Un reel salvato su disco: copione completo + quale hook è stato scelto."""

    nome_reel: str
    argomento: str
    hook_varianti: list[str]
    hook_scelto: str
    scene: list[ScenaReel]
    chiusura_cta: str

    def testo_completo(self) -> str:
        """Tutto il parlato del reel (hook + scene + CTA), per stimare la durata."""
        parti = [self.hook_scelto] + [s.testo_parlato for s in self.scene] + [self.chiusura_cta]
        return " ".join(parti)


# ---------------------------------------------------------------------------
# Produzione del reel (registrazione, visivi, montaggio): stato separato dal
# copione, salvato in reel/<nome>/produzione.json.
# ---------------------------------------------------------------------------

TipoAsset = Literal["immagine", "video"]
FonteAsset = Literal["caricato", "nasa", "wikimedia", "youtube"]


class Take(BaseModel):
    """Una registrazione audio di un segmento, già ripulita (rumore, silenzi)."""

    id: str
    file: str  # relativo a reel/<nome>/audio/
    durata: float
    creato_il: str
    voce_ai: str | None = None  # es. "Kokoro · Sara"; None = registrata dall'utente


class Asset(BaseModel):
    """Un'immagine o un video della libreria del reel, con i dati per i crediti."""

    id: str
    tipo: TipoAsset
    fonte: FonteAsset
    file: str  # relativo a reel/<nome>/media/
    anteprima: str  # relativo a reel/<nome>/media/ (jpg)
    titolo: str
    autore: str | None = None
    licenza: str | None = None
    url_origine: str | None = None
    durata: float | None = None  # solo video
    larghezza: int
    altezza: int


class ParolaTrascritta(BaseModel):
    """Una parola riconosciuta da Whisper, con tempi relativi all'inizio del segmento."""

    testo: str
    inizio: float
    fine: float


class StatoSegmento(BaseModel):
    """Stato di produzione di un segmento parlato (hook, una scena, la chiusura).

    Il visivo di un segmento è `asset_id`, a meno che `continua_precedente` sia vero:
    in quel caso il segmento prosegue lo stesso visivo del precedente (un video
    continua a scorrere senza tagli). `inizio_video` conta solo sul primo segmento
    di un blocco.
    """

    chiave: str
    takes: list[Take] = Field(default_factory=list)
    take_scelto: str | None = None
    asset_id: str | None = None
    continua_precedente: bool = False
    inizio_video: float = 0.0
    # Visivi orizzontali: False = interi con i lati sfocati; True = ingranditi a tutto
    # schermo, tagliando i lati. `inquadratura_x` sceglie la porzione tenuta
    # (0 = bordo sinistro, 0.5 = centro, 1 = bordo destro). Come `inizio_video`,
    # contano solo sul primo segmento di un blocco.
    riempi_schermo: bool = False
    inquadratura_x: float = 0.5
    query_ricerca: str = ""
    parole: list[ParolaTrascritta] | None = None  # None = non ancora trascritto
    parole_take: str | None = None  # take a cui si riferisce la trascrizione

    def take_attivo(self) -> Take | None:
        return next((t for t in self.takes if t.id == self.take_scelto), None)


class ProduzioneReel(BaseModel):
    segmenti: dict[str, StatoSegmento] = Field(default_factory=dict)
    asset: list[Asset] = Field(default_factory=list)
    musica_file: str | None = None  # relativo a reel/<nome>/media/
    musica_volume: float = 0.18
    # Crediti del brano se scelto dall'archivio di musica libera (Openverse); vuoti se caricato.
    musica_titolo: str | None = None
    musica_autore: str | None = None
    musica_licenza: str | None = None
    musica_url: str | None = None
    musica_attribuzione: str | None = None
    musica_query: str = ""
    sottotitoli_attivi: bool = True
    titolo_hook_attivo: bool = True
    voce_motore: str = "kokoro"  # kokoro | gemini
    voce_nome: str = "if_sara"
    voce_velocita: float = 1.0
    voce_istruzioni: str = "Leggi con tono divulgativo, coinvolgente e sicuro, ritmo sostenuto"  # solo Gemini
    soggetto_copertina: str | None = None  # in inglese, proposto da Gemini
    titolo_copertina: str = ""  # con **parola** per l'evidenziazione rossa

    def asset_per_id(self, asset_id: str | None) -> Asset | None:
        return next((a for a in self.asset if a.id == asset_id), None)


class AnalisiVisivaReel(BaseModel):
    """Output strutturato (una sola chiamata): query di ricerca per ogni segmento +
    soggetto della copertina."""

    query: list[str] = Field(
        description="La parola chiave in inglese (1-3 parole) per ogni segmento, nello stesso ordine"
    )
    musica_query: str = Field(description="1-3 parole in inglese: genere o atmosfera della musica di sottofondo")
    soggetto_copertina: str = Field(
        description="Un singolo soggetto semplice e diretto, in inglese, per l'illustrazione di copertina"
    )
