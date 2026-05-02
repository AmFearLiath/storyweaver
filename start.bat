@echo off
chcp 65001 >nul
title Storyweaver — KI Game Master

echo ============================================================
echo   Storyweaver — KI Game Master Setup
echo ============================================================
echo.

:: Python prüfen
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden. Bitte Python 3.10+ installieren.
    pause
    exit /b 1
)

echo [1/3] Installiere Python-Abhängigkeiten...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [FEHLER] Installation fehlgeschlagen.
    pause
    exit /b 1
)

echo [2/3] Prüfe Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNUNG] Ollama nicht erreichbar!
    echo.
    echo   Starte Ollama in einem anderen Fenster:
    echo     ollama serve
    echo.
    echo   Danach ein Modell laden (falls nicht vorhanden):
    echo     ollama pull llama3
    echo.
    echo   Drücke eine Taste wenn Ollama läuft...
    pause >nul
)

echo [3/3] Starte Adventure Server...
echo.
echo   Browser öffnen: http://localhost:8000
echo.
start "" "http://localhost:8000"
python run.py
pause
