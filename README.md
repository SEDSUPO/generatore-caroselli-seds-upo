# Generatore caroselli Instagram — SEDS UPO

App locale (si usa dal browser) che prepara i contenuti Instagram di divulgazione
spaziale di SEDS UPO: caroselli scientifici, caroselli di notizie e reel (copione,
voce, visivi, montaggio video, copertina e caption). La pubblicazione su Instagram
resta manuale.

Unica credenziale richiesta: una API key gratuita di Google Gemini, che ognuno
inserisce dall'app e resta sul proprio computer (file `.env`, mai nel repository).

## Per i collaboratori: installare e aggiornare

1. Dalla pagina [**Releases**](https://github.com/SEDSUPO/generatore-caroselli-seds-upo/releases/latest) scarica
   `Generatore_caroselli_SEDS_UPO_windows.zip`: è il pacchetto **pronto all'uso**, con
   Python e tutte le librerie già dentro. Non va installato niente.
2. Estrailo (clic destro → *Estrai tutto*) in una cartella dal percorso corto, per
   esempio Documenti, e fai doppio clic su `avvia_app.bat`.
3. L'app si apre nel browser su **Impostazioni**: incolla la tua API key gratuita
   (da <https://aistudio.google.com/apikey>).
4. **Aggiornamenti**: in Impostazioni l'app controlla da sola se c'è una versione nuova;
   premi **Aggiorna ora** e si chiude, si aggiorna e si riapre. Chiave, lavori e
   impostazioni restano come sono.

Istruzioni passo passo e soluzione dei problemi più comuni: [`LEGGIMI.txt`](LEGGIMI.txt).

In alternativa al pacchetto pronto all'uso: `Generatore_caroselli_SEDS_UPO_codice.zip` (o
un clone del repository) + Python 3.11 o più recente + doppio clic su `installa.bat`, che
crea un ambiente `.venv` e installa `requirements.txt`.

## Per chi sviluppa: pubblicare una nuova versione

Il repository contiene solo codice, grafica, `config.yaml` (impostazioni di partenza) e
`feeds.yaml` (le testate). `.gitignore` tiene fuori `.env`, tutti i lavori (`reel/`,
`prompts/`, `output*/`, `notizie/`, `input_immagini*/`), i modelli scaricati e
`design_system.md`, che resta solo sul PC di sviluppo.

**Pubblicare**: doppio clic su `pubblica.bat` (`strumenti/pubblica_versione.py`):
1. controlla che non stiano per finire su GitHub `.env`, dati personali o la API key
   (cercata dentro ogni file del repository);
2. chiede una riga su cosa cambia: diventa la nota che i collaboratori leggono nell'app;
3. fa commit, crea l'etichetta `vAAAA.MM.GG-HHMM` e la invia a GitHub.

GitHub Actions ([`.github/workflows/pubblica.yml`](.github/workflows/pubblica.yml)) esegue
[`strumenti/crea_pacchetti.py`](strumenti/crea_pacchetti.py) su Windows e pubblica la
versione in *Releases* con due file (circa 10 minuti):
- `..._windows.zip`, **pronto all'uso**: Python *embeddable* di python.org (stessa
  versione usata per installare le librerie, così quelle compilate sono compatibili), il
  file `python3XX._pth` che aggiunge `Lib\site-packages` e la cartella dell'app al
  percorso di import, tutte le librerie installate con `pip install --target` (pip
  compreso, per aggiornare yt-dlp dall'app) e le DLL del runtime Visual C++, così non
  serve il Redistributable. Prima di comprimere verifica che l'app si importi con quel
  Python;
- `..._codice.zip`: solo il codice + `versione.txt`, usato dall'aggiornamento.

**Aggiornamento dall'app** (`aggiorna_app.py`, solo libreria standard, così funziona anche
se le librerie dell'app sono rotte): Impostazioni legge l'ultima release dall'API di
GitHub (senza login, risposta tenuta 10 minuti). *Aggiorna ora* avvia `aggiorna_app.py
--automatico --riavvia` in una finestra sua e chiude l'app (`os._exit(0)`: con codice 0
termina anche il processo che sorveglia i riavvii di Flask). L'aggiornamento aspetta che
non ci siano più processi Python dell'app, salva il codice attuale in `.aggiornamenti/`
(ultime 3 copie), scarica e applica il nuovo codice senza toccare `.env`, lavori,
modelli, `python\`/`.venv` e le impostazioni personali (`config.yaml`, `feeds.yaml`),
toglie i file obsoleti, reinstalla le librerie solo se `requirements.txt` è cambiato e
riapre l'app (`NON_APRIRE_BROWSER=1`: la pagina già aperta si ricarica da sola quando la
versione cambia). `aggiorna.bat` fa lo stesso a mano; `aggiorna.bat --ripristina` torna
alla versione precedente. La copia di sviluppo (senza `versione.txt`) non si aggiorna
da GitHub: si aggiorna con git.

I pacchetti si possono creare anche in locale per provarli:
`python strumenti/crea_pacchetti.py --versione prova --uscita dist`.

## Setup per lo sviluppo

```bash
pip install -r requirements.txt
python run_app.py
```

Font e logo sono in `assets/`. `GENERATORE_PORTA=5055 python run_app.py` avvia su un'altra
porta (utile per provare una seconda copia insieme a quella già aperta).

## App web (interfaccia grafica)

**Avvio (Windows)**: doppio click su [`avvia_app.bat`](avvia_app.bat).

**Avvio (da terminale, qualsiasi piattaforma)**:

```bash
python run_app.py
```

Si apre automaticamente il browser su `http://127.0.0.1:5000`. Al primo avvio
la app reindirizza alla pagina **Impostazioni**: incolla lì la tua
`GOOGLE_API_KEY` — viene salvata nel file `.env` nella root del progetto
(creato automaticamente se non esiste), che resta l'unico posto dove risiede
la chiave.

