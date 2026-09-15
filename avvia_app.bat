@echo off
cd /d "%~dp0"
title Generatore Caroselli SEDS UPO

rem Python dell'app, in ordine: pacchetto pronto all'uso (python\), installazione con
rem installa.bat (.venv), Python di sistema. Niente blocchi tra parentesi con codice
rem Python dentro: le sue parentesi li chiuderebbero.
set "PY="
if exist "python\python.exe" set "PY=python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PY goto :python_pronto
python -c "import flask" >nul 2>nul
if errorlevel 1 goto :non_installata
set "PY=python"

:python_pronto
rem Windows non carica le librerie con percorsi oltre circa 260 caratteri.
"%PY%" -c "import os, sys; sys.exit(1 if len(os.getcwd()) > 140 else 0)"
if errorlevel 1 goto :percorso_lungo

echo ============================================================
echo   Generatore Caroselli SEDS UPO
echo ============================================================
echo.
echo L'app si apre nel browser all'indirizzo  http://127.0.0.1:5000
echo Se non si apre da sola, copia l'indirizzo nel browser.
echo.
echo Lascia aperta questa finestra mentre usi l'app:
echo chiuderla spegne l'app.
echo.
"%PY%" run_app.py
rem Uscita normale (es. chiusura per aggiornamento): la finestra si chiude da sola.
if errorlevel 1 pause
exit /b

:non_installata
echo L'app non e' ancora installata.
echo Scarica il pacchetto pronto all'uso da GitHub, oppure fai doppio clic
echo su installa.bat. Istruzioni nel file LEGGIMI.txt.
echo.
pause
exit /b 1

:percorso_lungo
echo ERRORE: la cartella dell'app si trova in un percorso troppo lungo:
echo   %CD%
echo Spostala in un posto piu' corto, per esempio in Documenti o in C:\
echo.
pause
exit /b 1
