@echo off
setlocal
set "ESITO=0"
cd /d "%~dp0"
title Installazione Generatore Caroselli SEDS UPO

echo ============================================================
echo   Generatore Caroselli SEDS UPO - installazione
echo ============================================================
echo.
echo Questa procedura va fatta una volta sola. Scarica le librerie
echo necessarie (circa 1 GB): servono da 5 a 15 minuti e una
echo connessione a internet. Non chiudere questa finestra.
echo.

if not exist "python\python.exe" goto :cerca_python
echo Questo e' il pacchetto pronto all'uso: Python e librerie sono gia' inclusi,
echo non c'e' niente da installare. Avvia l'app con  avvia_app.bat
goto :fine

:cerca_python
rem --- 1. Cerca Python 3.11 o piu' recente -----------------------
rem Niente blocchi tra parentesi qui: le parentesi del codice Python li chiuderebbero.
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_trovato
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto :senza_python
set "ESITO=1"

:python_trovato
for /f "delims=" %%v in ('%PY% --version') do set "VERSIONE=%%v"
echo [1/3] Trovato %VERSIONE%

rem Windows non carica le librerie con percorsi oltre circa 260 caratteri, e quelle
rem installate stanno parecchie cartelle piu' in basso di questa.
%PY% -c "import os, sys; sys.exit(1 if len(os.getcwd()) > 140 else 0)"
if errorlevel 1 goto :percorso_lungo

rem --- 2. Ambiente separato per le librerie ------------------------
if exist ".venv\Scripts\python.exe" (
    echo [2/3] Ambiente gia' presente, lo aggiorno.
) else (
    echo [2/3] Creo l'ambiente per le librerie...
    %PY% -m venv .venv
    if errorlevel 1 goto :errore
)

rem --- 3. Librerie ------------------------------------------------
echo [3/3] Installo le librerie, attendi...
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
if errorlevel 1 goto :errore
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :errore
".venv\Scripts\python.exe" -c "import flask, google.genai, faster_whisper, kokoro_onnx, imageio_ffmpeg, yt_dlp"
if errorlevel 1 goto :errore

echo.
echo ============================================================
echo   Installazione completata.
echo   Per usare l'app fai doppio clic su  avvia_app.bat
echo ============================================================
goto :fine

:senza_python
echo ERRORE: Python non e' installato, oppure e' troppo vecchio.
echo.
echo  1. Scaricalo da  https://www.python.org/downloads/
echo  2. Durante l'installazione spunta la casella
echo     "Add python.exe to PATH"  (in basso nella prima schermata)
echo  3. Chiudi questa finestra e rifai doppio clic su installa.bat
echo.
echo Istruzioni complete nel file LEGGIMI.txt
goto :fine

:percorso_lungo
echo.
echo ERRORE: la cartella dell'app si trova in un percorso troppo lungo,
echo e Windows non riuscirebbe a caricare alcune librerie:
echo   %CD%
echo.
echo Sposta la cartella "Generatore_caroselli_SEDS_UPO" in un posto piu' corto,
echo per esempio in Documenti o direttamente in C:\, poi rifai doppio clic
echo su installa.bat.
set "ESITO=1"
goto :fine

:errore
set "ESITO=1"
echo.
echo ERRORE durante l'installazione: leggi i messaggi qui sopra.
echo Le cause piu' comuni sono la connessione a internet assente
echo o un antivirus che blocca il download. Riprova rifacendo
echo doppio clic su installa.bat: riparte da dove si era fermato.
echo Altre soluzioni nel file LEGGIMI.txt, sezione "Problemi".

:fine
echo.
if /i not "%~1"=="--senza-pausa" pause
exit /b %ESITO%