Sempre in Impostazioni, sezione **Modelli Gemini**: aggiungi/rimuovi/riordina i
modelli usati per testo (analisi, caption, notizie) e vision (layout, QA) — in
cascata, se il primo è sovraccarico (errore 503 "high demand", frequente sui
modelli gratuiti nelle ore di punta) o irraggiungibile, l'app prova subito il
successivo nella lista invece di aspettare in retry sullo stesso. Un nuovo
modello viene verificato (esiste ed è accessibile con la tua chiave) prima di
essere aggiunto. Le modifiche scrivono direttamente in `config.yaml`.

Flusso nell'interfaccia:

1. **Home** → incolla il testo grezzo della ricerca e un nome per il
   carosello → "Analizza e genera prompt" (chiama Gemini, crea le slide).
2. **Pagina del carosello** → per ogni slide: copia il prompt Nano Banana con
   un click, genera l'immagine esternamente, caricala trascinandola/
   selezionandola nella slide corrispondente. In alternativa **"Cerca immagine
   pubblica"** cerca una foto su NASA Image Library o Wikimedia Commons, con la parola
   chiave già compilata da Gemini (una sola chiamata per tutte le slide, all'apertura
   della pagina). Le foto usano il **layout foto**: sfondo navy con quadrettatura
   blueprint, testo in alto e la foto sotto come una stampa appoggiata sul foglio (bordo
   bianco, ombra morbida, segni d'angolo rossi), che finisce sopra il logo senza
   coprirlo. Autore e licenza finiscono nei crediti della caption.
   Il layout foto si può attivare anche per un'immagine caricata a mano.

   Con **"Modifica testo"** puoi
   correggere il copy direttamente (usa `**parola**` per evidenziarla: testo bianco
   su un rettangolino rosso, perché il rosso direttamente sul navy si leggeva male),
   entro il limite caratteri configurato — se la slide era già
   composta, l'output viene invalidato e va ricomposta.
3. Quando tutte le immagini sono caricate, **"Componi tutte le slide"**
   compone con Pillow, valuta ogni slide con Gemini vision e mostra i
   punteggi QA direttamente sotto ogni slide (badge rosso se sotto soglia,
   con il commento di Gemini).
4. Per una slide sotto soglia: carica un'altra immagine di sfondo e usa
   "Componi questa slide" per rigenerare solo quella.
5. Nel pannello **"Caption per Instagram"**, inserisci il nome autore (facoltativo)
   e genera un testo esteso per la descrizione del post + 5 hashtag, salvato in
   `output/<nome_carosello>/caption.txt` (e incluso nello ZIP).
6. **"Scarica ZIP output"** scarica tutte le slide finali + la caption, pronte
   per l'upload manuale su Instagram.
7. **"Elimina carosello"** (sulla home o nella pagina del carosello) cancella
   definitivamente prompt, immagini caricate e output — azione irreversibile,
   richiede conferma.

## Carosello notizie

Un secondo tipo di carosello, più semplice: niente illustrazioni generate, foto
reali reperite da internet. Ogni slide è una notizia indipendente (non una fase
di un arco narrativo unico), quindi il carosello si costruisce incrementalmente:

1. Dalla home, crea un **carosello notizie** (solo un titolo).
2. Nella sua pagina, **"Carica ultime notizie"** legge i feed RSS delle testate
   configurate (stesso sistema di `anime-bites-auto`) e mostra le notizie più recenti,
   con i titoli tradotti in italiano. Selezioni quelle che ti interessano e le aggiungi
   in blocco, oppure incolli l'**URL di una notizia** qualsiasi. Per ogni notizia
   Gemini legge davvero la pagina (strumento `url_context`) e scrive un titolo breve;
   se la pagina non si lascia leggere, sintetizza dal titolo e dall'anteprima del feed.
   La foto arriva dal tag `og:image` della pagina o da quella indicata dal feed, sempre
   sostituibile.
3. Ogni slide si compone con un layout fisso — foto a tutto campo, pannello
   nero inferiore, titolo centrato, fonte e freccia di swipe — senza QA
   vision: essendo un template deterministico non serve un giudizio estetico.
4. Genera la caption (una sola per l'intero carosello, riassume tutte le
   notizie incluse) e scarica lo ZIP finale.

Eliminare una singola notizia o modificarne il titolo invalida solo l'output
di quella slide (va ricomposta), senza toccare le altre.

### Feed delle testate

`feeds.yaml` contiene i feed (una novantina, ricavati dalla lista di siti spaziali
italiani e internazionali). Si gestiscono da **Impostazioni → Testate**: si incolla
l'indirizzo di un sito o di una sua sezione, anche una lista intera, e l'app trova il
feed da sola (prima i `<link rel="alternate">` della pagina, poi i percorsi consueti
come `/feed` o `/rss.xml`). Per liste lunghe c'è anche lo script:

```bash
python importa_feed.py lista.txt                 # aggiunge ai feed presenti
python importa_feed.py lista.txt --sostituisci   # riparte da zero
```

Molti siti della lista sono quotidiani o portali generalisti: spesso espongono solo il
feed dell'intero sito, che mescola lo spazio con politica e sport e, pubblicando più
spesso, riempirebbe l'elenco. Per questo le notizie passano da due filtri:
1. **locale** (`src/feed_filtro.py`): se in un feed meno del 40% delle notizie contiene
   parole legate allo spazio (in più lingue), di quel feed si tengono solo quelle che le
   contengono;
2. **Gemini**, insieme alla traduzione dei titoli (3 richieste in parallelo da 50
   titoli): marca ogni notizia come spaziale o no. Le non spaziali sono nascoste, con
   una casella per mostrarle.

L'elenco resta in memoria 15 minuti, condiviso con i suggerimenti di argomento del reel.
Parametri in `config.yaml`: `max_notizie_per_feed`, `max_notizie_totali`,
`traduci_titoli_feed`.

## Creatore Reel

Il terzo tipo di contenuto: un reel Instagram completo, dal copione al video
montato. Il lavoro è diviso in cinque passi (barra in cima a ogni pagina, con la
spunta su quelli completi).

1. **Copione**. Dalla home scrivi un argomento a mano oppure clicca "Suggerisci
   argomenti": Gemini sceglie tra le ultime notizie dei feed delle testate (senza
   consumare la quota della ricerca web, usata solo come ripiego se i feed non
   rispondono) e aggiunge qualche approfondimento evergreen. Il copione ha 2-3
   varianti di **hook**, le **scene** (cosa dire e cosa mostrare) e una
   **chiusura**. La durata è stimata dalle parole (≈150 al minuto). Si possono
   **aggiungere scene** in qualsiasi punto ("+ Aggiungi una scena qui") ed
   eliminarle: le scene si rinumerano e registrazioni, trascrizioni e visivi restano
   agganciati alla scena giusta (quelle di una scena eliminata vengono cancellate).
   Rigenerare il copione scarta le registrazioni, ma tiene la libreria di immagini e video.
