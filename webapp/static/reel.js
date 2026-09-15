// Helper condivisi dalle pagine di produzione del reel (e dalle impostazioni).

async function chiamaApi(url, { metodo = "POST", dati = null } = {}) {
  let corpo = null;
  if (dati instanceof FormData) {
    corpo = dati;
  } else if (dati) {
    corpo = new FormData();
    Object.entries(dati).forEach(([k, v]) => corpo.append(k, v));
  }
  let resp;
  try {
    resp = await fetch(url, { method: metodo, body: corpo });
  } catch (e) {
    return { ok: false, messaggio: "Errore di rete: il server non risponde." };
  }
  try {
    return await resp.json();
  } catch (e) {
    return { ok: false, messaggio: `Risposta inattesa dal server (HTTP ${resp.status}).` };
  }
}

// Interroga lo stato di un lavoro in background finché non finisce.
// `suAggiornamento(lavoro)` riceve { stato, fase, avanzamento, messaggio }.
function seguiLavoro(chiave, suAggiornamento, intervallo = 1000) {
  return new Promise((risolvi) => {
    const controlla = async () => {
      let dati;
      try {
        const resp = await fetch(`/api/lavoro?chiave=${encodeURIComponent(chiave)}`);
        dati = await resp.json();
      } catch (e) {
        setTimeout(controlla, intervallo * 2); // il server può essere in riavvio
        return;
      }
      const lavoro = dati.lavoro;
      if (!lavoro) {
        risolvi({ stato: "errore", messaggio: "Operazione interrotta (il server è stato riavviato?)." });
        return;
      }
      suAggiornamento(lavoro);
      if (lavoro.stato === "in_corso") {
        setTimeout(controlla, intervallo);
      } else {
        risolvi(lavoro);
      }
    };
    controlla();
  });
}

// Ricarica la pagina tornando allo stesso punto di scorrimento.
function ricaricaMantenendoPosizione() {
  try {
    sessionStorage.setItem(`scroll:${location.pathname}`, String(window.scrollY));
  } catch (e) {}
  location.reload();
}

let posizioneRimandata = null;

function ripristinaPosizione() {
  try {
    const chiave = `scroll:${location.pathname}`;
    const y = sessionStorage.getItem(chiave);
    if (y !== null) {
      sessionStorage.removeItem(chiave);
      posizioneRimandata = Number(y);
      window.scrollTo(0, posizioneRimandata);
    }
  } catch (e) {}
}

// Per contenuti caricati dopo l'apertura (es. risultati di ricerca): se la pagina
// era troppo corta per tornare al punto giusto, ci riprova una volta.
function ripristinaPosizioneRimandata() {
  if (posizioneRimandata !== null) {
    window.scrollTo(0, posizioneRimandata);
    posizioneRimandata = null;
  }
}

function formattaSecondi(secondi) {
  const s = Math.max(0, secondi);
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

async function copiaTesto(bottone, testo) {
  await navigator.clipboard.writeText(testo);
  const originale = bottone.textContent;
  bottone.textContent = "Copiato!";
  setTimeout(() => (bottone.textContent = originale), 1500);
}

document.addEventListener("DOMContentLoaded", ripristinaPosizione);
