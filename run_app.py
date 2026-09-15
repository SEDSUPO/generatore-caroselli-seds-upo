"""Avvia l'app web locale e apre automaticamente il browser."""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

from webapp.app import app

HOST = "127.0.0.1"
PORT = int(os.environ.get("GENERATORE_PORTA", "5000"))  # altra porta: utile per provare due copie insieme


def _apri_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


def _ignora_variabili_werkzeug_scadute() -> None:
    """Quando l'app si riavvia dopo un aggiornamento, il nuovo processo può ereditare
    le variabili interne del riavvio automatico di Werkzeug dall'app appena chiusa:
    WERKZEUG_SERVER_FD indica una connessione che non esiste più, e l'avvio fallirebbe
    con "WinError 10038". Una connessione valida si lascia (è il normale riavvio
    automatico), una scaduta si scarta e l'app parte da zero."""
    descrittore = os.environ.get("WERKZEUG_SERVER_FD")
    if descrittore is None:
        return
    try:
        socket.fromfd(int(descrittore), socket.AF_INET, socket.SOCK_STREAM).close()
    except (OSError, ValueError):
        os.environ.pop("WERKZEUG_SERVER_FD", None)
        os.environ.pop("WERKZEUG_RUN_MAIN", None)


if __name__ == "__main__":
    _ignora_variabili_werkzeug_scadute()
    # use_reloader=True: riavvia da solo il processo quando un file .py cambia (Flask
    # in debug=False mette anche in cache i template .html, risolto separatamente in
    # webapp/app.py con TEMPLATES_AUTO_RELOAD) — niente più "chiudi e rilancia a mano".
    #
    # Il controllo su WERKZEUG_RUN_MAIN evita di aprire il browser due volte: con il
    # reloader attivo questo script viene eseguito sia dal processo "supervisore" (che
    # non serve mai richieste, si limita a monitorare e rilanciare) sia dal processo
    # figlio che serve davvero — solo quest'ultimo ha la variabile impostata a "true".
    # NON_APRIRE_BROWSER: riavvio dopo un aggiornamento, la pagina è già aperta e si ricarica da sola.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" and not os.environ.get("NON_APRIRE_BROWSER"):
        threading.Timer(1.0, _apri_browser).start()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=True)