2. **Registrazione**. Registri la voce dal browser, una parte alla volta, con un
   **gobbo** a tutto schermo: conto alla rovescia, misuratore di livello,
   Spazio per avviare/fermare, frecce per cambiare parte. Ogni ripresa viene
   ripulita con ffmpeg (taglio dei filtri bassi, riduzione del rumore, taglio dei
   silenzi iniziali e finali). Si possono tenere più riprese e scegliere la
   migliore, oppure caricare un file audio registrato altrove.

   In alternativa una **voce generata** legge il copione e diventa una ripresa come
   le altre, quindi puoi alternarla alle tue registrazioni parte per parte:
   - **Kokoro**: gratuita, gira sul computer, nessuna quota. Due voci italiane
     (Sara, Nicola). Il modello (~120 MB) si scarica la prima volta in `modelli/kokoro/`.
     Serve circa 2,5 volte la durata dell'audio: un reel da un minuto richiede un
     paio di minuti.
   - **Gemini**: intonazione più naturale e istruzioni sul tono ("leggi con tono
     entusiasta"), ma ogni parte usa una richiesta della quota gratuita, che per i
     modelli vocali è limitata. I modelli sono in cascata come quelli di testo
     (Impostazioni → Modelli Gemini → Voce).

   Si genera una parte alla volta (con il testo modificabile, per correggere la
   pronuncia) o tutte le parti non ancora registrate. "Ascolta un esempio" fa sentire
   la voce scelta prima di generare. Se nel video c'è una voce generata, la pagina
   Pubblica ricorda di attivare l'etichetta **"Info sull'AI"** su Instagram, e la
   voce compare in `crediti.txt`.
3. **Visivi**. Una libreria per reel con immagini e video:
   - caricati da te;
   - cercati su **NASA Image and Video Library** e **Wikimedia Commons** (gratuiti,
     senza chiave; autore e licenza vengono salvati per i crediti). All'apertura della
     pagina il campo di ricerca di ogni parte si compila da solo con la parola chiave
     che sintetizza di più il soggetto (1-3 parole in inglese, una sola chiamata
     Gemini) e parte la ricerca per la parte selezionata;
   - scaricati da **YouTube** con yt-dlp (solo la traccia video, fino a 1080p).
   Poi assegni un visivo a ogni parte: dalla scheda della parte, **"Cambia visivo"**
   apre lì dentro la ricerca online (già compilata), la libreria e il caricamento di un
   file; in alternativa si trascina un'immagine o un video direttamente sulla scheda, e
   sostituisce il visivo di quella parte. Con **"Continua il visivo della parte
   precedente"** più parti condividono lo stesso visivo senza tagli (es. un video
   che scorre sotto due scene). Per i video scegli con un cursore il **tratto** da
   usare, lungo esattamente quanto la voce di quelle parti. Le immagini hanno sempre
   uno **zoom lento e costante**. I visivi orizzontali di default restano interi, con
   ai lati una versione sfocata di sé stessi invece delle bande nere; con **"Riempi lo
   schermo"** vengono invece ingranditi a tutto schermo tagliando i lati, e il cursore
   **Inquadratura** sceglie quale porzione tenere (anteprima verticale dal vivo nella
   pagina, avviso se la risoluzione è troppo bassa per l'ingrandimento).
4. **Montaggio** (in background, con barra di avanzamento):
   - la voce viene trascritta in locale con **Whisper** per i **sottotitoli animati**
     parola per parola (il copione aiuta Whisper con i nomi propri): testo bianco
     senza contorno e un rettangolino rosso che segue la parola pronunciata. Il
     rettangolo è una forma vettoriale ASS posizionata con le metriche del font (libass
     dimensiona il font sull'altezza winAscent+winDescent, non sull'em). Una
     sfumatura scura sul terzo inferiore e in alto tiene leggibili testo e logo anche su
     riprese chiare;
   - durante l'hook il testo appare come titolo grande;
   - logo in overlay e musica di sottofondo facoltativa, che si abbassa da sola
     quando parli. La musica si cerca direttamente nell'app su **Openverse** (brani
     Creative Commons, soprattutto da Jamendo, senza chiave), con il genere già
     suggerito da Gemini; si ascolta e si sceglie con un clic, e titolo, autore e
     licenza finiscono nei crediti. Di default si escludono le licenze ND, che
     vietano le modifiche (il montaggio taglia e abbassa il brano). La libreria audio
     di YouTube non è integrabile: non ha un'API e si usa solo da YouTube Studio con il
     proprio account;
   - volume normalizzato a -14 LUFS.
   C'è un'**anteprima veloce** a 540p e il **video finale** 1080×1920 H.264.
