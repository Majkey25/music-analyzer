@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv is missing. Install it at https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo FFmpeg is missing from PATH. Install it at https://ffmpeg.org/download.html
    pause
    exit /b 1
)
uv run --locked --no-dev --extra separation python -X utf8 .
if errorlevel 1 (
    echo Music Analyzer exited with an error. See the details above.
    pause
    exit /b 1
)
