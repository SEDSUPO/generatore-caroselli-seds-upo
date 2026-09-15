@echo off
cd /d "%~dp0"
title Pubblica una nuova versione su GitHub
python strumenti\pubblica_versione.py %*
echo.
pause