5. **Pubblica**:
   - prompt Nano Banana per la **copertina** (stesso stile fisso dei caroselli),
     oppure un fotogramma del video;
   - titolo con `**parola**` evidenziata come nelle slide (bianco su rettangolino rosso). Titolo e logo stanno nella fascia visibile
     anche nella griglia del profilo;
   - **caption** con i crediti di immagini e video in fondo;
   - ZIP con video, copertina, caption, copione e crediti completi.

La pubblicazione su Instagram resta manuale.

**Impostazioni → Reel**:
- modello Whisper (`small` consigliato; si scarica una volta in `modelli/whisper/`);
- versione di yt-dlp installata e ultima disponibile, con "Aggiorna ora";
- download anticipato del modello della voce gratuita Kokoro;
- aggiornamento automatico di yt-dlp: al massimo un controllo al giorno, e
  aggiornamento con nuovo tentativo quando un download fallisce;
- durata massima dei video YouTube.

yt-dlp gira sempre come processo separato, quindi un aggiornamento ha effetto
senza riavviare l'app.

Note:
- ffmpeg arriva dal pacchetto `imageio-ffmpeg`, niente da installare a mano.
- Usa solo video e musica che hai il diritto di riutilizzare: i crediti automatici
  non sostituiscono il permesso dell'autore.
