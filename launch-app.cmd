@echo off
setlocal
cd /d "%~dp0"
if exist "release\FacebookMediaDownloader\FacebookMediaDownloader.exe" (
    start "" "%~dp0release\FacebookMediaDownloader\FacebookMediaDownloader.exe"
    exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
    echo Set up the Python environment using the commands in README.md first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B app.py
if errorlevel 1 pause
