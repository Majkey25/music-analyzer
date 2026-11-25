@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Chybi uv. Instalace: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo Chybi FFmpeg v PATH. Instalace: https://ffmpeg.org/download.html
    pause
    exit /b 1
)
uv run --locked --no-dev --extra separation python -X utf8 .
if errorlevel 1 (
    echo Aplikace se neukoncila spravne. Podrobnosti jsou vyse.
    pause
    exit /b 1
)