- I lavori in background vivono nel processo dell'app: se l'app si riavvia durante
  un montaggio, il montaggio va rilanciato.

## CLI (alternativa da terminale)

### 1. Analisi testo + generazione prompt immagine

```bash
python -m src.main genera-prompt --input testo_ricerca.txt --nome nome_carosello
```

Analizza il testo con Gemini, decide il numero di slide (5-10, configurabile in
`config.yaml`), applica lo schema narrativo a 6 fasi e scrive:

- `prompts/<nome_carosello>/slides.json` — copy strutturato a segmenti + prompt immagine
- `prompts/<nome_carosello>/prompts_immagini.txt` — prompt leggibili da copiare in Nano Banana

### 2. Generazione manuale delle immagini (pausa umana)

Genera con Nano Banana le immagini di sfondo (senza testo) usando i prompt del
file `.txt`, e salvale come:

```
input_immagini/<nome_carosello>/01.png
input_immagini/<nome_carosello>/02.png
...
```

### 3. Composizione + QA

```bash
python -m src.main componi --nome nome_carosello
```

Valida che tutte le immagini richieste siano presenti, compone ogni slide con
Pillow (sfondo + testo + logo), valuta ogni slide con Gemini vision e, se il
punteggio medio è sotto soglia (default 7/10, in `config.yaml`), chiede
interattivamente se ricaricare l'immagine o procedere comunque.

