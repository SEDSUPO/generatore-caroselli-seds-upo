@echo off
rem Tutto in un unico blocco tra parentesi: cmd lo legge per intero prima di eseguirlo.
rem Serve perche' l'aggiornamento sostituisce anche questo file mentre e' in uso.
(
    cd /d "%~dp0"
    title Aggiornamento Generatore Caroselli SEDS UPO
    if exist "python\python.exe" (
        "python\python.exe" aggiorna_app.py %*
    ) else if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" aggiorna_app.py %*
    ) else (
        python aggiorna_app.py %*
    )
    echo.
    pause
    exit /b
)