Output finale in `output/<nome_carosello>/01.png, 02.png, ...` pronto per
l'upload manuale, con log dei punteggi in `output/<nome_carosello>/qa_log.json`.

## Decisioni di implementazione

- **Cascata di modelli**: `config.modelli_testo`/`modelli_vision` sono liste, non
  singole stringhe — `src/gemini_client.py` prova tutti i modelli di un giro prima
  di aspettare in backoff (aspettare su un modello sovraccarico quando ce n'è un
  altro pronto è tempo sprecato). Anche un errore 404 (modello non trovato/rimosso)
  fa passare al successivo invece di bloccare tutto: solo così un tipo nel nome di
  un modello aggiunto a mano non rompe l'intera cascata. `src/config.py` riscrive
  `config.yaml` con una sostituzione mirata della singola riga, non un dump YAML
  completo, per non perdere i commenti del file.
- **Font**: Space Grotesk (variabile, un solo file, pesi Regular/Bold applicati
  a runtime via variation axes) — vedi motivazione discussa in fase di design.
- **Evidenziazione parole**: il copy non usa marcatori nel testo ma una lista
  ordinata di segmenti `{testo, evidenziato}`, per evitare ambiguità di matching
  su parole ripetute.
- **Prompt Nano Banana**: il Blocco di Stile Fisso (Sezione 3 del design
  system) è una costante Python (`src/image_prompts.py`), mai generata o
  riparafrasata da Gemini. La riga "Composition" invece è dinamica per slide —
  il design system la prevede esplicitamente variabile — e include quanta
  percentuale del frame lasciare vuota, calcolata da `_percentuale_margine_vuoto`
  in base alla quantità di testo di quella slide (stessa logica di wrapping
  usata poi da Pillow, condivisa in `src/text_metrics.py`).
- **Formato di generazione**: il prompt chiede a Nano Banana il formato finale
  4:5 (1080x1350) direttamente, non più 9:16 (scelta iniziale del template poi
  rivista: generare già nel formato giusto evita di sprecare pixel col crop).
  Il cover-crop centrato in fase di composizione resta comunque come rete di
  sicurezza per qualunque proporzione lo sfondo generato abbia davvero.
- **Posizionamento testo content-aware**: prima di disegnare il testo, lo
  sfondo generato viene analizzato con Gemini vision (`src/layout_vision.py`)
  per individuare le zone realmente libere dall'illustrazione (Nano Banana non
  rispetta sempre alla lettera il margine richiesto). Il testo viene
  posizionato lì, allineato a sinistra; se non ci sta in una sola zona viene
  diviso tra le due zone migliori individuate. Se l'analisi fallisce, si
  ricade su una fascia superiore di default con un velo di sicurezza più
  marcato. In ogni caso resta anche un velo navy leggero e sfumato dietro il
  testo come rete di sicurezza contro imperfezioni residue.
- **Slide Hook**: usa una scala di font più grande delle altre (pensata per un
  titolo breve, non un paragrafo — vedi istruzioni a Gemini in
  `src/text_analysis.py`), ma la stessa logica di posizionamento content-aware
  delle altre slide.
- **Caption**: generata da un prompt Gemini separato (`src/caption.py`), sullo
  stesso testo grezzo della ricerca ma con istruzioni diverse (testo esteso e
  discorsivo, non il copy breve delle slide). Il testo grezzo viene salvato in
  `prompts/<nome_carosello>/testo_originale.txt` alla creazione del carosello,
  cosa che permette di generare/rigenerare la caption in un secondo momento
  senza incollarlo di nuovo — i caroselli creati prima di questa funzione non
  hanno quel file, quindi non possono generare la caption (l'interfaccia lo
  segnala invece di fallire). La firma dell'autore è assemblata in Python, non
  chiesta a Gemini, così si può cambiare senza rigenerare il testo.
